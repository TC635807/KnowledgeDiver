"""卡片 CRUD 路由。

提供卡片的增删改查、语义搜索及链接关系管理接口。
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)
from fastapi import APIRouter, HTTPException, Depends
from typing import List, Optional
from datetime import datetime
import uuid

from pydantic import BaseModel

from backend.models import Card
from backend.storage import SqliteCardStore
from backend.routes.auth import get_current_user
from backend.models.user import User
from backend.links.manager import LinkManager
from backend.ai.embedder import Embedder
from backend.config import DEFAULT_SESSION_ID

router = APIRouter()
_logger = logging.getLogger(__name__)


class CardCreateRequest(BaseModel):
    """创建卡片请求体。"""
    title: str = "新卡片"
    content: str = ""
    parent_id: Optional[str] = None


class CardUpdateRequest(BaseModel):
    """更新卡片请求体。"""
    title: Optional[str] = None
    content: Optional[str] = None
    metadata: Optional[dict] = None
    links: Optional[List[str]] = None  # 前链（出链）：当前卡片指向的其他卡片 ID 列表


@router.get("/api/cards")
async def list_cards(
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user)
) -> List[dict]:
    """列出指定会话中的所有卡片。"""
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    cards = store.list_cards()
    return [card.model_dump(mode="json") for card in cards]


@router.get("/api/cards/search")
async def search_cards(
    query: str,
    session_id: str = DEFAULT_SESSION_ID,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
):
    """语义搜索当前会话中的卡片。"""
    start = time.perf_counter()
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    embedder = Embedder.get()

    try:
        embedding = await embedder.encode_one(query)
    except Exception as e:
        _logger.warning("查询编码失败 query=%r: %s", query, e)
        return {"results": [], "fallback": True}

    results = store.vector_search(embedding, limit, 2.0)
    cards = []
    for cid, dist in results:
        card = store.read_card(cid)
        if card:
            cards.append({"card": card.model_dump(mode="json"), "score": round(1 - dist, 4)})

    elapsed = time.perf_counter() - start
    _logger.info(
        "语义搜索 query=%r session=%s → %d 条结果, 耗时 %.1fms",
        query, session_id, len(cards), elapsed * 1000,
    )

    if not cards:
        return {"results": [], "fallback": True}

    return {"results": cards}


@router.post("/api/cards/reindex")
async def reindex_cards(
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
):
    """为当前会话的所有卡片重建向量索引。"""
    _logger.info("重建索引请求 session=%s user=%s", session_id, current_user.username)
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    count = store.reindex_all()
    _logger.info("重建索引完成: %d 张卡片", count)
    return {"indexed": count}


@router.get("/api/cards/{card_id}")
async def get_card(
    card_id: str,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user)
) -> dict:
    """获取单张卡片详情。"""
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    card = store.read_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    return card.model_dump(mode="json")


@router.get("/api/cards/{card_id}/raw")
async def get_card_raw(
    card_id: str,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
) -> dict:
    """获取卡片来源的原始网页全文（供前端「原文全文」折叠区查看）。

    卡片 sources 字段记录了来源 URL，raw_pages 表保存了抓取时的完整正文。
    """
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    card = store.read_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found")
    if not card.sources:
        return {"sources": []}
    from backend.storage.raw_store import RawPageStore

    raw_store = RawPageStore(username=current_user.username, session_id=session_id)
    sources = []
    for url in card.sources:
        row = raw_store.get(url)
        if row and row.get("content"):
            sources.append({
                "url": url,
                "title": row.get("title", "") or "",
                "content": row["content"],
                "fetched_at": row.get("fetched_at", "") or "",
            })
    return {"sources": sources}


@router.post("/api/cards")
async def create_card(
    card_data: CardCreateRequest,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user)
) -> dict:
    """创建新卡片，可选关联父卡片建立链接。"""
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    now = datetime.utcnow()
    card = Card(
        id=str(uuid.uuid4()),
        title=card_data.title,
        content=card_data.content,
        metadata={},
        links=[],
        backlinks=[],
        created_at=now,
        updated_at=now,
        sources=[],
        confidence=0.5,
        tags=[],
    )
    store.create_card(card)

    if card_data.parent_id:
        try:
            link_manager = LinkManager(store)
            await link_manager.create_link(card_data.parent_id, card.id, link_type="parent")
        except Exception as e:
            logger.warning("Failed to create parent link: %s", e)

    return card.model_dump(mode="json")


@router.put("/api/cards/{card_id}")
async def update_card(
    card_id: str,
    card_data: CardUpdateRequest,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user)
) -> dict:
    """更新卡片标题/内容/元数据，以及通过 LinkManager 同步链接变更。"""
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    existing = store.read_card(card_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Card not found")

    if card_data.title is not None:
        existing.title = card_data.title
    if card_data.content is not None:
        existing.content = card_data.content
    if card_data.metadata is not None:
        existing.metadata = card_data.metadata
    existing.updated_at = datetime.utcnow()

    # Save title/content/metadata changes before processing links
    store.update_card(existing)

    # Process link changes via LinkManager to maintain links↔backlinks symmetry
    if card_data.links is not None:
        old_links = set(existing.links or [])
        new_links = set(card_data.links)
        to_add = new_links - old_links
        to_remove = old_links - new_links
        link_manager = LinkManager(store)
        for target_id in to_remove:
            await link_manager.remove_link(card_id, target_id)
        for target_id in to_add:
            await link_manager.create_link(card_id, target_id)
        # Re-read after LinkManager modifies and saves the card
        existing = store.read_card(card_id)

    return existing.model_dump(mode="json")


@router.delete("/api/cards/{card_id}")
async def delete_card(
    card_id: str,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user)
) -> dict:
    """删除指定卡片。"""
    store = SqliteCardStore(username=current_user.username, session_id=session_id)
    success = store.delete_card(card_id)
    if not success:
        raise HTTPException(status_code=404, detail="Card not found")
    return {"status": "deleted", "id": card_id}

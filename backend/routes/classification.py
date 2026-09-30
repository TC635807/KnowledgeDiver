"""卡片分类路由。

提供 AI 分类建议、手动重分类、树结构查询接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.storage import CardStore
from backend.ai import AIProvider
from backend.classification import CardClassifier
from backend.models import Card  # type: ignore
from pydantic import BaseModel


router = APIRouter(prefix="/api/classification", tags=["classification"])


class CardRef(BaseModel):
    """卡片引用信息。"""
    id: str
    title: str


class SuggestRequest(BaseModel):
    """分类建议请求体。"""
    card_id: str


@router.post("/suggest")
async def suggest(card_ref: SuggestRequest, store: CardStore = Depends(lambda: None), ai: AIProvider = Depends(lambda: None)):
    """获取 AI 建议的父卡片（即该卡片应放入哪个分类下）。"""
    classifier = CardClassifier(store, ai)
    card = store.get_card(card_ref.card_id)  # type: ignore
    suggestion = await classifier.suggest_parent(card)  # type: ignore
    return suggestion.dict()


@router.post("/apply")
async def apply(card_ref: SuggestRequest, store: CardStore = Depends(lambda: None), ai: AIProvider = Depends(lambda: None)):
    """获取 AI 建议并自动应用到卡片分类。"""
    classifier = CardClassifier(store, ai)
    card = store.get_card(card_ref.card_id)  # type: ignore
    suggestion = await classifier.suggest_parent(card)  # type: ignore
    if suggestion.suggested_parent_id:
        await store.update_parent(card.id, suggestion.suggested_parent_id)  # type: ignore
    return suggestion.dict()


class ReclassifyRequest(BaseModel):
    card_id: str
    new_parent_id: str | None


@router.post("/reclassify")
async def reclassify(req: ReclassifyRequest, store: CardStore = Depends(lambda: None), ai: AIProvider = Depends(lambda: None)):
    """手动将卡片移到指定父卡片下。"""
    classifier = CardClassifier(store, ai)
    updated = await classifier.reclassify(req.card_id, req.new_parent_id)  # type: ignore
    return {"id": updated.id, "parent_id": updated.parent_id}


@router.get("/tree")
async def tree(root_id: str | None = None, store: CardStore = Depends(lambda: None), ai: AIProvider = Depends(lambda: None)):
    """获取分类树（可选指定根节点）。"""
    classifier = CardClassifier(store, ai)
    return await classifier.get_tree(root_id)


@router.get("/tree/{card_id}")
async def tree_sub(card_id: str, store: CardStore = Depends(lambda: None), ai: AIProvider = Depends(lambda: None)):
    """获取指定卡片下的子树。"""
    classifier = CardClassifier(store, ai)
    return await classifier.get_tree(card_id)


@router.get("/children/{card_id}")
async def children(card_id: str, store: CardStore = Depends(lambda: None), ai: AIProvider = Depends(lambda: None)):
    """获取指定卡片的直接子卡片列表。"""
    classifier = CardClassifier(store, ai)
    return [c for c in await classifier.get_children(card_id)]


@router.get("/ancestors/{card_id}")
async def ancestors(card_id: str, store: CardStore = Depends(lambda: None), ai: AIProvider = Depends(lambda: None)):
    """获取指定卡片的所有祖先卡片链。"""
    classifier = CardClassifier(store, ai)
    return await classifier.get_ancestors(card_id)

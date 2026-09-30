"""卡片链接路由。

提供创建链接、删除链接、查询出链/入链和链接校验接口。
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List

from backend.links import LinkManager
from backend.storage import SqliteCardStore
from backend.models.user import User
from backend.routes.auth import get_current_user
from backend.config import DEFAULT_SESSION_ID

router = APIRouter()


class LinkRequest(BaseModel):
    """创建链接请求体。"""
    from_card_id: str
    to_card_id: str
    link_type: str = "reference"


class UnlinkRequest(BaseModel):
    """删除链接请求体。"""
    from_card_id: str
    to_card_id: str
    link_type: str = "reference"


def _get_manager(username: str, session_id: str = DEFAULT_SESSION_ID) -> LinkManager:
    store = SqliteCardStore(username=username, session_id=session_id)
    return LinkManager(store)


@router.post("/api/links")
async def create_link(
    req: LinkRequest,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
):
    """在两个卡片之间创建链接，同步维护出链和入链。"""
    manager = _get_manager(current_user.username, session_id)
    ok, warnings = await manager.create_link(req.from_card_id, req.to_card_id, req.link_type)
    if not ok:
        raise HTTPException(status_code=400, detail="Unable to create link")
    return {"success": ok, "warnings": [w.dict() for w in warnings]}


@router.delete("/api/links")
async def delete_link(
    req: UnlinkRequest,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
):
    """删除两个卡片之间的链接。"""
    manager = _get_manager(current_user.username, session_id)
    ok = await manager.remove_link(req.from_card_id, req.to_card_id, req.link_type)
    if not ok:
        raise HTTPException(status_code=404, detail="Link not found")
    return {"success": ok}


@router.get("/api/links/{card_id}/outgoing")
async def outgoing_links(
    card_id: str,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
):
    """获取指定卡片的出链列表。"""
    manager = _get_manager(current_user.username, session_id)
    return {"outgoing": await manager.get_outgoing_links(card_id)}


@router.get("/api/links/{card_id}/incoming")
async def incoming_links(
    card_id: str,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
):
    """获取指定卡片的入链列表。"""
    manager = _get_manager(current_user.username, session_id)
    return {"incoming": await manager.get_incoming_links(card_id)}


@router.get("/api/links/{card_id}/validate")
async def validate_links(
    card_id: str,
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
):
    """校验指定卡片的链接一致性（检测悬挂链接等）。"""
    manager = _get_manager(current_user.username, session_id)
    return {"warnings": [w.dict() for w in await manager.validate_links(card_id)]}

"""会话分享路由。

提供创建分享令牌、吊销分享令牌和通过令牌访问分享内容的功能。
"""

import os

from fastapi import APIRouter, Depends, HTTPException, Request

from backend.models.user import User
from backend.routes.auth import get_current_user
from backend.share.manager import ShareManager
from backend.storage.sqlite_card_store import SqliteCardStore
from backend.storage.session_store import SessionStore

router = APIRouter()


def get_session_store(current_user: User = Depends(get_current_user)) -> SessionStore:
    """获取当前用户的会话存储仓库。"""
    return SessionStore(username=current_user.username)


def _frontend_url(request: Request) -> str:
    """获取前端 URL，用于生成分享链接。"""
    return os.getenv("FRONTEND_URL", "http://localhost:5173")


@router.post("/api/sessions/{session_id}/share")
async def share_session(
    session_id: str,
    request: Request,
    store: SessionStore = Depends(get_session_store),
) -> dict:
    """创建会话分享令牌并返回分享链接。"""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    token = ShareManager().create_token(store.username, session_id)
    share_url = f"{_frontend_url(request)}/?share={token}"

    return {
        "token": token,
        "share_url": share_url,
        "session_id": session_id,
    }


@router.delete("/api/sessions/{session_id}/share")
async def revoke_share(
    session_id: str,
    store: SessionStore = Depends(get_session_store),
) -> dict:
    """吊销指定会话的分享令牌。"""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    ShareManager().revoke_token(store.username, session_id)
    return {"status": "revoked"}


@router.get("/api/share/{token}")
async def view_shared_session(token: str) -> dict:
    """通过分享令牌查看共享会话的内容。"""
    share_info = ShareManager().validate_token(token)
    if share_info is None:
        raise HTTPException(status_code=404, detail="分享链接不存在或已失效")

    username = share_info["username"]
    session_id = share_info["session_id"]

    session_store = SessionStore(username=username)
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话已不存在")

    card_store = SqliteCardStore(username=username, session_id=session_id)
    cards = card_store.list_cards()

    return {
        "session": session.model_dump(mode="json"),
        "cards": [c.model_dump(mode="json") for c in cards],
    }

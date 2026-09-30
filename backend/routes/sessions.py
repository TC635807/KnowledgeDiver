"""会话管理路由。

提供会话的 CRUD 及跨会话卡片移动功能。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Depends
from typing import List

from backend.models.session import Session, SessionCreate, SessionUpdate
from backend.models.user import User
from backend.routes.auth import get_current_user
from backend.storage.session_store import SessionStore

router = APIRouter()


def get_session_store(current_user: User = Depends(get_current_user)) -> SessionStore:
    """Get session store for current user."""
    return SessionStore(username=current_user.username)


@router.get("/api/sessions", response_model=List[Session])
async def list_sessions(store: SessionStore = Depends(get_session_store)) -> List[Session]:
    """List all sessions for the current user."""
    return store.list_sessions()


@router.post("/api/sessions", response_model=Session)
async def create_session(
    session_data: SessionCreate,
    store: SessionStore = Depends(get_session_store)
) -> Session:
    """Create a new session."""
    try:
        return store.create_session(session_data.name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="创建会话失败")


@router.get("/api/sessions/{session_id}", response_model=Session)
async def get_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store)
) -> Session:
    """Get a specific session."""
    session = store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@router.put("/api/sessions/{session_id}", response_model=Session)
async def update_session(
    session_id: str,
    session_data: SessionUpdate,
    store: SessionStore = Depends(get_session_store)
) -> Session:
    """Update a session's name."""
    try:
        session = store.update_session(session_id, session_data.name)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return session
    except ValueError as e:
        raise HTTPException(status_code=400, detail="更新会话失败")


@router.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str,
    store: SessionStore = Depends(get_session_store)
) -> dict:
    """Delete a session."""
    try:
        success = store.delete_session(session_id)
        if not success:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"status": "deleted", "id": session_id}
    except ValueError as e:
        raise HTTPException(status_code=403, detail="删除会话失败")


@router.post("/api/sessions/{session_id}/move-cards")
async def move_cards_to_session(
    session_id: str,
    target_session_id: str,
    card_ids: List[str],
    store: SessionStore = Depends(get_session_store)
) -> dict:
    """将指定卡片从当前会话移动到目标会话。"""
    try:
        moved_count = store.move_cards_to_session(session_id, target_session_id, card_ids)
        return {
            "status": "moved",
            "moved_count": moved_count,
            "source_session_id": session_id,
            "target_session_id": target_session_id
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail="移动卡片失败")
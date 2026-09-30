"""Hub 社区路由。

提供公开浏览 Hub 会话、分享/取消分享工作区会话、
点赞/点踩/评论等社区功能。
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query

from backend.models.hub import (
    HubManifest,
    HubSessionSummary,
    HubSessionDetail,
    HubCommentCreate,
)
from backend.models.user import User
from backend.routes.auth import get_current_user
from backend.storage.hub_store import HubStore
from backend.storage.sqlite_card_store import SqliteCardStore
from backend.storage.session_store import SessionStore

router = APIRouter()


def get_hub_store() -> HubStore:
    """获取 Hub 存储仓库。"""
    return HubStore()


# ── 公开浏览（无需认证） ────────────────────────────────────────

@router.get("/api/hub", response_model=List[HubSessionSummary])
async def search_hub(
    query: str = Query("", description="Search query for name/description/topics"),
    sort_by: str = Query("newest", description="Sort: newest, most_likes, trending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    creator: str = Query("", description="Filter by creator username"),
    hub: HubStore = Depends(get_hub_store),
) -> List[HubSessionSummary]:
    return hub.list_sessions(query=query, sort_by=sort_by, page=page, page_size=page_size, creator=creator)


@router.get("/api/hub/{username}/{session_name}", response_model=HubSessionDetail)
async def get_hub_session(
    username: str,
    session_name: str,
    hub: HubStore = Depends(get_hub_store),
) -> HubSessionDetail:
    detail = hub.get_session_detail(username, session_name)
    if detail is None:
        raise HTTPException(status_code=404, detail="Hub session not found")
    return HubSessionDetail(**detail)


# ── 用户个人页 ────────────────────────────────────────────────────

@router.get("/api/hub/user/{username}/profile")
async def get_user_profile(
    username: str,
    hub: HubStore = Depends(get_hub_store),
) -> dict:
    """Return user's hub stats + most-liked sessions."""
    stats = hub.get_user_stats(username)
    popular = hub.get_user_sessions(username, sort_by="most_likes", page=1, page_size=5)
    return {
        **stats,
        "popular_sessions": [s.model_dump(mode="json") for s in popular],
    }


@router.get("/api/hub/user/{username}/sessions")
async def get_user_sessions(
    username: str,
    sort_by: str = Query("newest", description="Sort: newest, most_likes, trending"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    hub: HubStore = Depends(get_hub_store),
) -> List[HubSessionSummary]:
    """List sessions created by a specific user."""
    return hub.get_user_sessions(username, sort_by=sort_by, page=page, page_size=page_size)


# ── 认证操作 ──────────────────────────────────────────────────────

@router.post("/api/hub/share")
async def share_to_hub(
    body: dict,
    current_user: User = Depends(get_current_user),
    hub: HubStore = Depends(get_hub_store),
):
    """Share (or update) a workspace session to Hub."""
    session_id = body.get("session_id", "")
    description = body.get("description", "")
    topics = body.get("topics", [])

    if len(description) > 500:
        raise HTTPException(status_code=400, detail="简介不能超过500个字符")
    if not isinstance(topics, list) or len(topics) > 20:
        raise HTTPException(status_code=400, detail="主题标签不能超过20个")

    # read the session from workspace
    session_store = SessionStore(username=current_user.username)
    session = session_store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    session_name = body.get("session_name", session.name)
    # Validate session name to prevent path traversal (defense in depth)
    if not session_name or ".." in session_name or "/" in session_name or "\\" in session_name:
        raise HTTPException(status_code=400, detail="会话名称无效")

    # read all cards in this session
    card_store = SqliteCardStore(username=current_user.username, session_id=session_id)
    cards = card_store.list_cards()

    manifest = hub.share_session(
        username=current_user.username,
        session_name=session_name,
        cards=cards,
        description=description,
        topics=topics,
    )
    return {"status": "ok", "manifest": manifest.model_dump(mode="json")}


@router.delete("/api/hub/{username}/{session_name}")
async def unshare_from_hub(
    username: str,
    session_name: str,
    current_user: User = Depends(get_current_user),
    hub: HubStore = Depends(get_hub_store),
):
    """Unshare a session from Hub (only the creator can do this)."""
    if username != current_user.username:
        raise HTTPException(status_code=403, detail="You can only unshare your own sessions")
    if not hub.session_exists(username, session_name):
        raise HTTPException(status_code=404, detail="Hub session not found")
    hub.unshare_session(username, session_name)
    return {"status": "ok"}


@router.post("/api/hub/{username}/{session_name}/import")
async def import_from_hub(
    username: str,
    session_name: str,
    current_user: User = Depends(get_current_user),
    hub: HubStore = Depends(get_hub_store),
):
    """Import a Hub session into current user's workspace."""
    if not hub.session_exists(username, session_name):
        raise HTTPException(status_code=404, detail="Hub session not found")
    result = hub.import_session(username, session_name, current_user.username)
    if result is None:
        raise HTTPException(status_code=500, detail="Import failed")
    new_session_id = result
    return {"status": "ok", "session_id": new_session_id}


@router.post("/api/hub/{username}/{session_name}/like")
async def like_hub_session(
    username: str,
    session_name: str,
    current_user: User = Depends(get_current_user),
    hub: HubStore = Depends(get_hub_store),
):
    manifest = hub.like_session(username, session_name, voter=current_user.username)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Hub session not found")
    return {"status": "ok", "likes": manifest.likes, "dislikes": manifest.dislikes}


@router.post("/api/hub/{username}/{session_name}/dislike")
async def dislike_hub_session(
    username: str,
    session_name: str,
    current_user: User = Depends(get_current_user),
    hub: HubStore = Depends(get_hub_store),
):
    manifest = hub.dislike_session(username, session_name, voter=current_user.username)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Hub session not found")
    return {"status": "ok", "likes": manifest.likes, "dislikes": manifest.dislikes}


@router.post("/api/hub/{username}/{session_name}/comment")
async def add_hub_comment(
    username: str,
    session_name: str,
    body: HubCommentCreate,
    current_user: User = Depends(get_current_user),
    hub: HubStore = Depends(get_hub_store),
):
    manifest = hub.add_comment(username, session_name, current_user.username, body.content)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Hub session not found")
    return {"status": "ok", "comments": manifest.model_dump(mode="json")["comments"]}


@router.delete("/api/hub/{username}/{session_name}/comment/{index}")
async def delete_hub_comment(
    username: str,
    session_name: str,
    index: int,
    current_user: User = Depends(get_current_user),
    hub: HubStore = Depends(get_hub_store),
):
    try:
        manifest = hub.delete_comment(username, session_name, index, author=current_user.username)
    except PermissionError:
        raise HTTPException(status_code=403, detail="You can only delete your own comments")
    except IndexError:
        raise HTTPException(status_code=404, detail="Comment not found")
    if manifest is None:
        raise HTTPException(status_code=404, detail="Hub session not found")
    return {"status": "ok", "comments": manifest.model_dump(mode="json")["comments"]}

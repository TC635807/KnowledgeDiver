"""Agent 路由 — SSE 流式聊天 + 上下文清空 + 历史加载。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.models.user import User
from backend.routes.auth import get_current_user
from backend.agent.schemas import AgentChatRequest

router = APIRouter()


@router.get("/api/agent/history")
async def agent_history(
    session_id: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    from backend.agent.context import AgentContext

    ctx = AgentContext.get()
    session = ctx.get_or_create(current_user.username, session_id)
    return {"messages": session.to_llm_messages()}


@router.post("/api/agent/chat")
async def agent_chat(
    body: AgentChatRequest,
    current_user: User = Depends(get_current_user),
):
    from backend.agent.bridge import loop_bridge
    from backend.agent.loop import run_agent_loop
    from backend.agent.manager import AgentLoopManager

    key = f"{current_user.username}:{body.session_id}"
    mgr = AgentLoopManager.get_instance()

    if mgr.is_running(key):
        # 循环在后台运行：注入消息，返回订阅流（刷新/断连不取消循环）
        loop_bridge.send(key, body.message)
        return StreamingResponse(
            mgr.subscribe(key),
            media_type="text/event-stream",
            headers={
                "X-Accel-Buffering": "no-buffering",
                "Cache-Control": "no-cache",
            },
        )

    mgr.start(
        key,
        run_agent_loop(
            username=current_user.username,
            session_id=body.session_id,
            user_message=body.message,
        ),
    )
    return StreamingResponse(
        mgr.subscribe(key),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no-buffering",
            "Cache-Control": "no-cache",
        },
    )


@router.get("/api/agent/stream")
async def agent_stream(
    session_id: str = Query(...),
    current_user: User = Depends(get_current_user),
):
    """订阅当前运行中的 Agent 循环事件流（刷新页面后重连用）。

    无运行循环时返回 error 事件；断开订阅不影响循环继续后台运行。
    """
    from backend.agent.manager import AgentLoopManager

    key = f"{current_user.username}:{session_id}"
    mgr = AgentLoopManager.get_instance()
    return StreamingResponse(
        mgr.subscribe(key),
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no-buffering",
            "Cache-Control": "no-cache",
        },
    )


@router.post("/api/agent/clear")
async def agent_clear(
    body: AgentChatRequest,
    current_user: User = Depends(get_current_user),
):
    from backend.agent.context import AgentContext

    ctx = AgentContext.get()
    ctx.clear(current_user.username, body.session_id)
    return {"ok": True}

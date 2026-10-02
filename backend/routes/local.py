"""本地写入路由（/api/local/*）。

「从 Hub 导入」的正确链路（设计 §4.3 / 03 文档 §7）：
    GET /api/hub/{creator}/{session_name}（远端**公开**接口，免鉴权）
      -> 用**本地**身份写进 cards/local/（游客也能用）

绝不调用远端 /api/hub/{u}/{s}/import —— 那是写进云端账号的工作区。
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.models.user import User
from backend.remote.proxy import proxy as remote_proxy
from backend.routes.auth import get_current_user
from backend.services.hub_import import import_hub_detail

logger = logging.getLogger(__name__)

router = APIRouter()


class HubImportPayload(BaseModel):
    creator: str
    session_name: str


@router.post("/api/local/hub-import")
async def import_from_hub(
    payload: HubImportPayload,
    current_user: User = Depends(get_current_user),
):
    """把远端 Hub 的公开会话复制进**本地**工作区（identity = 本地账号）。"""
    creator = (payload.creator or "").strip()
    session_name = (payload.session_name or "").strip()
    if not creator or not session_name:
        raise HTTPException(status_code=400, detail="creator 与 session_name 不能为空")

    if not remote_proxy.enabled:
        raise HTTPException(
            status_code=400,
            detail="未配置服务器地址，无法从 Hub 导入；请先在「账号与服务器」设置里填写服务器地址",
        )

    remote_path = "/api/hub/" + quote(creator, safe="") + "/" + quote(session_name, safe="")
    try:
        status, detail = await remote_proxy.fetch_json(remote_path)
    except httpx.HTTPError as exc:
        logger.warning("[HubImport] 远端不可达: %s -> %s", remote_path, exc)
        return JSONResponse(
            status_code=502,
            content={
                "detail": "远程服务器不可达，请检查网络或稍后重试",
                "status_code": 502,
                "remote": remote_proxy.base_url,
                "error": type(exc).__name__,
            },
        )
    except RuntimeError as exc:  # 运行中被关掉（KD_SERVER_URL 置空）
        raise HTTPException(status_code=400, detail="未配置服务器地址，无法从 Hub 导入") from exc

    if status == 404:
        raise HTTPException(status_code=404, detail="Hub 上没有这个共享会话")
    if not (200 <= status < 300) or not isinstance(detail, dict):
        raise HTTPException(status_code=502, detail="读取 Hub 会话失败（远端返回 " + str(status) + "）")

    source = "hub:" + creator + "/" + session_name
    try:
        result = import_hub_detail(detail, username=current_user.username, source=source)
    except Exception as exc:  # 落盘失败要显式报错，不能静默
        logger.exception("[HubImport] 写入本地工作区失败")
        raise HTTPException(status_code=500, detail="写入本地工作区失败：" + str(exc)) from exc

    logger.info(
        "[HubImport] %s -> 本地会话 %s（%d 张卡）",
        source, result["session_id"], result["card_count"],
    )
    return {"status": "ok", "source": source, **result}


__all__ = ["router"]

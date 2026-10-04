"""跨源纵深防御（红队 §1.1 / 设计 §3.1）。

CORS 只能阻止跨源**读取**响应，挡不住「简单请求」被真正执行；
所以再加一道 Origin 校验中间件：

- 带 Origin 且不在白名单内            -> 403
- 带 Sec-Fetch-Site: cross-site 且无 Origin -> 403
- 无 Origin（curl / 同源导航 / SSR）  -> 放行

v3 之后主屏障已经是「内置本地账号的 token」（跨源 JS 读不到 localStorage），
本中间件是纵深防御，不是唯一屏障。
"""

from __future__ import annotations

import logging
import re
from typing import override

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request

from backend.config import CORS_ORIGINS, CORS_ORIGIN_REGEX

logger = logging.getLogger(__name__)

_REJECTION = {"detail": "跨源请求被拒绝", "status_code": 403}


def allowed_origins() -> list[str]:
    """CORS_ORIGINS 解析成列表（逗号分隔，忽略空项）。"""
    return [item.strip() for item in CORS_ORIGINS.split(",") if item.strip()]


def origin_is_allowed(origin: str) -> bool:
    """Origin 是否在白名单内（显式列表 + 回环 regex 兜底）。"""
    value = (origin or "").strip()
    if not value:
        return False
    allowed = allowed_origins()
    if "*" in allowed:
        return True
    if value in allowed:
        return True
    return bool(re.match(CORS_ORIGIN_REGEX, value, flags=re.IGNORECASE))


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Origin / Sec-Fetch-Site 校验：不通过直接 403，不进入业务路由。"""

    @override
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        origin = request.headers.get("origin")
        if origin:
            if not origin_is_allowed(origin):
                # 不打印这一行的话，前端只会看到「本地工作区启动失败」，无从判断是谁被拒了
                logger.warning(
                    "[OriginGuard] 拒绝跨源请求: origin=%r path=%s。"
                    "若是你用局域网地址打开本机前端（vite 启动横幅里的 Network 链接），"
                    "改用 http://localhost:3000 打开，或把该来源加进 CORS_ORIGINS",
                    origin,
                    request.url.path,
                )
                return JSONResponse(status_code=403, content=dict(_REJECTION))
        elif request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
            # 部分浏览器在跨源简单请求上省略 Origin，但有 Sec-Fetch-Site
            return JSONResponse(status_code=403, content=dict(_REJECTION))
        return await call_next(request)


__all__ = ["OriginGuardMiddleware", "allowed_origins", "origin_is_allowed"]

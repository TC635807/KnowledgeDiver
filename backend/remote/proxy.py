"""反向代理网关：把客户端的白名单路径透明转发到官方服务器（05-实施契约 §2.4）。

设计要点（每条都有对应测试，实测依据见 03-代理方案可行性.md）：
- **显式路径白名单，不做前缀透传**：避免顺带放行 POST /api/auth/upgrade 这类
  服务器侧无支付校验的敏感接口（红队 §1.4）。
- 代理路由注册在本地 auth/hub 路由**之前**（FastAPI 按注册顺序匹配），
  但只注册白名单路径，所以本地 /api/sessions、/api/cards 等不可能被转发。
- **不解析、不校验 JWT**：token 由远端签发、由远端校验，本地后端不需要服务器的 JWT_SECRET。
- 响应用 aiter_raw 流式透传：multipart / 二进制头像 / chunked 均不做二次编码。
- Location 改写 + server/date 去重 + 502 结构化降级 + 分级体上限。
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from backend.config import (
    KD_REMOTE_CONNECT_TIMEOUT,
    KD_REMOTE_READ_TIMEOUT,
    KD_SERVER_PROXY,
    KD_SERVER_URL,
)

logger = logging.getLogger(__name__)

# 请求体上限：普通身份/头像 8MB；迁移上传下载 60MB
# （与远端 MAX_UPLOAD_SIZE=50MB、nginx 60m 对齐，避免在本机先于 nginx 触发）
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_TRANSFER_BYTES = 60 * 1024 * 1024
# 上传/下载的读超时（普通请求 = KD_REMOTE_READ_TIMEOUT，默认 5s）
TRANSFER_READ_TIMEOUT = 60.0

# 逐跳头（RFC 7230），转发时必须剔除
_HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})
# 请求侧额外剔除：
#   host / content-length 由 httpx 按远端地址重建；
#   origin / referer 是「浏览器」的来源信息，服务端到服务端调用不应伪装它
#   （否则远端自己的 Origin 白名单会拒绝或误判）。
_REQ_DROP = _HOP_BY_HOP | {"host", "content-length", "origin", "referer"}
# 响应侧额外剔除：server / date 由本地 uvicorn 重新生成，避免重复头
_RESP_DROP = _HOP_BY_HOP | {"server", "date"}

# ── 显式路径白名单：(method, 本地路径, 远端路径, 是否迁移通道) ──────────
# 远端路径为 None 表示「与本地路径相同」；带花括号的占位符按 request.path_params 填充。
PROXIED_ROUTES: list[tuple[str, str, str | None, bool]] = [
    # 身份
    ("POST", "/api/auth/register", None, False),
    ("POST", "/api/auth/login", None, False),
    ("GET", "/api/auth/me", None, False),
    ("POST", "/api/auth/avatar", None, False),
    ("GET", "/api/auth/avatar/{username}", None, False),
    # 论坛（浏览免鉴权，互动带服务器 token）
    ("GET", "/api/hub", None, False),
    ("GET", "/api/hub/user/{username}/profile", None, False),
    ("GET", "/api/hub/user/{username}/sessions", None, False),
    ("GET", "/api/hub/{username}/{session_name}", None, False),
    ("POST", "/api/hub/share", None, False),
    ("DELETE", "/api/hub/{username}/{session_name}", None, False),
    ("POST", "/api/hub/{username}/{session_name}/import", None, False),
    ("POST", "/api/hub/{username}/{session_name}/like", None, False),
    ("POST", "/api/hub/{username}/{session_name}/dislike", None, False),
    ("POST", "/api/hub/{username}/{session_name}/comment", None, False),
    ("DELETE", "/api/hub/{username}/{session_name}/comment/{index}", None, False),
    # 迁移专用通道（绝不复用本地 /api/sessions/upload 路径名，红队 §1.3）
    ("GET", "/api/remote/sessions/list", "/api/sessions", False),
    ("POST", "/api/remote/sessions/upload", "/api/sessions/upload", True),
    ("GET", "/api/remote/sessions/download/{session_id}", "/api/sessions/{session_id}/download", True),
]

# 显式拒绝：服务器侧无订单校验的免支付开通会员接口（红队 §1.4）
REJECTED_ROUTES: list[tuple[str, str]] = [
    ("POST", "/api/auth/upgrade"),
]


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "status_code": status_code})


class RemoteProxy:
    """一个指向 base_url 的反向代理网关（可注入 transport 以便测试）。"""

    def __init__(
        self,
        base_url: str,
        *,
        connect_timeout: float = KD_REMOTE_CONNECT_TIMEOUT,
        read_timeout: float = KD_REMOTE_READ_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or "").strip().rstrip("/")
        if self.base_url in ("/", "http:/", "https:/"):
            self.base_url = ""
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._transport = transport
        self._clients: dict[bool, httpx.AsyncClient] = {}
        self.router = self._build_router()

    # ── 生命周期 ──────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        """KD_SERVER_URL 为空 → 整个代理不启用（纯本地模式）。"""
        return bool(self.base_url)

    def set_base_url(self, base_url: str) -> None:
        """运行时切换服务器地址（设置接口 PUT 后立即生效，无需重启）。"""
        new = (base_url or "").strip().rstrip("/")
        if new in ("/", "http:/", "https:/"):
            new = ""
        if new != self.base_url:
            logger.info("[RemoteProxy] 服务器地址已切换: %r -> %r", self.base_url, new)
            self.base_url = new

    async def aclose(self) -> None:
        for client in list(self._clients.values()):
            try:
                await client.aclose()
            except Exception:  # pragma: no cover - 关闭失败无需影响退出
                pass
        self._clients.clear()

    def _client(self, transfer: bool) -> httpx.AsyncClient:
        client = self._clients.get(transfer)
        if client is None:
            timeout = httpx.Timeout(
                connect=self.connect_timeout,
                read=TRANSFER_READ_TIMEOUT if transfer else self.read_timeout,
                write=TRANSFER_READ_TIMEOUT if transfer else self.read_timeout,
                pool=5.0,
            )
            # trust_env=False 是**必需**的：否则 httpx 会读取 shell 里的
            # ALL_PROXY / HTTPS_PROXY 等变量。若用户设的是 socks5 而环境里没有
            # socksio，httpx 会抛 ImportError（不是 HTTPError），表现为
            # 「服务器内部错误」500 —— 这正是实测踩到的坑。
            # 云端域名通常可直连；确实需要代理时用 KD_SERVER_PROXY 显式指定。
            client = httpx.AsyncClient(
                base_url=self.base_url or "http://localhost",
                follow_redirects=False,
                timeout=timeout,
                transport=self._transport,
                trust_env=False,
                proxy=(KD_SERVER_PROXY or None),
            )
            self._clients[transfer] = client
        return client

    # ── 服务端到服务端 GET（例如拉 Hub 公开详情）────────────────────
    async def fetch_json(self, path: str) -> tuple[int, Any]:
        """GET 远端 path，返回 (status_code, json)。

        与代理转发共用同一个 httpx 客户端（连接池/超时一致）；网络异常原样抛
        httpx.HTTPError，由调用方决定降级文案。跟随重定向（这是服务端取数据，
        不是要透传给浏览器）。
        """
        if not self.enabled:
            raise RuntimeError("KD_SERVER_URL 未配置")
        client = self._client(False)
        try:
            resp = await client.get(self.base_url + path, follow_redirects=True)
        except httpx.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一成 HTTPError，交给调用方做 502 降级
            raise httpx.ConnectError(
                "无法发起出站请求（" + type(exc).__name__ + "）：" + str(exc)[:160]
            ) from exc
        try:
            data: Any = resp.json()
        except ValueError:
            data = None
        return resp.status_code, data

    # ── Location 改写 ─────────────────────────────────────────────
    def rewrite_location(self, location: str) -> str:
        """把指向远端的**绝对** Location 改写成本地相对路径。

        不改写的话，浏览器会跟着 307/302 绕过本地代理直连远端地址。
        """
        loc = (location or "").strip()
        if not loc or not self.base_url:
            return loc
        if loc.startswith(self.base_url):
            return loc[len(self.base_url):] or "/"
        try:
            base = urlsplit(self.base_url)
            parts = urlsplit(loc)
        except ValueError:
            return loc
        if parts.scheme in ("http", "https") and parts.netloc and parts.netloc == base.netloc:
            return urlunsplit(("", "", parts.path or "/", parts.query, parts.fragment))
        return loc

    # ── 转发 ──────────────────────────────────────────────────────
    async def _forward(self, request: Request, remote_path: str, transfer: bool) -> Response:
        if not self.enabled:
            return _error(404, "未配置服务器地址，代理不可用")

        limit = MAX_TRANSFER_BYTES if transfer else MAX_BODY_BYTES
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit:
            return _error(413, "请求体过大（上限 " + str(limit // (1024 * 1024)) + "MB）")
        body = await request.body()
        if len(body) > limit:
            return _error(413, "请求体过大（上限 " + str(limit // (1024 * 1024)) + "MB）")

        target = remote_path
        if request.url.query:
            target = remote_path + "?" + request.url.query

        headers = {k: v for k, v in request.headers.items() if k.lower() not in _REQ_DROP}
        prev_xff = request.headers.get("x-forwarded-for")
        client_host = request.client.host if request.client else ""
        headers["X-Forwarded-For"] = (prev_xff + ", " if prev_xff else "") + client_host
        headers["X-Forwarded-Proto"] = request.url.scheme
        headers["X-Forwarded-Host"] = request.headers.get("host", "")

        client = self._client(transfer)
        # 用**绝对** URL：客户端是按第一次的 base_url 缓存的，
        # 这样设置接口切换服务器地址后即刻生效，无需重建连接池。
        remote_req = client.build_request(
            request.method, self.base_url + target, headers=headers, content=body
        )
        try:
            remote_resp = await client.send(remote_req, stream=True)
        except httpx.HTTPError as exc:
            logger.warning("[RemoteProxy] 远程不可达: %s %s -> %s", request.method, target, exc)
            return JSONResponse(
                status_code=502,
                content={
                    "detail": "远程服务器不可达，请检查网络或稍后重试",
                    "status_code": 502,
                    "remote": self.base_url,
                    "error": type(exc).__name__,
                },
            )
        except Exception as exc:  # noqa: BLE001 - 出站任何异常都不能变成不透明的 500
            # 例如 httpx 缺少 socksio 时的 ImportError：既不是 HTTPError，
            # 又不能泄漏成「服务器内部错误」，必须给出可操作的中文提示。
            logger.exception("[RemoteProxy] 出站请求异常: %s %s", request.method, target)
            return JSONResponse(
                status_code=502,
                content={
                    "detail": "本机无法发起对服务器的请求（"
                    + type(exc).__name__
                    + "）：请检查系统代理设置（ALL_PROXY / HTTPS_PROXY）或改用 KD_SERVER_PROXY",
                    "status_code": 502,
                    "remote": self.base_url,
                    "error": type(exc).__name__,
                },
            )

        out_headers = {k: v for k, v in remote_resp.headers.items() if k.lower() not in _RESP_DROP}
        for key in list(out_headers):
            if key.lower() == "location":
                out_headers[key] = self.rewrite_location(out_headers[key])

        return StreamingResponse(
            remote_resp.aiter_raw(),
            status_code=remote_resp.status_code,
            headers=out_headers,
            background=BackgroundTask(remote_resp.aclose),
        )

    # ── 路由构建 ──────────────────────────────────────────────────
    def _build_router(self) -> APIRouter:
        router = APIRouter()

        for method, local_path, remote_path, transfer in PROXIED_ROUTES:
            async def endpoint(request: Request, _remote=remote_path, _transfer=transfer) -> Response:
                target = _remote
                if target is None:
                    target = request.url.path
                else:
                    target = target.format(**request.path_params)
                return await self._forward(request, target, _transfer)

            router.add_api_route(
                local_path,
                endpoint,
                methods=[method],
                name="remote_proxy_" + method.lower() + "_" + local_path,
                response_model=None,
            )

        for method, local_path in REJECTED_ROUTES:
            async def rejected() -> Response:
                raise HTTPException(status_code=404, detail="该接口在客户端不可用")

            router.add_api_route(
                local_path,
                rejected,
                methods=[method],
                name="remote_rejected_" + method.lower() + "_" + local_path,
                response_model=None,
            )

        return router


# 应用级单例：地址/超时取自 backend.config（.env 可覆盖）
proxy = RemoteProxy(KD_SERVER_URL)

__all__ = [
    "RemoteProxy",
    "proxy",
    "PROXIED_ROUTES",
    "REJECTED_ROUTES",
    "MAX_BODY_BYTES",
    "MAX_TRANSFER_BYTES",
]

"""反向代理网关（05-实施契约 §2.4）：白名单、透传、Location 改写、降级、体上限。

全部用 httpx.MockTransport，不打真实网络。
注意：MockTransport 必须返回**未被消费**的流式响应（httpx.Response(stream=...)），
否则代理的 aiter_raw 会抛 StreamConsumed —— 真实服务器上是流式响应，不受影响。
"""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi import APIRouter, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import include_remote_proxy  # noqa: E402
from backend.remote.proxy import (  # noqa: E402
    MAX_BODY_BYTES,
    MAX_TRANSFER_BYTES,
    PROXIED_ROUTES,
    REJECTED_ROUTES,
    TRANSFER_READ_TIMEOUT,
    RemoteProxy,
)

REMOTE = "https://remote.test"


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def reply(status: int = 200, *, json_body=None, body: bytes = b"", headers=None) -> httpx.Response:
    """构造一个未消费的流式响应（模拟真实服务器的流式返回）。"""
    hdrs = dict(headers or {})
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        hdrs.setdefault("content-type", "application/json")
    return httpx.Response(status, headers=hdrs, stream=_Chunks([body]))


class Recorder:
    """记录转发出去的请求，并可自定义响应/异常。"""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.responder = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responder is None:
            return reply(json_body={"ok": True})
        return self.responder(request)

    @property
    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


@pytest.fixture()
def remote() -> Recorder:
    return Recorder()


@pytest.fixture()
def gateway(remote) -> RemoteProxy:
    return RemoteProxy(
        REMOTE,
        connect_timeout=1.0,
        read_timeout=2.0,
        transport=httpx.MockTransport(remote),
    )


@pytest.fixture()
def client(gateway) -> TestClient:
    app = FastAPI()
    app.include_router(gateway.router)
    # follow_redirects=False：测的是**代理返回什么**，不是测试客户端自己会怎么跟随
    return TestClient(app, follow_redirects=False)


# ── 白名单 ────────────────────────────────────────────────────────

def test_whitelist_is_explicit_and_excludes_upgrade():
    rules = {(method, path) for method, path, _, _ in PROXIED_ROUTES}
    assert ("POST", "/api/auth/register") in rules
    assert ("POST", "/api/auth/login") in rules
    assert ("GET", "/api/auth/me") in rules
    assert ("POST", "/api/hub/share") in rules
    assert ("DELETE", "/api/hub/{username}/{session_name}/comment/{index}") in rules
    assert ("GET", "/api/remote/sessions/list") in rules
    # 服务器侧无支付校验的免支付开会员接口：绝不转发
    assert ("POST", "/api/auth/upgrade") not in rules
    assert ("POST", "/api/auth/upgrade") in {tuple(rule) for rule in REJECTED_ROUTES}
    # 本地工作区路径绝不进入白名单
    for _, path in rules:
        assert not path.startswith("/api/sessions")
        assert not path.startswith("/api/cards")
        assert not path.startswith("/api/pipeline")


def test_upgrade_is_rejected_without_forwarding(client, remote):
    resp = client.post("/api/auth/upgrade")
    assert resp.status_code == 404
    assert remote.requests == [], "upgrade 绝不能被转发"


# ── 请求透传 ──────────────────────────────────────────────────────

def test_method_query_and_auth_headers_are_forwarded(client, remote):
    resp = client.get(
        "/api/auth/me?trace=1",
        headers={"Authorization": "Bearer T", "Accept": "application/json"},
    )
    assert resp.status_code == 200
    request = remote.requests[0]
    assert request.method == "GET"
    assert str(request.url) == REMOTE + "/api/auth/me?trace=1"
    assert request.headers["authorization"] == "Bearer T"
    assert request.headers["accept"] == "application/json"


def test_host_and_hop_by_hop_headers_are_dropped(client, remote):
    client.get(
        "/api/hub",
        headers={
            "Connection": "x-hop-token",
            "X-Custom": "1",
            "Transfer-Encoding": "chunked",
        },
    )
    request = remote.requests[0]
    assert request.headers["host"] == "remote.test", "host 必须按远端地址重建"
    # httpx 自己可能补一个 Connection: keep-alive，关键是客户端发来的逐跳值没被透传
    assert "x-hop-token" not in request.headers.get("connection", "")
    assert "transfer-encoding" not in request.headers
    assert request.headers["x-custom"] == "1"


def test_browser_origin_and_referer_are_not_forwarded(client, remote):
    """服务端到服务端调用不应伪装浏览器的来源信息（否则远端 Origin 白名单会误判）。"""
    client.get(
        "/api/hub",
        headers={"Origin": "http://localhost:3000", "Referer": "http://localhost:3000/"},
    )
    request = remote.requests[0]
    assert "origin" not in request.headers
    assert "referer" not in request.headers
    assert request.headers["x-forwarded-proto"] == "http"
    assert request.headers["x-forwarded-for"]


def test_multipart_boundary_is_preserved(client, remote):
    resp = client.post(
        "/api/auth/avatar",
        files={"file": ("a.png", b"PNG-bytes", "image/png")},
        headers={"Authorization": "Bearer T"},
    )
    assert resp.status_code == 200
    request = remote.requests[0]
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    assert b'name="file"' in request.content
    assert request.headers["authorization"] == "Bearer T"


# ── 响应透传 ──────────────────────────────────────────────────────

def test_status_headers_and_body_pass_through(client, remote):
    payload = {"detail": "输入数据验证失败，请检查后重试", "errors": [{"loc": ["body", "x"]}]}
    remote.responder = lambda request: reply(
        422, json_body=payload, headers={"X-Remote": "yes", "server": "uvicorn", "date": "now"}
    )
    resp = client.post("/api/auth/register", json={"username": "a"})
    assert resp.status_code == 422
    assert resp.json() == payload
    assert resp.headers["x-remote"] == "yes"
    assert "server" not in resp.headers
    assert "date" not in resp.headers


def test_error_statuses_are_not_swallowed(client, remote):
    for status in (401, 403, 404, 429):
        remote.responder = lambda request, s=status: reply(
            s, json_body={"detail": "来自远端", "status_code": s}
        )
        resp = client.get("/api/hub")
        assert resp.status_code == status
        assert resp.json()["status_code"] == status


def test_absolute_location_is_rewritten_and_not_followed(client, remote):
    """远端 307 的绝对 Location 必须改写成相对路径，否则浏览器会绕过本地代理。"""
    remote.responder = lambda request: reply(307, headers={"location": REMOTE + "/api/auth/login"})
    resp = client.get("/api/hub")
    assert resp.status_code == 307
    assert resp.headers["location"] == "/api/auth/login", "绝对 Location 必须改写成本地相对路径"
    assert len(remote.requests) == 1, "代理不得跟随重定向（aiter_raw 只消费一次）"


def test_foreign_location_is_left_untouched(client, remote):
    remote.responder = lambda request: reply(302, headers={"location": "https://evil.example/x"})
    resp = client.get("/api/hub")
    assert resp.headers["location"] == "https://evil.example/x"


def test_rewrite_location_unit(gateway):
    assert gateway.rewrite_location(REMOTE + "/api/auth/login") == "/api/auth/login"
    assert gateway.rewrite_location(REMOTE) == "/"
    assert gateway.rewrite_location(REMOTE + "/a?b=1") == "/a?b=1"
    assert gateway.rewrite_location("/already-relative") == "/already-relative"
    assert gateway.rewrite_location("https://evil.example/x") == "https://evil.example/x"
    assert gateway.rewrite_location("") == ""


# ── 降级 ──────────────────────────────────────────────────────────

def test_unreachable_remote_maps_to_structured_502(client, remote):
    def explode(request):
        raise httpx.ConnectError("connection refused", request=request)

    remote.responder = explode
    resp = client.get("/api/hub")
    assert resp.status_code == 502
    assert resp.json() == {
        "detail": "远程服务器不可达，请检查网络或稍后重试",
        "status_code": 502,
        "remote": REMOTE,
        "error": "ConnectError",
    }


def test_read_timeout_maps_to_502(client, remote):
    def explode(request):
        raise httpx.ReadTimeout("timed out", request=request)

    remote.responder = explode
    resp = client.get("/api/auth/me")
    assert resp.status_code == 502
    assert resp.json()["error"] == "ReadTimeout"


def test_local_endpoints_are_never_affected_by_remote_downtime(remote, gateway):
    """远端炸掉时，本地路由照常工作（代理完全不参与）。"""
    def explode(request):
        raise httpx.ConnectError("down", request=request)

    remote.responder = explode
    local = APIRouter()

    @local.get("/api/sessions")
    async def sessions():
        return {"source": "local"}

    app = FastAPI()
    app.include_router(gateway.router)
    app.include_router(local)
    client = TestClient(app)

    assert client.get("/api/sessions").json() == {"source": "local"}
    assert client.get("/api/hub").status_code == 502
    assert remote.urls == [REMOTE + "/api/hub"]


# ── 体积分级与超时 ────────────────────────────────────────────────

def test_oversized_body_is_rejected_with_413_before_forwarding(client, remote):
    big = b"x" * (MAX_BODY_BYTES + 1)
    resp = client.post("/api/auth/avatar", content=big)
    assert resp.status_code == 413
    assert "8MB" in resp.json()["detail"]
    assert remote.requests == [], "超限请求不应打到远端"


def test_transfer_channel_allows_larger_bodies(client, remote):
    body = b"z" * (MAX_BODY_BYTES + 4096)
    resp = client.post("/api/remote/sessions/upload", content=body)
    assert resp.status_code == 200
    assert remote.urls == [REMOTE + "/api/sessions/upload"]
    assert MAX_TRANSFER_BYTES >= 60 * 1024 * 1024


def test_transfer_channel_has_its_own_bigger_timeout():
    proxy = RemoteProxy(REMOTE, read_timeout=5.0)
    assert proxy._client(False).timeout.read == 5.0
    assert proxy._client(True).timeout.read == TRANSFER_READ_TIMEOUT == 60.0


# ── 迁移路径映射 ──────────────────────────────────────────────────

def test_remote_sessions_paths_map_to_real_remote_paths(client, remote):
    client.get("/api/remote/sessions/list")
    client.get("/api/remote/sessions/download/abc-123?x=1")
    client.post("/api/remote/sessions/upload", content=b"zip")
    assert remote.urls == [
        REMOTE + "/api/sessions",
        REMOTE + "/api/sessions/abc-123/download?x=1",
        REMOTE + "/api/sessions/upload",
    ]


# ── 开关、运行时切换与注册顺序 ────────────────────────────────────

def test_empty_server_url_means_proxy_not_registered(remote):
    app = FastAPI()
    assert include_remote_proxy(app, RemoteProxy("")) is False
    assert all(getattr(route, "path", "") != "/api/hub" for route in app.routes)

    enabled = FastAPI()
    assert include_remote_proxy(
        enabled, RemoteProxy(REMOTE, transport=httpx.MockTransport(remote))
    ) is True
    assert TestClient(enabled).get("/api/hub").status_code == 200
    assert remote.urls == [REMOTE + "/api/hub"]


def test_mounted_but_disabled_proxy_returns_404(remote):
    proxy = RemoteProxy(REMOTE, transport=httpx.MockTransport(remote))
    app = FastAPI()
    app.include_router(proxy.router)
    client = TestClient(app)

    assert client.get("/api/hub").status_code == 200
    proxy.set_base_url("")
    assert client.get("/api/hub").status_code == 404
    assert remote.urls == [REMOTE + "/api/hub"], "关闭后不得再打远端"


def test_set_base_url_switches_target_even_with_cached_client(remote):
    """设置接口切地址后必须**立刻**生效：连接池是按第一次的 base_url 建的，转发要用绝对 URL。"""
    proxy = RemoteProxy(REMOTE, transport=httpx.MockTransport(remote))
    app = FastAPI()
    app.include_router(proxy.router)
    client = TestClient(app)

    client.get("/api/hub")                      # 先建立连接池
    proxy.set_base_url("https://other.test/")
    client.get("/api/hub")
    assert remote.urls == [REMOTE + "/api/hub", "https://other.test/api/hub"]


def test_local_workspace_routes_are_not_shadowed(remote, gateway):
    local = APIRouter()

    @local.get("/api/sessions")
    async def sessions():
        return {"source": "local"}

    @local.get("/api/cards")
    async def cards():
        return {"source": "local"}

    app = FastAPI()
    app.include_router(gateway.router)   # 代理注册在前
    app.include_router(local)
    client = TestClient(app)

    assert client.get("/api/sessions").json() == {"source": "local"}
    assert client.get("/api/cards").json() == {"source": "local"}
    assert remote.requests == [], "本地端点一个都不能被转发"


# ── 真实应用集成 ──────────────────────────────────────────────────

def test_real_app_keeps_local_workspace_local(fastapi_app):
    resp = TestClient(fastapi_app).get("/api/cards")
    # /api/cards 不在白名单：无 token 时是**本地** 401，而不是代理的 502/404
    assert resp.status_code == 401
    assert resp.json()["status_code"] == 401


def test_real_app_rejects_upgrade(fastapi_app):
    resp = TestClient(fastapi_app).post("/api/auth/upgrade")
    assert resp.status_code == 404


# ── 出站代理环境隔离（实测回归：ALL_PROXY 无 socksio → 曾表现为 500）─────────

class _Boom(httpx.AsyncBaseTransport):
    """模拟 httpx 在缺少 socksio 时抛出的 ImportError（不是 HTTPError）。"""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def handle_async_request(self, request):
        raise self._exc


def test_outbound_client_ignores_system_proxy_env(monkeypatch):
    """出站客户端必须 trust_env=False，否则会被 shell 的 ALL_PROXY 劫持。"""
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7907")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7907")
    proxy = RemoteProxy(REMOTE, transport=httpx.MockTransport(lambda r: reply()))
    assert proxy._client(False).trust_env is False
    assert proxy._client(True).trust_env is False


def test_socks_import_error_maps_to_502_not_500(remote):
    """ImportError（缺 socksio）必须降级为 502 并给出可操作提示，不能是 500。"""
    boom = RemoteProxy(REMOTE, transport=_Boom(
        ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")
    ))
    app = FastAPI()
    app.include_router(boom.router)
    resp = TestClient(app, raise_server_exceptions=False).get("/api/hub")
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "ImportError"
    assert "代理" in body["detail"]


def test_fetch_json_converts_unknown_error_to_httperror():
    """fetch_json 的未知异常要统一成 httpx.HTTPError，供调用方做 502 降级。"""
    boom = RemoteProxy(REMOTE, transport=_Boom(RuntimeError("boom")))
    import asyncio

    with pytest.raises(httpx.HTTPError):
        asyncio.run(boom.fetch_json("/api/hub/x/y"))


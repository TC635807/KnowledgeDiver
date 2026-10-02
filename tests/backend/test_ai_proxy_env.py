"""AI 出站代理隔离（实测回归：ALL_PROXY=socks5 无 socksio 曾导致 500）。

根因：httpx 在**客户端构造阶段**就会为 socks 代理 import socksio，
缺失时抛 ImportError（不是 HTTPError）。而 AsyncOpenAI 构造原本在 try 之外，
于是变成不透明的 500「服务器内部错误」，并且会打挂所有 AI 功能。
"""

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.routes.auth import get_current_user  # noqa: E402


@pytest.fixture()
def captured_client(monkeypatch):
    """替换 httpx.AsyncClient，记录构造参数。"""
    captured = {}

    class FakeClient(httpx.AsyncClient):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(trust_env=False)

    monkeypatch.setattr("backend.ai.openai_provider.httpx.AsyncClient", FakeClient)
    return captured


def test_ignores_system_proxy_env(captured_client, monkeypatch):
    """未显式配置代理时：trust_env=False、proxy=None（不被 ALL_PROXY 劫持）。"""
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setattr("backend.ai.openai_provider.AI_PROXY_URL", "")
    monkeypatch.setattr("backend.scraper.proxy_config.get_proxy_url", lambda: None)

    from backend.ai.openai_provider import build_ai_http_client

    build_ai_http_client()
    assert captured_client["trust_env"] is False
    assert captured_client["proxy"] is None


def test_uses_explicit_ai_proxy_url(captured_client, monkeypatch):
    monkeypatch.setattr("backend.ai.openai_provider.AI_PROXY_URL", "socks5://127.0.0.1:7897")

    from backend.ai.openai_provider import build_ai_http_client

    build_ai_http_client()
    assert captured_client["proxy"] == "socks5://127.0.0.1:7897"


def test_falls_back_to_global_proxy_port(captured_client, monkeypatch):
    monkeypatch.setattr("backend.ai.openai_provider.AI_PROXY_URL", "")
    monkeypatch.setattr(
        "backend.scraper.proxy_config.get_proxy_url", lambda: "http://127.0.0.1:7897"
    )

    from backend.ai.openai_provider import build_ai_http_client

    build_ai_http_client()
    assert captured_client["proxy"] == "http://127.0.0.1:7897"


def test_ai_test_endpoint_never_returns_500_on_construction_error(monkeypatch):
    """构造失败也必须返回 ok:false 的可读提示，而不是 500。"""

    def boom(*args, **kwargs):
        raise ImportError("Using SOCKS proxy, but the 'socksio' package is not installed.")

    # 注意：端点内部是函数级 "from openai import AsyncOpenAI"，所以要打 openai 模块
    monkeypatch.setattr("openai.AsyncOpenAI", boom)
    monkeypatch.setattr(
        "backend.routes.settings.effective_settings",
        lambda: {"api_url": "https://ollama.com/v1", "api_key": "k", "model": "m"},
    )
    app.dependency_overrides[get_current_user] = lambda: User(
        username="u", hashed_password="x", created_at=datetime.utcnow()
    )
    try:
        resp = TestClient(app, raise_server_exceptions=False).post("/api/settings/ai/test")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False
    assert "socksio" in body["message"]

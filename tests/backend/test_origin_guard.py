"""跨源加固（红队 §1.1 / 设计 §3.1）：CORS 白名单 + Origin 校验中间件。

锁定：
- CORS_ORIGINS 默认不再是通配 "*"；
- 带 Origin 且不在白名单 -> 403（而不是 200），无 Origin（curl/同源）放行；
- 带 Sec-Fetch-Site: cross-site 且无 Origin -> 403；
- 拒绝对「写接口」同样生效（红队原始攻击面：PUT /api/settings/ai 偷 AI Key）。
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import security as security_module  # noqa: E402
from backend.config import CORS_ORIGINS  # noqa: E402
from backend.security import allowed_origins, origin_is_allowed  # noqa: E402

EVIL = "https://evil.example"
# 只读、不落盘的本地端点：用来验证「请求穿过了 Origin 校验」
PROBE_PATH = "/api/settings/ai"


@pytest.fixture()
def client(fastapi_app):
    return TestClient(fastapi_app)


def test_default_cors_origins_is_loopback_whitelist_not_wildcard():
    origins = allowed_origins()
    assert "*" not in origins, "默认 CORS 绝不能再是通配"
    assert "http://localhost:3000" in origins
    assert "http://127.0.0.1:8000" in origins
    assert CORS_ORIGINS.strip() != "*"


def test_evil_origin_is_rejected_before_business_route(client):
    resp = client.get(PROBE_PATH, headers={"Origin": EVIL})
    assert resp.status_code == 403
    assert resp.json() == {"detail": "跨源请求被拒绝", "status_code": 403}


def test_evil_origin_cannot_write_settings(client):
    """红队原始攻击：任意网页跨源 PUT /api/settings/ai 把 api_url 改到攻击者地址。"""
    resp = client.put(
        "/api/settings/ai",
        headers={"Origin": EVIL},
        json={"api_url": "https://attacker.example/v1"},
    )
    assert resp.status_code == 403


def test_origin_guard_covers_health_endpoint(client):
    assert client.get("/", headers={"Origin": EVIL}).status_code == 403


def test_allowed_origin_passes_the_guard(client):
    resp = client.get(PROBE_PATH, headers={"Origin": "http://localhost:3000"})
    # 穿过了 Origin 校验 -> 因缺少本地 token 而 401
    assert resp.status_code == 401


def test_loopback_regex_allows_any_port(client):
    """vite 端口可变（3001/5173…）：回环 regex 兜底，不会误杀本机前端。"""
    for origin in ("http://127.0.0.1:5173", "http://localhost:3001", "http://[::1]:8080"):
        assert origin_is_allowed(origin), origin
        assert client.get(PROBE_PATH, headers={"Origin": origin}).status_code == 401


def test_requests_without_origin_pass(client):
    """curl / 同源导航 / SSR：无 Origin 一律放行（再由 token 决定鉴权）。"""
    resp = client.get(PROBE_PATH)
    assert resp.status_code == 401


def test_cross_site_sec_fetch_without_origin_is_rejected(client):
    resp = client.get(PROBE_PATH, headers={"Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403


def test_same_origin_sec_fetch_without_origin_passes(client):
    resp = client.get(PROBE_PATH, headers={"Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 401


def test_origin_is_allowed_unit_cases(monkeypatch):
    assert origin_is_allowed("http://localhost:3000") is True
    assert origin_is_allowed("https://evil.example") is False
    assert origin_is_allowed("null") is False
    assert origin_is_allowed("") is False

    # 显式配置 "*" 是运维主动选择（向后兼容），此时放行一切
    monkeypatch.setattr(security_module, "CORS_ORIGINS", "*")
    assert origin_is_allowed("https://any.example") is True

    # 显式白名单之外的域仍然被拒
    monkeypatch.setattr(security_module, "CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setattr(security_module, "CORS_ORIGIN_REGEX", "^$")
    assert origin_is_allowed("http://localhost:3000") is True
    assert origin_is_allowed("http://127.0.0.1:3000") is False

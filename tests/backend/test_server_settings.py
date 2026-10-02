"""「账号与服务器」设置（05-实施契约 §2.5）：GET/PUT /api/settings/server + 探活。

锁定：
- 复用 ai_settings 的 .env 就地改写（不新建配置文件），空串 = 删除键回到默认；
- 只接受 http(s):// 地址；写回后代理**立即**改用新地址（无需重启）；
- 探活先 /api/meta，失败回落 /api/hub；5s 超时且永不抛异常。
"""

import os
import stat
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

from backend.config import (  # noqa: E402
    KD_REMOTE_CONNECT_TIMEOUT,
    KD_REMOTE_READ_TIMEOUT,
    KD_SERVER_URL_DEFAULT,
    LOCAL_ACCOUNT_USER,
)
from backend.models.user import User  # noqa: E402
from backend.remote.proxy import proxy as remote_proxy  # noqa: E402
from backend.routes import settings as settings_module  # noqa: E402
from backend.routes.auth import get_current_user  # noqa: E402
from backend.services import ai_settings  # noqa: E402

SERVER_PATH = "/api/settings/server"
TEST_PATH = "/api/settings/server/test"


@pytest.fixture()
def env_file(tmp_path, monkeypatch) -> Path:
    """把 .env 指向临时文件，并清掉环境里的干扰值。"""
    target = tmp_path / ".env"
    monkeypatch.setattr(ai_settings, "ENV_PATH", target)
    monkeypatch.delenv("KD_SERVER_URL", raising=False)
    return target


@pytest.fixture()
def client(fastapi_app, env_file) -> TestClient:
    local_user = User(username="local", hashed_password="hashed", created_at=datetime.utcnow())
    fastapi_app.dependency_overrides[get_current_user] = lambda: local_user
    snapshot = remote_proxy.base_url
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()
    remote_proxy.set_base_url(snapshot)


@pytest.fixture()
def probe(monkeypatch):
    """可编程的探活 transport；返回被调用的 URL 列表。"""
    calls: list[str] = []
    responses: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        result = responses.get(str(request.url), 404)
        if isinstance(result, Exception):
            raise result
        return httpx.Response(result, json={"ok": True})

    monkeypatch.setattr(settings_module, "_probe_transport", httpx.MockTransport(handler))
    return {"calls": calls, "responses": responses}


# ── GET ───────────────────────────────────────────────────────────

def test_get_server_settings_shape(client):
    resp = client.get(SERVER_PATH)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server_url"] == KD_SERVER_URL_DEFAULT
    assert body["server_url_source"] == "default"
    assert body["defaults"]["server_url"] == KD_SERVER_URL_DEFAULT
    assert body["connect_timeout"] == KD_REMOTE_CONNECT_TIMEOUT == 3
    assert body["read_timeout"] == KD_REMOTE_READ_TIMEOUT == 5
    assert body["local_account_user"] == LOCAL_ACCOUNT_USER
    assert body["local_account_enabled"] is True


def test_server_settings_require_local_token(fastapi_app):
    fastapi_app.dependency_overrides.pop(get_current_user, None)
    anonymous = TestClient(fastapi_app)
    assert anonymous.get(SERVER_PATH).status_code == 401
    assert anonymous.put(SERVER_PATH, json={"server_url": ""}).status_code == 401
    assert anonymous.post(TEST_PATH, json={}).status_code == 401


# ── PUT ───────────────────────────────────────────────────────────

def test_put_writes_env_in_place_and_reports_source(client, env_file):
    resp = client.put(SERVER_PATH, json={"server_url": "https://knowledgediver.cloud/"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server_url"] == "https://knowledgediver.cloud"  # 去掉尾斜杠
    assert body["server_url_source"] == "env_file"

    text = env_file.read_text(encoding="utf-8")
    assert "KD_SERVER_URL=https://knowledgediver.cloud" in text
    assert text.count("KD_SERVER_URL=") == 1
    assert ai_settings.read_env_file()["KD_SERVER_URL"] == "https://knowledgediver.cloud"


def test_put_preserves_other_env_lines(client, env_file):
    env_file.write_text("# 注释保留\nJWT_SECRET=keep-me\nAI_MODEL=m\n", encoding="utf-8")
    client.put(SERVER_PATH, json={"server_url": "https://a.example"})

    text = env_file.read_text(encoding="utf-8")
    assert "# 注释保留" in text
    assert "JWT_SECRET=keep-me" in text
    assert "AI_MODEL=m" in text
    assert "KD_SERVER_URL=https://a.example" in text


def test_put_rejects_non_http_urls(client, env_file):
    for bad in ("ftp://x.example", "knowledgediver.cloud", "http://", "javascript:alert(1)"):
        resp = client.put(SERVER_PATH, json={"server_url": bad})
        assert resp.status_code == 400, bad
        assert not env_file.exists() or "KD_SERVER_URL" not in env_file.read_text(encoding="utf-8")


def test_put_empty_string_deletes_key_and_falls_back_to_default(client, env_file):
    client.put(SERVER_PATH, json={"server_url": "https://a.example"})
    resp = client.put(SERVER_PATH, json={"server_url": ""})
    assert resp.status_code == 200
    body = resp.json()
    assert body["server_url"] == KD_SERVER_URL_DEFAULT
    assert body["server_url_source"] == "default"
    assert "KD_SERVER_URL" not in env_file.read_text(encoding="utf-8")


def test_put_takes_effect_on_the_proxy_immediately(client):
    client.put(SERVER_PATH, json={"server_url": "https://other.test/"})
    assert remote_proxy.base_url == "https://other.test"
    assert remote_proxy.enabled is True


def test_new_env_file_keeps_0600_permissions(client, env_file):
    client.put(SERVER_PATH, json={"server_url": "https://a.example"})
    assert stat.S_IMODE(os.stat(env_file).st_mode) == 0o600
    assert sorted(p.name for p in env_file.parent.iterdir()) == [".env"], "不得新建其它配置文件"


# ── 探活 ──────────────────────────────────────────────────────────

def test_probe_prefers_meta(client, probe):
    probe["responses"]["https://ok.example/api/meta"] = 200
    resp = client.post(TEST_PATH, json={"server_url": "https://ok.example"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == 200
    assert body["remote"] == "https://ok.example"
    assert isinstance(body["latency_ms"], int) and body["latency_ms"] >= 0
    assert probe["calls"] == ["https://ok.example/api/meta"]


def test_probe_falls_back_to_hub_when_meta_is_missing(client, probe):
    probe["responses"]["https://old.example/api/meta"] = 404
    probe["responses"]["https://old.example/api/hub?page=1&page_size=1"] = 200
    body = client.post(TEST_PATH, json={"server_url": "https://old.example"}).json()
    assert body["ok"] is True
    assert body["status"] == 200
    assert probe["calls"] == [
        "https://old.example/api/meta",
        "https://old.example/api/hub?page=1&page_size=1",
    ]


def test_probe_failure_reports_detail_and_never_raises(client, probe):
    probe["responses"]["https://down.example/api/meta"] = 404
    probe["responses"]["https://down.example/api/hub?page=1&page_size=1"] = 500
    resp = client.post(TEST_PATH, json={"server_url": "https://down.example"})
    assert resp.status_code == 200, "探活失败也必须是 200 + ok=false，不能抛错"
    body = resp.json()
    assert body["ok"] is False
    assert body["status"] == 500
    assert "404" in body["detail"] and "500" in body["detail"]


def test_probe_network_error_is_reported_not_raised(client, probe):
    probe["responses"]["https://gone.example/api/meta"] = httpx.ConnectTimeout("timeout")
    probe["responses"]["https://gone.example/api/hub?page=1&page_size=1"] = httpx.ConnectTimeout("timeout")
    body = client.post(TEST_PATH, json={"server_url": "https://gone.example"}).json()
    assert body["ok"] is False
    assert "ConnectTimeout" in body["detail"]


def test_probe_rejects_invalid_url(client):
    body = client.post(TEST_PATH, json={"server_url": "not-a-url"}).json()
    assert body["ok"] is False
    assert body["status"] is None
    assert "http://" in body["detail"]


def test_probe_defaults_to_effective_server_url(client, probe):
    probe["responses"][KD_SERVER_URL_DEFAULT + "/api/meta"] = 200
    body = client.post(TEST_PATH, json={}).json()
    assert body["ok"] is True
    assert body["remote"] == KD_SERVER_URL_DEFAULT

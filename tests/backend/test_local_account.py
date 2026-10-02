"""内置本地账号（v3）：POST /api/auth/local-session。

锁定：
- 默认（LOCAL_ACCOUNT=1）为内置账号幂等创建、并签发长期本地 JWT，前端启动时零登录动作；
- LOCAL_ACCOUNT=0 -> 403 本地账号未启用；
- 本地 token 能通过 get_current_user（本地鉴权语义完全不变，不需要 auto_error=False）。
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.auth.jwt import decode_access_token  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.routes import auth as auth_module  # noqa: E402
from backend.routes.auth import ensure_local_user, get_user_store  # noqa: E402


class FakeUserStore:
    """内存版用户仓库：只实现本地账号链路用到的方法，绝不碰真实数据库。"""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}

    def get_user(self, username: str):
        return self.users.get(username)

    def create_user(self, username: str, password: str):
        if username in self.users:
            raise ValueError("用户名已存在")
        user = User(username=username, hashed_password="hashed", created_at=datetime.utcnow())
        self.users[username] = user
        return user

    def update_user(self, username: str, **kwargs):
        user = self.users.get(username)
        if user is not None and "avatar_url" in kwargs:
            self.users[username] = user.model_copy(update={"avatar_url": kwargs["avatar_url"]})


@pytest.fixture()
def store() -> FakeUserStore:
    return FakeUserStore()


@pytest.fixture()
def client(fastapi_app, store, monkeypatch):
    fastapi_app.dependency_overrides[get_user_store] = lambda: store
    monkeypatch.setattr(auth_module, "LOCAL_ACCOUNT", True)
    monkeypatch.setattr(auth_module, "LOCAL_ACCOUNT_USER", "local")
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def test_local_session_issues_long_lived_token(client, store):
    resp = client.post("/api/auth/local-session")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {"access_token", "token_type", "username"}
    assert body["token_type"] == "bearer"
    assert body["username"] == "local"

    payload = decode_access_token(body["access_token"])
    assert payload is not None
    assert payload["sub"] == "local"

    # 30 天有效期（与 JWT_EXPIRATION_HOURS 的短 TTL 区分开）
    remaining = datetime.fromtimestamp(payload["exp"], tz=timezone.utc) - datetime.now(timezone.utc)
    assert timedelta(days=29) < remaining <= timedelta(days=31)

    # 账号被真正创建（随机密码只用于满足 get_user 必须存在）
    assert "local" in store.users


def test_local_session_is_idempotent(client, store):
    first = client.post("/api/auth/local-session")
    second = client.post("/api/auth/local-session")
    assert first.status_code == 200 and second.status_code == 200
    assert list(store.users) == ["local"]
    assert decode_access_token(first.json()["access_token"])["sub"] == "local"
    assert decode_access_token(second.json()["access_token"])["sub"] == "local"


def test_local_token_authenticates_local_endpoints(client):
    """本地 token 能通过 get_current_user：鉴权语义完全不变。

    这里刻意选 /api/settings/ai —— 它不在代理白名单里（证明走的是本地路由），
    且只读、不落盘，测试不产生任何工作区副作用。
    """
    token = client.post("/api/auth/local-session").json()["access_token"]
    resp = client.get("/api/settings/ai", headers={"Authorization": "Bearer " + token})
    assert resp.status_code == 200, resp.text
    assert "api_url" in resp.json()


def test_local_endpoints_still_require_a_token(client):
    """v3 不改鉴权语义：本地端点无 token 仍然 401（不是被当游客放行）。"""
    resp = client.get("/api/settings/ai")
    assert resp.status_code == 401
    assert resp.json()["status_code"] == 401


def test_local_session_disabled_returns_403(client, monkeypatch):
    monkeypatch.setattr(auth_module, "LOCAL_ACCOUNT", False)
    resp = client.post("/api/auth/local-session")
    assert resp.status_code == 403
    assert resp.json() == {"detail": "本地账号未启用", "status_code": 403}


def test_ensure_local_user_is_idempotent(store):
    first = ensure_local_user(store)
    second = ensure_local_user(store)
    assert first.username == "local" == second.username
    assert len(store.users) == 1


def test_ensure_local_user_survives_concurrent_create(store):
    """并发下 create_user 抛 ValueError 时应读回已有账号而不是报错。"""
    store.create_user("local", "x")
    assert ensure_local_user(store).username == "local"

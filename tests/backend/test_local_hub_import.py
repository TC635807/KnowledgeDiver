"""从 Hub 导入到本地工作区（task-12 / 契约 §2.6）：POST /api/local/hub-import。

锁定：
- 数据来源是远端**公开**详情接口；写入用**本地**身份，落在 cards/<local>/；
- 绝不调用远端 /api/hub/{u}/{s}/import（那是写进云端账号工作区）；
- 重名自动追加「 (来自 Hub)」/ 序号；卡片 id 冲突重新分配；links/backlinks 统一重建；
- KD_SERVER_URL 为空 -> 400 明确中文提示；远端不可达 -> 502；远端 404 -> 404。
"""

import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.models.user import User  # noqa: E402
from backend.remote.proxy import RemoteProxy  # noqa: E402
from backend.routes import local as local_route  # noqa: E402
from backend.routes.auth import get_current_user  # noqa: E402
from backend.services.hub_import import import_hub_detail, sanitize_session_name, unique_session_name  # noqa: E402
from backend.storage import session_database  # noqa: E402
from backend.storage.session_store import SessionStore  # noqa: E402
from backend.storage.sqlite_card_store import SqliteCardStore  # noqa: E402

IMPORT_PATH = "/api/local/hub-import"
REMOTE = "https://remote.test"
LOCAL_USER = "local"


# ── 隔离：把 cards/ 与 session.db 都落到 tmp_path ────────────────

@pytest.fixture()
def workspace(tmp_path, monkeypatch) -> Path:
    """SessionStore 用 cwd 相对路径，SessionDatabaseManager 用 PROJECT_ROOT —— 两者都要指到 tmp。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(session_database, "PROJECT_ROOT", tmp_path)
    return tmp_path


def hub_detail(name: str, cards=None, creator: str = "alice") -> dict:
    return {
        "manifest": {"name": name, "creator": creator, "description": "来自社区的会话", "topics": ["t"]},
        "cards": cards if cards is not None else [],
    }


def card_payload(title: str, content: str, *, card_id=None, links=None, backlinks=None, sources=None):
    return {
        "id": card_id or str(uuid.uuid4()),
        "title": title,
        "content": content,
        "metadata": {"confidence": "0.8"},
        "links": links or [],
        "backlinks": backlinks or [],
        "sources": sources or ["https://example.com/a"],
        "tags": ["tag1"],
        "confidence": 0.8,
    }


# ── 服务层：真实存储落盘 ─────────────────────────────────────────

def test_import_writes_session_and_cards_to_local_workspace(workspace):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    detail = hub_detail(
        "共享会话",
        [
            card_payload("卡 A", "# 卡 A\n\nA 的正文", card_id=a, links=[b]),
            card_payload("卡 B", "B 的正文", card_id=b, backlinks=[a]),
        ],
    )

    result = import_hub_detail(detail, username=LOCAL_USER, source="hub:alice/共享会话")

    assert result["card_count"] == 2
    assert result["renamed"] is False

    # 落盘位置：cards/local/sessions.json + cards/local/{sid}/session.db
    assert (workspace / "cards" / LOCAL_USER / "sessions.json").exists()
    assert (workspace / "cards" / LOCAL_USER / result["session_id"] / "session.db").exists()

    sessions = {s.id: s for s in SessionStore(username=LOCAL_USER).list_sessions()}
    assert result["session_id"] in sessions
    assert sessions[result["session_id"]].name == "共享会话"
    assert sessions[result["session_id"]].card_count == 2

    cards = {c.id: c for c in SqliteCardStore(username=LOCAL_USER, session_id=result["session_id"]).list_cards()}
    assert set(cards) == {a, b}
    assert cards[a].title == "卡 A"
    assert cards[a].content == "A 的正文", "正文里的「# 标题」前缀应被去掉"
    assert cards[a].metadata["imported_from"] == "hub:alice/共享会话"
    assert cards[a].sources == ["https://example.com/a"]
    assert cards[a].tags == ["tag1"]
    assert cards[a].confidence == 0.8

    # links/backlinks 统一重建为无向邻接集（与 LinkManager 不变式一致）
    assert cards[a].links == [b] and cards[a].backlinks == [b]
    assert cards[b].links == [a] and cards[b].backlinks == [a]


def test_import_keeps_duplicate_names_unique(workspace):
    detail = hub_detail("重复名", [card_payload("卡", "正文")])

    first = import_hub_detail(detail, username=LOCAL_USER, source="hub:alice/重复名")
    second = import_hub_detail(detail, username=LOCAL_USER, source="hub:alice/重复名")
    third = import_hub_detail(detail, username=LOCAL_USER, source="hub:alice/重复名")

    assert first["session_name"] == "重复名" and first["renamed"] is False
    assert second["session_name"] == "重复名 (来自 Hub)" and second["renamed"] is True
    assert third["session_name"] == "重复名 (来自 Hub) 2" and third["renamed"] is True
    assert first["session_id"] != second["session_id"] != third["session_id"]


def test_import_remaps_invalid_and_conflicting_card_ids(workspace):
    shared = str(uuid.uuid4())
    detail = hub_detail(
        "id 冲突",
        [
            card_payload("卡1", "c1", card_id=shared, links=[shared, "not-a-uuid"]),
            card_payload("卡2", "c2", card_id=shared),      # 同一个 id 出现两次
            card_payload("卡3", "c3", card_id="not-a-uuid"),
        ],
    )

    result = import_hub_detail(detail, username=LOCAL_USER, source="hub:alice/id")
    cards = SqliteCardStore(username=LOCAL_USER, session_id=result["session_id"]).list_cards()

    assert len(cards) == 3
    assert len({c.id for c in cards}) == 3, "id 必须互不冲突"
    for card in cards:
        uuid.UUID(card.id)  # 必须是合法 UUID
    # 自引用与不存在的引用都要被丢掉
    for card in cards:
        assert card.links == [] and card.backlinks == []


def test_import_handles_empty_and_dirty_payloads(workspace):
    result = import_hub_detail({"manifest": {"name": "  "}, "cards": None}, LOCAL_USER, "hub:x/y")
    assert result["session_name"] == "未命名会话"
    assert result["card_count"] == 0
    assert result["renamed"] is False


def test_sanitize_and_unique_helpers():
    assert sanitize_session_name(" a/b\\c..d   e ") == "a-b-c.d e"
    assert sanitize_session_name("") == "未命名会话"
    assert unique_session_name("x", set()) == "x"
    assert unique_session_name("x", {"x"}) == "x (来自 Hub)"
    assert unique_session_name("x", {"x", "x (来自 Hub)"}) == "x (来自 Hub) 2"


# ── 路由层 ───────────────────────────────────────────────────────

class Recorder:
    def __init__(self):
        self.urls: list[str] = []
        self.responder = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(str(request.url))
        if self.responder is None:
            return httpx.Response(200, json=hub_detail("远端会话", [card_payload("卡", "正文")]))
        return self.responder(request)


@pytest.fixture()
def remote() -> Recorder:
    return Recorder()


@pytest.fixture()
def gateway(monkeypatch, remote) -> RemoteProxy:
    proxy = RemoteProxy(REMOTE, transport=httpx.MockTransport(remote))
    monkeypatch.setattr(local_route, "remote_proxy", proxy)
    return proxy


@pytest.fixture()
def client(fastapi_app, workspace, gateway) -> TestClient:
    fastapi_app.dependency_overrides[get_current_user] = lambda: User(
        username=LOCAL_USER, hashed_password="hashed", created_at=datetime.utcnow()
    )
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def test_route_imports_through_public_detail_endpoint(client, remote, workspace):
    resp = client.post(IMPORT_PATH, json={"creator": "alice", "session_name": "共享会话"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["session_id"] and body["session_name"] == "远端会话"
    assert body["card_count"] == 1
    assert body["source"] == "hub:alice/共享会话"
    # 数据来源必须是**公开详情**接口，绝不是 /import
    assert remote.urls == [REMOTE + "/api/hub/alice/" + "%E5%85%B1%E4%BA%AB%E4%BC%9A%E8%AF%9D"]

    # 卡片真的落在了本地 local 工作区
    cards = SqliteCardStore(username=LOCAL_USER, session_id=body["session_id"]).list_cards()
    assert [c.title for c in cards] == ["卡"]


def test_route_requires_a_local_token(fastapi_app, workspace, gateway):
    fastapi_app.dependency_overrides.pop(get_current_user, None)
    resp = TestClient(fastapi_app).post(IMPORT_PATH, json={"creator": "a", "session_name": "b"})
    assert resp.status_code == 401


def test_route_rejects_blank_params(client, remote):
    assert client.post(IMPORT_PATH, json={"creator": " ", "session_name": "b"}).status_code == 400
    assert remote.urls == []


def test_route_reports_missing_server_configuration(client, gateway, remote):
    gateway.set_base_url("")
    resp = client.post(IMPORT_PATH, json={"creator": "alice", "session_name": "s"})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "未配置服务器地址，无法从 Hub 导入；请先在「账号与服务器」设置里填写服务器地址"
    assert remote.urls == []


def test_route_maps_remote_404_to_chinese_detail(client, remote):
    remote.responder = lambda request: httpx.Response(404, json={"detail": "Hub session not found"})
    resp = client.post(IMPORT_PATH, json={"creator": "alice", "session_name": "gone"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Hub 上没有这个共享会话"


def test_route_maps_remote_failure_to_502(client, remote):
    remote.responder = lambda request: httpx.Response(500, text="boom")
    resp = client.post(IMPORT_PATH, json={"creator": "alice", "session_name": "s"})
    assert resp.status_code == 502
    assert "500" in resp.json()["detail"]


def test_route_maps_unreachable_remote_to_structured_502(client, remote):
    def explode(request):
        raise httpx.ConnectError("refused", request=request)

    remote.responder = explode
    resp = client.post(IMPORT_PATH, json={"creator": "alice", "session_name": "s"})
    assert resp.status_code == 502
    assert resp.json() == {
        "detail": "远程服务器不可达，请检查网络或稍后重试",
        "status_code": 502,
        "remote": REMOTE,
        "error": "ConnectError",
    }


def test_route_never_calls_remote_import(client, remote):
    client.post(IMPORT_PATH, json={"creator": "alice", "session_name": "s"})
    assert all("/import" not in url for url in remote.urls)

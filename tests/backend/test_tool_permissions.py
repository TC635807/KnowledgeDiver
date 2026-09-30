"""工具权限分级与审计测试（T14 重构② / T12 P0 安全）。

锁定：
- legacy（默认）不拦截任何工具，行为零变化；
- no_write 拒绝 4 个写层工具，且**不触达底层 API**（拒绝发生在派发之前）；
- readonly 额外拒绝处方层；读层始终可用；
- 未知模式回退 legacy；
- 审计开关默认关闭（零副作用），开启后写 JSONL 且包含工具名+层级+成功标志。
"""

import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config as config_mod  # noqa: E402
from backend.agent import tool_registry  # noqa: E402
from backend.agent.tools import ToolExecutor  # noqa: E402
from backend.models.card import Card  # noqa: E402


def _card(title: str) -> Card:
    t = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    return Card(
        id=str(uuid.uuid4()), title=title, content="内容 " + title, metadata={},
        links=[], backlinks=[], sources=[], tags=[],
        created_at=t, updated_at=t, confidence=0.8,
    )


class FakeQuality:
    def __init__(self, *args, **kwargs):
        pass

    def enrich(self, card):
        return {"quality": {"gap_score": 0.5}}

    def enrich_all(self, cards):
        return [{"id": c.id, "title": c.title, "quality": {}} for c in cards]

    def get_score(self, card_id):
        return {"gap_score": 0.4, "gap_percentile": 0.5, "structure_score": 0.5,
                "semantic_score": 0.5, "graph_score": 0.5, "confidence_score": 0.5, "title": "某卡"}

    def get_cluster_report(self):
        return {"clusters": [], "undercovered": []}

    def get_all_scores(self):
        return []

    def invalidate_card(self, card_id):
        pass

    def refresh(self):
        pass


class FakeAPI:
    def __init__(self):
        self.c1 = _card("测试卡")
        self.cards = {self.c1.id: self.c1}
        self.calls = []
        self.card_store = None
        self.raw_store = None
        self.username = "u1"
        self.session_id = "s1"

    def get_card_info(self, card_id):
        self.calls.append(("get_card_info", card_id))
        return self.cards.get(card_id)

    def list_cards(self):
        return list(self.cards.values())

    def get_linked_cards(self, card_id):
        return []

    async def search_similar_cards(self, query, limit=5, threshold=0.5):
        self.calls.append(("search_similar_cards", query))
        return []

    async def expand_from_card_with_task(self, card_id, **kwargs):
        self.calls.append(("expand_from_card_with_task", card_id))
        return [_card("扩展卡")], None

    async def search_by_keyword_with_task(self, keyword, **kwargs):
        self.calls.append(("search_by_keyword_with_task", keyword))
        return [], None

    async def gap_driven_exploration_with_task(self, keyword, **kwargs):
        self.calls.append(("gap_driven", keyword))
        return [], None

    def update_card(self, **kwargs):
        self.calls.append(("update_card", kwargs.get("card_id")))

    def link_cards(self, a, b, parent=None):
        self.calls.append(("link_cards", (a, b)))
        return True


@pytest.fixture
def executor(monkeypatch):
    import backend.agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "QualityProvider", FakeQuality)
    return ToolExecutor(FakeAPI())


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_switches(monkeypatch):
    for env in ("KD_TOOL_PERMISSION", "KD_TOOL_AUDIT", "KD_TOOL_AUDIT_PATH"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(config_mod, "TOOL_PERMISSION_MODE", "legacy", raising=False)
    monkeypatch.setattr(config_mod, "TOOL_AUDIT_ENABLED", False, raising=False)


# ── 模式解析 ─────────────────────────────────────────────────────────

def test_default_mode_is_legacy_and_allows_everything():
    assert tool_registry.permission_mode() == "legacy"
    assert tool_registry.blocked_tiers() == frozenset()
    for name in tool_registry._TOOL_NAMES:
        allowed, reason = tool_registry.is_tool_allowed(name)
        assert allowed is True and reason == ""


def test_unknown_mode_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("KD_TOOL_PERMISSION", "bogus")
    assert tool_registry.permission_mode() == "legacy"
    assert tool_registry.blocked_tiers() == frozenset()


def test_no_write_blocks_only_write_tier(monkeypatch):
    monkeypatch.setenv("KD_TOOL_PERMISSION", "no_write")
    assert tool_registry.blocked_tiers() == frozenset({"write"})
    assert tool_registry.is_tool_allowed("search_by_keyword")[0] is False
    assert tool_registry.is_tool_allowed("link_card")[0] is False
    assert tool_registry.is_tool_allowed("plan_knowledge_gaps")[0] is True
    assert tool_registry.is_tool_allowed("get_card_info")[0] is True


def test_readonly_blocks_write_and_prescribe(monkeypatch):
    monkeypatch.setenv("KD_TOOL_PERMISSION", "readonly")
    assert tool_registry.blocked_tiers() == frozenset({"write", "prescribe"})
    assert tool_registry.is_tool_allowed("plan_knowledge_gaps")[0] is False
    assert tool_registry.is_tool_allowed("assess_knowledge_base")[0] is True


# ── 执行器拦截 ───────────────────────────────────────────────────────

def test_execute_legacy_unchanged(executor):
    r = run(executor.execute("get_card_info", {"card_id": executor._api.c1.id}))
    assert r.success
    assert "blocked" not in (r.data or {})


def test_execute_no_write_blocks_before_dispatch(executor, monkeypatch):
    monkeypatch.setenv("KD_TOOL_PERMISSION", "no_write")
    r = run(executor.execute("expand_from_card", {"card_id": executor._api.c1.id}))
    assert not r.success
    assert r.data == {"blocked": True, "reason": "permission", "tier": "write"}
    assert "权限模式 no_write" in r.summary
    # 关键安全断言：被拒的写工具没有触达底层 API
    assert not [c for c in executor._api.calls if c[0] == "expand_from_card_with_task"]


def test_execute_no_write_blocks_search_and_link(executor, monkeypatch):
    monkeypatch.setenv("KD_TOOL_PERMISSION", "no_write")
    r1 = run(executor.execute("search_by_keyword", {"keyword": "某主题"}))
    r2 = run(executor.execute("link_card", {"card_id_a": executor._api.c1.id,
                                            "card_id_b": executor._api.c1.id}))
    assert not r1.success and r1.data["tier"] == "write"
    assert not r2.success and r2.data["tier"] == "write"
    assert not [c for c in executor._api.calls if c[0] in ("search_by_keyword_with_task", "link_cards")]


def test_execute_readonly_allows_read_blocks_prescribe(executor, monkeypatch):
    monkeypatch.setenv("KD_TOOL_PERMISSION", "readonly")
    ok = run(executor.execute("assess_card_quality", {}))
    denied = run(executor.execute("plan_knowledge_gaps", {}))
    assert ok.success
    assert not denied.success and denied.data["tier"] == "prescribe"


def test_write_streak_state_not_mutated_on_denial(executor, monkeypatch):
    monkeypatch.setenv("KD_TOOL_PERMISSION", "no_write")
    before = executor._write_streak
    run(executor.execute("search_by_keyword", {"keyword": "x"}))
    assert executor._write_streak == before


# ── 审计 ─────────────────────────────────────────────────────────────

def test_audit_disabled_writes_nothing(executor, tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(config_mod, "TOOL_AUDIT_PATH", str(path), raising=False)
    assert tool_registry.audit_enabled() is False
    run(executor.execute("get_card_info", {"card_id": executor._api.c1.id}))
    assert not path.exists()


def test_audit_enabled_records_tool_tier_success(executor, tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("KD_TOOL_AUDIT", "1")
    monkeypatch.setattr(config_mod, "TOOL_AUDIT_PATH", str(path), raising=False)
    assert tool_registry.audit_enabled() is True

    run(executor.execute("get_card_info", {"card_id": executor._api.c1.id}))
    run(executor.execute("expand_from_card", {"card_id": executor._api.c1.id}))

    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 2
    assert lines[0]["tool"] == "get_card_info" and lines[0]["tier"] == "read"
    assert lines[0]["success"] is True and lines[0]["username"] == "u1"
    assert lines[1]["tool"] == "expand_from_card" and lines[1]["tier"] == "write"


def test_loop_filters_blocked_tiers_from_schemas():
    """loop 侧接线守卫：非 legacy 模式必须先把禁用层级的 schema 移除。"""
    import backend.agent.loop as loop_mod
    src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    assert "blocked_tiers()" in src
    assert 'tool_tier(s["function"]["name"])' in src


def test_audit_records_denied_call(executor, tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("KD_TOOL_PERMISSION", "no_write")
    monkeypatch.setenv("KD_TOOL_AUDIT", "1")
    monkeypatch.setattr(config_mod, "TOOL_AUDIT_PATH", str(path), raising=False)
    run(executor.execute("refresh_card", {"card_id": executor._api.c1.id}))
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["tool"] == "refresh_card" and rec["tier"] == "write"
    assert rec["success"] is False and rec["blocked"] is True and rec["reason"] == "permission"

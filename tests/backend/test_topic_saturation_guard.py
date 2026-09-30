"""主题饱和守卫（P0-2）与 refresh 可达性（P0-1）回归测试。

锁定：
- legacy 开关（默认）下 assess_exploration_need 的 data 与改动前逐字段一致（无新键）；
- topic_guard 开关下：饱和主题不再把 expand 推荐指向本卡，改向 undercovered 代表卡；
  覆盖不足卡 / weak 簇 / unknown 卡仍保留原路径；
- 守卫内部异常 → 捕获后回退 legacy（不打断工具）；
- KD_REFRESH_RULE=percentile 让"绝对门槛 0 命中"的库恢复可触发，并输出可达性审计；
- 关键字分支不再因未定义 best_gap 抛 NameError。
"""

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models.card import Card  # noqa: E402
from backend.quality.topic_guard import (  # noqa: E402
    SATURATED_AVG_GAP_MAX,
    best_undercovered,
    card_topic_verdict,
    cluster_saturated,
    library_saturation_cutoff,
    topic_aware_decision,
)

# Card.id 受 pydantic 校验，必须是合法 UUID 字符串
MAIN = "11111111-1111-4111-8111-111111111111"
GAPCARD = "22222222-2222-4222-8222-222222222222"
WEAKCARD = "33333333-3333-4333-8333-333333333333"


def _uid() -> str:
    return str(uuid.uuid4())


def _card(cid: str, title: str) -> Card:
    t = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    return Card(
        id=cid, title=title, content="测试内容 " + title, metadata={},
        links=[], backlinks=[], sources=[], tags=[],
        created_at=t, updated_at=t, confidence=0.8,
    )


# ── 簇报告构造 ────────────────────────────────────────────────────────

def _cluster(cid, card_ids, *, status="healthy", avg_gap=0.2, size=None):
    return {
        "cluster_id": cid, "size": size if size is not None else len(card_ids),
        "card_ids": list(card_ids),
        "titles": [f"t-{c}" for c in card_ids],
        "avg_gap": avg_gap, "worst_gap": round(avg_gap + 0.2, 4),
        "compactness": 0.2, "link_density": 0.5, "link_density_low": False,
        "dims": {"content": 0.8, "sources": 0.7, "links": 0.6},
        "status": status, "priority": 0 if status == "healthy" else 2,
    }


def _report(*, avg_gap=0.2, status="healthy", undercovered=True,
            main_id=MAIN, undercovered_id=GAPCARD):
    clusters = [_cluster(0, [main_id, "c-2", "c-3", "c-4", "c-5"],
                         status=status, avg_gap=avg_gap)]
    if status == "weak":
        clusters.append(_cluster(1, [WEAKCARD, "c-7", "c-8", "c-9"], status="weak", avg_gap=0.7))
    uc = []
    if undercovered:
        uc.append({"card_id": undercovered_id, "title": "缺口主题代表卡",
                   "gap_score": 0.82, "cluster_size": 2})
        uc.append({"card_id": "u-2", "title": "另一个孤立卡",
                   "gap_score": 0.61, "cluster_size": 1})
    return {"n_cards": 10, "n_clusters": len(clusters), "n_undercovered": len(uc),
            "median_avg_gap": avg_gap, "clusters": clusters, "undercovered": uc}


# ── 执行器替身 ────────────────────────────────────────────────────────

class FakeAPI:
    def __init__(self, similar=None):
        self.cards = {
            MAIN: _card(MAIN, "主卡"),
            GAPCARD: _card(GAPCARD, "缺口主题代表卡"),
            WEAKCARD: _card(WEAKCARD, "薄弱簇卡"),
        }
        self.similar = similar or []
        self.calls = []
        self.card_store = None
        self.raw_store = None
        self.username = "test-user"
        self.session_id = "test-session"

    def get_card_info(self, card_id):
        self.calls.append(("get_card_info", {"card_id": card_id}))
        return self.cards.get(card_id)

    def list_cards(self):
        return list(self.cards.values())

    async def search_similar_cards(self, query, limit=5, threshold=0.5):
        self.calls.append(("search_similar_cards", {"query": query}))
        return list(self.similar)

    async def search_by_keyword_with_task(self, keyword, **kwargs):
        self.calls.append(("search_by_keyword_with_task", {"keyword": keyword}))
        return [_card(_uid(), "新卡")], None

    async def expand_from_card_with_task(self, card_id, **kwargs):
        self.calls.append(("expand_from_card_with_task", {"card_id": card_id}))
        return [_card(_uid(), "扩展卡")], None


def _executor(monkeypatch, *, score=None, report=None, all_scores=None,
              similar=None, guard_boom=False):
    import backend.agent.tools as tools_mod

    class FakeQuality:
        def __init__(self, *args, **kwargs):
            pass

        def enrich(self, card):
            return {"quality": {}}

        def enrich_all(self, cards):
            return [{"id": c.id, "title": c.title, "quality": {}} for c in cards]

        def get_score(self, card_id):
            return dict(score) if score is not None else None

        def get_cluster_report(self):
            return report if report is not None else {"clusters": [], "undercovered": []}

        def get_all_scores(self):
            return list(all_scores or [])

        def invalidate_card(self, card_id):
            pass

        def refresh(self):
            pass

    monkeypatch.setattr(tools_mod, "QualityProvider", FakeQuality)
    if guard_boom:
        def _boom(*a, **k):
            raise RuntimeError("guard exploded")
        monkeypatch.setattr(tools_mod, "topic_aware_decision", _boom)
    return tools_mod.ToolExecutor(FakeAPI(similar=similar))


def run(coro):
    return asyncio.run(coro)


def _score(gap=0.5, pct=0.5, struct=0.5):
    return {
        "card_id": MAIN, "title": "主卡", "gap_score": gap, "gap_percentile": pct,
        "structure_score": struct, "semantic_score": 0.5, "graph_score": 0.5,
        "confidence_score": 0.5,
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("KD_DECISION_MODE", raising=False)
    monkeypatch.delenv("KD_REFRESH_RULE", raising=False)


# ── 纯函数：主题判定 ─────────────────────────────────────────────────

def test_saturated_when_healthy_and_low_avg_gap():
    v = card_topic_verdict(_report(avg_gap=0.2), MAIN)
    assert v["topic_saturated"] is True
    assert v["topic_status"] == "healthy"
    assert v["topic_gap"] == pytest.approx(0.2)


def test_not_saturated_when_avg_gap_just_above_threshold():
    v = card_topic_verdict(_report(avg_gap=SATURATED_AVG_GAP_MAX + 0.01), MAIN)
    assert v["topic_saturated"] is False


def test_not_saturated_for_weak_status():
    v = card_topic_verdict(_report(status="weak", avg_gap=0.7), WEAKCARD)
    assert v["topic_status"] == "weak"
    assert v["topic_saturated"] is False


def test_cluster_saturated_shared_rule():
    assert cluster_saturated(_cluster(0, [MAIN] + ["c"] * 4, avg_gap=0.2)) is True
    assert cluster_saturated(_cluster(0, [MAIN] + ["c"] * 4, avg_gap=0.4)) is False
    assert cluster_saturated(_cluster(0, [MAIN] + ["c"] * 4, status="weak")) is False
    assert cluster_saturated(None) is False


def test_library_cutoff_is_relative_to_library():
    """绝对档 0.35 在真实库不可达（实测簇 avg_gap ∈ [0.43,0.55]）→ 用库内上中位数。"""
    report = {"clusters": [
        _cluster(0, [MAIN] + ["c"] * 4, avg_gap=0.43),
        _cluster(1, ["c9"] + ["d"] * 4, avg_gap=0.47),
        _cluster(2, ["e1"] + ["f"] * 4, avg_gap=0.52, status="weak"),
    ], "undercovered": []}
    assert library_saturation_cutoff(report) == pytest.approx(0.47)


def test_library_cutoff_falls_back_when_single_cluster():
    report = {"clusters": [_cluster(0, [MAIN] + ["c"] * 4, avg_gap=0.43)],
              "undercovered": []}
    assert library_saturation_cutoff(report) == pytest.approx(SATURATED_AVG_GAP_MAX)


def test_library_cutoff_skips_fragmented():
    report = {"clusters": [
        _cluster(0, [MAIN] + ["c"] * 4, avg_gap=0.40, status="fragmented"),
        _cluster(1, ["e1"] + ["f"] * 4, avg_gap=0.46),
    ], "undercovered": []}
    assert library_saturation_cutoff(report) == pytest.approx(SATURATED_AVG_GAP_MAX)


def test_relative_cutoff_enables_realistic_saturation():
    """真实量级的 healthy 簇（avg_gap 0.45/0.48）在相对档下可被判饱和。"""
    report = {"clusters": [
        _cluster(0, [MAIN] + ["c"] * 4, avg_gap=0.45),
        _cluster(1, ["e1"] + ["f"] * 4, avg_gap=0.48),
    ], "undercovered": []}
    v = card_topic_verdict(report, MAIN)
    assert v["saturation_cutoff"] == pytest.approx(0.48)
    assert v["topic_saturated"] is True
    # 绝对档下不可达（记录实现前行为，防止阈值悄悄回退）
    assert cluster_saturated(report["clusters"][0]) is False


def test_unknown_card_is_conservative():
    v = card_topic_verdict(_report(), "no-such-card")
    assert v["topic_status"] == "unknown"
    assert v["topic_saturated"] is False


def test_empty_report_is_conservative():
    v = card_topic_verdict({}, MAIN)
    assert v["topic_status"] == "unknown"
    assert v["topic_saturated"] is False


def test_undercovered_card_not_saturated_topic_gap_one():
    v = card_topic_verdict(_report(), GAPCARD)
    assert v["topic_status"] == "undercovered"
    assert v["topic_saturated"] is False
    assert v["topic_gap"] == 1.0


def test_best_undercovered_picks_max_gap():
    red = best_undercovered(_report())
    assert red["card_id"] == GAPCARD
    assert red["gap_score"] == pytest.approx(0.82)


def test_best_undercovered_excludes_self_and_none_when_empty():
    assert best_undercovered(_report(undercovered_id=GAPCARD),
                             exclude_card_id=GAPCARD)["card_id"] == "u-2"
    assert best_undercovered({"clusters": [], "undercovered": []}) is None


def test_topic_aware_decision_redirect_but_not_self():
    d = topic_aware_decision(_report(undercovered_id=GAPCARD), GAPCARD)
    # GAPCARD 自身是 undercovered -> 不饱和，不触发守卫
    assert d["guard_applied"] is False


def test_topic_aware_decision_redirect_fields():
    d = topic_aware_decision(_report(), MAIN)
    assert d["guard_applied"] is True
    assert d["decision"] == "needs_expand"
    assert d["redirect_card_id"] == GAPCARD
    assert d["redirect_card_id"] != MAIN
    assert "饱和" in d["reason"]
    assert "缺口主题代表卡" in d["reason"]


def test_topic_aware_decision_saturated_without_undercovered():
    d = topic_aware_decision(_report(undercovered=False), MAIN)
    assert d["guard_applied"] is True
    assert d["decision"] == "local_sufficient"
    assert d["redirect_card_id"] is None
    assert "饱和" in d["reason"]


# ── 执行器：legacy 默认行为不变 ──────────────────────────────────────

def test_legacy_default_card_path_no_new_keys(monkeypatch):
    ex = _executor(monkeypatch, score=_score(), report=_report())
    r = run(ex._assess_exploration_need({"card_id": MAIN}))
    assert r.success, r.summary
    assert r.data["decision"] == "needs_expand"  # 旧链：中间区间 -> expand
    for key in ("topic_saturated", "topic_status", "topic_gap", "redirect_card_id",
                "topic_guard_applied", "reachability"):
        assert key not in r.data, f"legacy 不应新增字段 {key}"
    assert "饱和" not in r.summary


def test_legacy_default_matches_reference_dict(monkeypatch):
    """legacy 输出逐字段与改动前实现一致（防止新逻辑意外改写旧路径）。"""
    score = _score(gap=0.42, pct=0.6, struct=0.5)
    ex = _executor(monkeypatch, score=score, report=_report())
    r = run(ex._assess_exploration_need({"card_id": MAIN}))
    expected = {
        "card_id": MAIN, "title": "主卡",
        "gap_score": 0.42, "gap_percentile": 0.6,
        "structure_score": 0.5, "semantic_score": 0.5,
        "graph_score": 0.5, "confidence_score": 0.5,
        "cluster_status": "healthy", "cluster_size": 5,
        "decision": "needs_expand",
        "recommendation": "gap=0.42 处于中间区间，优先用 expand_from_card 扩展关联内容（graph=0.50）",
    }
    assert r.data == expected


def test_legacy_unknown_env_falls_back(monkeypatch):
    import backend.agent.tools as tools_mod
    monkeypatch.setenv("KD_DECISION_MODE", "bogus")
    monkeypatch.setenv("KD_REFRESH_RULE", "bogus")
    assert tools_mod._decision_mode() == "legacy"
    assert tools_mod._refresh_rule() == "legacy"


# ── 执行器：topic_guard 行为 ─────────────────────────────────────────

def test_guard_redirects_saturated_topic_expand(monkeypatch):
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    ex = _executor(monkeypatch, score=_score(), report=_report(),
                   all_scores=[_score()])
    r = run(ex._assess_exploration_need({"card_id": MAIN}))
    assert r.success, r.summary
    assert r.data["topic_saturated"] is True
    assert r.data["topic_guard_applied"] is True
    assert r.data["redirect_card_id"] == GAPCARD
    assert r.data["redirect_card_id"] != MAIN
    assert "饱和" in r.data["recommendation"]
    assert "reachability" in r.data


def test_guard_undercovered_card_still_expands(monkeypatch):
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    ex = _executor(monkeypatch, score=_score(gap=0.5, pct=0.6), report=_report(),
                   all_scores=[])
    r = run(ex._assess_exploration_need({"card_id": GAPCARD}))
    assert r.success, r.summary
    assert r.data["decision"] == "needs_expand"
    assert r.data["topic_saturated"] is False
    assert r.data["redirect_card_id"] is None
    assert r.data["cluster_status"] == "undercovered"


def test_guard_weak_cluster_not_overridden(monkeypatch):
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    ex = _executor(monkeypatch, score=_score(gap=0.9, pct=0.95, struct=0.6),
                   report=_report(status="weak", avg_gap=0.7), all_scores=[])
    r = run(ex._assess_exploration_need({"card_id": WEAKCARD}))
    assert r.success, r.summary
    assert r.data["decision"] == "needs_expand"
    assert r.data["topic_saturated"] is False
    assert "域整体薄弱" in r.data["recommendation"]


def test_guard_saturated_without_undercovered_is_local_sufficient(monkeypatch):
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    ex = _executor(monkeypatch, score=_score(), report=_report(undercovered=False),
                   all_scores=[])
    r = run(ex._assess_exploration_need({"card_id": MAIN}))
    assert r.success, r.summary
    assert r.data["decision"] == "local_sufficient"
    assert r.data["redirect_card_id"] is None


def test_guard_refresh_precedence_kept(monkeypatch):
    """refresh 判定优先于饱和守卫（repair 与 exploration 是两种动作）。"""
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    ex = _executor(monkeypatch, score=_score(gap=0.9, pct=0.99, struct=0.1),
                   report=_report(), all_scores=[])
    r = run(ex._assess_exploration_need({"card_id": MAIN}))
    assert r.data["decision"] == "needs_refresh"
    assert r.data["topic_saturated"] is True  # 主题信息仍被记录
    assert "饱和" not in r.data["recommendation"]


def test_guard_exception_falls_back_to_legacy(monkeypatch):
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    ex = _executor(monkeypatch, score=_score(), report=_report(),
                   all_scores=[_score()], guard_boom=True)
    r = run(ex._assess_exploration_need({"card_id": MAIN}))
    assert r.success, r.summary
    assert r.data["decision"] == "needs_expand"
    assert "topic_saturated" not in r.data
    assert "饱和" not in r.data["recommendation"]


def test_guard_no_score_returns_no_data(monkeypatch):
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    ex = _executor(monkeypatch, score=None, report=_report())
    r = run(ex._assess_exploration_need({"card_id": MAIN}))
    assert r.success
    assert r.data["decision"] == "no_data"


# ── refresh 可达性（P0-1）────────────────────────────────────────────

def test_reachability_report_counts_absolute_unreachable():
    from backend.agent.tools import _reachability_report
    scores = [
        {"gap_score": 0.55, "gap_percentile": 1.0, "structure_score": 0.1},
        {"gap_score": 0.40, "gap_percentile": 0.5, "structure_score": 0.6},
    ]
    rep = _reachability_report(scores)
    assert rep["n_cards"] == 2
    assert rep["n_abs_ok"] == 0                # 最高 gap 0.55 < 绝对门槛 0.65
    assert rep["n_reachable_legacy"] == 0
    assert rep["reachable_legacy"] is False
    assert rep["n_reachable_percentile"] == 1  # 分位规则在同样的库上可达
    assert rep["reachable_percentile"] is True
    assert rep["max_gap"] == pytest.approx(0.55)


def test_reachability_report_empty_library():
    from backend.agent.tools import _reachability_report
    rep = _reachability_report([])
    assert rep["n_cards"] == 0
    assert rep["max_gap"] is None
    assert rep["reachable_legacy"] is False


def test_percentile_rule_restores_refresh_reachability(monkeypatch):
    score = _score(gap=0.55, pct=1.0, struct=0.1)
    # 旧规则：绝对 gap 0.65 门槛不可达 -> 不会 refresh
    ex = _executor(monkeypatch, score=score, report=_report())
    r_legacy = run(ex._assess_exploration_need({"card_id": MAIN}))
    assert r_legacy.data["decision"] != "needs_refresh"

    monkeypatch.setenv("KD_REFRESH_RULE", "percentile")
    ex2 = _executor(monkeypatch, score=score, report=_report(),
                    all_scores=[score])
    r_pct = run(ex2._assess_exploration_need({"card_id": MAIN}))
    assert r_pct.data["decision"] == "needs_refresh", r_pct.summary
    assert r_pct.data["reachability"]["n_reachable_legacy"] == 0
    assert r_pct.data["reachability"]["n_reachable_percentile"] >= 1


# ── 关键字分支（原实现 NameError）────────────────────────────────────

def test_keyword_branch_returns_decision_not_nameerror(monkeypatch):
    similar = [{"card": _card(MAIN, "主卡"), "score": 0.8}]
    ex = _executor(monkeypatch, score=_score(), report=_report(), similar=similar)
    r = run(ex._assess_exploration_need({"keyword": "某个主题"}))
    assert r.success, r.summary
    assert isinstance(r.data["best_gap"], float)
    assert r.data["decision"] in {"local_sufficient", "needs_refresh", "optional"}


def test_keyword_branch_guard_saturated(monkeypatch):
    monkeypatch.setenv("KD_DECISION_MODE", "topic_guard")
    similar = [{"card": _card(MAIN, "主卡"), "score": 0.8}]
    ex = _executor(monkeypatch, score=_score(), report=_report(),
                   all_scores=[_score()], similar=similar)
    r = run(ex._assess_exploration_need({"keyword": "已饱和主题"}))
    assert r.success, r.summary
    assert r.data["decision"] == "local_sufficient"
    assert r.data["topic_saturated"] is True
    assert r.data["redirect_card_id"] == GAPCARD
    assert "饱和" in r.data["recommendation"]


def test_keyword_branch_no_local_still_no_local(monkeypatch):
    ex = _executor(monkeypatch, score=_score(), report=_report(), similar=[])
    r = run(ex._assess_exploration_need({"keyword": "完全新主题"}))
    assert r.success
    assert r.data["decision"] == "no_local"

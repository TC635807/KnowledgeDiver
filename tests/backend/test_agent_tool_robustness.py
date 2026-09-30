"""Agent 工具执行器幻觉容错回归测试。

锁定行为（针对真实会话取证的失败模式）：
- 标题当 ID 调用 → 自动按标题回退（get_card_info / get_linked_cards / expand_from_card /
  refresh_card / link_card / assess_exploration_need / search_by_keyword 的 source_card_id）
- 缺参数 → 点名报错而非 KeyError 泛化"执行失败"
- 工具名拼错 → 模糊匹配执行（search_keyword → search_by_keyword 等）
- LLM 输出坏 JSON arguments → 轻量修复或明确报错回喂（不静默降级 {}）
- link_card 双 ID 失败定位到具体无效方
- assess_exploration_need 幻觉 ID → 明确报错，不误引导"可以直接联网搜索"
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

from backend.agent.tools import ToolExecutor  # noqa: E402
from backend.models.card import Card  # noqa: E402


def _card(title: str) -> Card:
    return Card(
        id=str(uuid.uuid4()), title=title, content="测试内容 " + title,
        metadata={}, created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc), confidence=0.8,
        sources=[], tags=[],
    )


class FakeQuality:
    """QualityProvider 的替身：不发嵌入模型、不落库。"""

    def __init__(self, *args, **kwargs):
        pass

    def enrich(self, card: Card) -> dict:
        return {"quality": {"gap_score": 0.5}}

    def enrich_all(self, cards) -> list:
        return [{"id": c.id, "title": c.title, "quality": {}} for c in cards]

    def get_score(self, card_id: str) -> dict | None:
        return {"gap_score": 0.4, "structure_score": 0.5, "semantic_score": 0.5,
                "graph_score": 0.5, "confidence_score": 0.5, "title": "某卡"}

    def get_cluster_report(self) -> dict:
        return {"clusters": [], "undercovered": []}

    def invalidate_card(self, card_id: str) -> None:
        pass

    def refresh(self) -> None:
        pass


class FakeAPI:
    """PipelineAPI 的最小替身：记录调用参数，供断言回退是否正确。"""

    def __init__(self):
        self.c1 = _card("海克斯大乱斗人马符文")
        self.c2 = _card("海克斯大乱斗吸血鬼符文")
        self.c3 = _card("我们的治疗（海克斯大乱斗）")
        self.cards = {c.id: c for c in (self.c1, self.c2, self.c3)}
        self.linked: list[tuple[str, str]] = []
        self.calls: list[tuple[str, dict]] = []
        self.card_store = None
        self.raw_store = None
        self.username = "test-user"
        self.session_id = "test-session"

    def get_card_info(self, card_id: str) -> Card | None:
        self.calls.append(("get_card_info", {"card_id": card_id}))
        return self.cards.get(card_id)

    def list_cards(self) -> list:
        return list(self.cards.values())

    def get_linked_cards(self, card_id: str) -> list:
        self.calls.append(("get_linked_cards", {"card_id": card_id}))
        return [c2 for c1, c2 in self.linked if c1 == card_id] + \
               [c1 for c1, c2 in self.linked if c2 == card_id]

    async def search_similar_cards(self, query: str, limit: int = 5, threshold: float = 0.5) -> list:
        self.calls.append(("search_similar_cards", {"query": query, "limit": limit, "threshold": threshold}))
        return []  # 默认无相似卡，避免自动挂载干扰用例

    def link_cards(self, a: str, b: str, parent: str | None = None) -> bool:
        self.calls.append(("link_cards", {"a": a, "b": b, "parent": parent}))
        if a not in self.cards or b not in self.cards:
            return False
        self.linked.append((a, b))
        return True

    async def expand_from_card_with_task(self, card_id: str, max_topics: int = 7, external_task=None):
        self.calls.append(("expand_from_card_with_task", {"card_id": card_id}))
        return [_card("扩展子主题卡")], None

    async def search_by_keyword_with_task(self, keyword, max_sources=2, source_card_id=None,
                                          external_task=None, persist=True, explore=False):
        self.calls.append(("search_by_keyword_with_task", {
            "keyword": keyword, "source_card_id": source_card_id, "persist": persist,
        }))
        return [], None

    def update_card(self, card_id, content, metadata, sources):
        self.calls.append(("update_card", {"card_id": card_id}))


@pytest.fixture
def executor(monkeypatch):
    import backend.agent.tools as tools_mod
    monkeypatch.setattr(tools_mod, "QualityProvider", FakeQuality)
    return ToolExecutor(FakeAPI())


def run(coro):
    return asyncio.run(coro)


# ── 标题当 ID → 自动回退 ──────────────────────────────────────────────

def test_get_card_info_title_fallback(executor):
    r = run(executor._get_card_info({"card_id": "海克斯大乱斗人马符文"}))
    assert r.success
    assert "按标题回退" in r.summary


def test_get_linked_title_fallback(executor):
    r = executor._get_linked({"card_id": "海克斯大乱斗人马符文"})
    assert r.success


def test_expand_title_fallback(executor):
    r = run(executor._expand({"card_id": "海克斯大乱斗人马符文"}))
    assert r.success, r.summary
    assert "按标题回退" in r.summary
    # expand 必须用解析出的真实 ID 而非标题
    expand_calls = [c for c in executor._api.calls if c[0] == "expand_from_card_with_task"]
    assert expand_calls and expand_calls[0][1]["card_id"] == executor._api.c1.id


def test_expand_nonexistent_id(executor):
    r = run(executor._expand({"card_id": "不存在的卡"}))
    assert not r.success
    assert "不存在" in r.summary and "list_cards" in r.summary


def test_refresh_title_fallback(executor):
    r = run(executor._refresh_card({"card_id": "海克斯大乱斗人马符文"}))
    # resolve 成功 → 不再报"不存在"；走 search 流程
    assert "不存在" not in r.summary
    search_calls = [c for c in executor._api.calls if c[0] == "search_by_keyword_with_task"]
    assert search_calls


def test_link_card_both_titles(executor):
    r = executor._link_card({"card_id_a": "海克斯大乱斗人马符文", "card_id_b": "海克斯大乱斗吸血鬼符文"})
    assert r.success, r.summary
    link_calls = [c for c in executor._api.calls if c[0] == "link_cards"]
    assert link_calls and link_calls[0][1] == {"a": executor._api.c1.id, "b": executor._api.c2.id, "parent": None}


def test_link_card_with_parent_hint(executor):
    """parent 参数透传：link_card 无向 + 显式树方向。"""
    r = executor._link_card({"card_id_a": "海克斯大乱斗人马符文", "card_id_b": "海克斯大乱斗吸血鬼符文", "parent": "a"})
    assert r.success
    link_calls = [c for c in executor._api.calls if c[0] == "link_cards"]
    assert link_calls and link_calls[0][1]["parent"] == "a"


def test_search_keyword_exact_title_blocked(executor):
    """方案 B：标题预检——归一化精确同名 → blocked，不联网。"""
    r = run(executor._search_keyword({"keyword": "海克斯大乱斗人马符文"}))
    assert not r.success
    assert r.data.get("exact_title") is True
    assert r.data.get("card_id") == executor._api.c1.id
    search_calls = [c for c in executor._api.calls if c[0] == "search_by_keyword_with_task"]
    assert not search_calls, "同名命中必须零联网"


def test_search_keyword_title_variant_blocked(executor):
    """归一化变体也拦截：多空格/大小写差异视为同名。"""
    r = run(executor._search_keyword({"keyword": "海克斯大乱斗人马 符文"}))
    assert not r.success
    assert r.data.get("exact_title") is True


def test_search_keyword_qualified_not_blocked(executor):
    """括号限定不同 → 不拦截（「梯度（深度学习）」vs「梯度」允许建卡）。"""
    r = run(executor._search_keyword({"keyword": "海克斯大乱斗人马符文（教程）"}))
    assert r.data.get("exact_title") is not True


def test_search_keyword_no_same_title_goes_online(executor):
    """无同名卡 → 正常联网搜索。"""
    r = run(executor._search_keyword({"keyword": "完全不同的新主题"}))
    search_calls = [c for c in executor._api.calls if c[0] == "search_by_keyword_with_task"]
    assert search_calls


def test_link_card_one_invalid_located(executor):
    r = executor._link_card({"card_id_a": "海克斯大乱斗人马符文", "card_id_b": "虚构卡牌"})
    assert not r.success
    assert "卡片 B" in r.summary and "虚构卡牌" in r.summary


def test_link_card_self_link_rejected(executor):
    r = executor._link_card({"card_id_a": "海克斯大乱斗人马符文", "card_id_b": "海克斯大乱斗人马符文"})
    assert not r.success
    assert "自身" in r.summary


# ── 缺参数 → 点名报错（非 KeyError）──────────────────────────────────

def test_get_card_info_missing_param(executor):
    r = run(executor._get_card_info({}))
    assert not r.success
    assert "缺少 card_id" in r.summary


def test_expand_missing_param(executor):
    r = run(executor._expand({}))
    assert not r.success
    assert "缺少 card_id" in r.summary


def test_link_card_missing_param(executor):
    r = executor._link_card({"card_id_a": "x"})
    assert not r.success
    assert "缺少 card_id_b" in r.summary


def test_search_keyword_missing_param(executor):
    r = run(executor._search_keyword({}))
    assert not r.success
    assert "缺少 keyword" in r.summary


# ── 工具名模糊匹配 ───────────────────────────────────────────────────

@pytest.mark.parametrize("wrong,right", [
    ("search_keyword", "search_by_keyword"),
    ("get_card", "get_card_info"),
    ("link_cards", "link_card"),
    ("linkcard", "link_card"),
    ("expand", "expand_from_card"),
    ("refresh", "refresh_card"),
    ("assess_exploration", "assess_exploration_need"),
    ("searchsimilar", "search_similar_cards"),
])
def test_fuzzy_tool_name(wrong, right):
    assert ToolExecutor._normalize_tool_name(wrong) == right


@pytest.mark.parametrize("name", ["random_tool", "tool", "link"])
def test_fuzzy_tool_name_ambiguous_none(name):
    assert ToolExecutor._normalize_tool_name(name) is None


def test_unknown_tool_lists_candidates(executor):
    r = run(executor.execute("random_tool", {}))
    assert not r.success
    assert "未知工具" in r.summary and "search_by_keyword" in r.summary


def test_fuzzy_name_executes_correct_tool(executor):
    # "get_card" 幻觉名 → 执行 get_card_info（传标题触发回退，说明走对工具）
    r = run(executor.execute("get_card", {"card_id": "海克斯大乱斗人马符文"}))
    assert r.success
    assert "按标题回退" in r.summary


# ── 坏 JSON arguments → 修复或明确报错 ───────────────────────────────

def test_parse_tool_args_normal():
    from backend.agent.provider import _parse_tool_args
    args, raw = _parse_tool_args('{"keyword": "海克斯大乱斗"}')
    assert args == {"keyword": "海克斯大乱斗"} and raw == ""


def test_parse_tool_args_single_quotes():
    from backend.agent.provider import _parse_tool_args
    args, _ = _parse_tool_args("{'keyword': '海克斯大乱斗'}")
    assert args == {"keyword": "海克斯大乱斗"}


def test_parse_tool_args_trailing_comma():
    from backend.agent.provider import _parse_tool_args
    args, _ = _parse_tool_args('{"keyword": "测试",}')
    assert args == {"keyword": "测试"}


def test_parse_tool_args_bare_key():
    from backend.agent.provider import _parse_tool_args
    args, _ = _parse_tool_args('{keyword: "测试"}')
    assert args == {"keyword": "测试"}


def test_parse_tool_args_unrepairable():
    from backend.agent.provider import _parse_tool_args
    args, raw = _parse_tool_args("keyword 海克斯")
    assert args is None and raw == "keyword 海克斯"


def test_raw_args_rejected_with_explanation(executor):
    r = run(executor.execute("get_card_info", {"_raw_args": "keyword 海克斯"}))
    assert not r.success
    assert "参数解析失败" in r.summary and "keyword 海克斯" in r.summary


# ── assess_exploration_need 幻觉 ID 不再误导搜索 ──────────────────────

def test_assess_exploration_need_phantom_id(executor):
    r = run(executor._assess_exploration_need({"card_id": "虚构卡牌"}))
    assert not r.success
    assert "不存在" in r.summary
    assert "联网搜索" not in r.summary


# ── search_by_keyword 的 source_card_id 支持标题 ─────────────────────

def test_search_keyword_source_card_id_title_resolve(executor):
    r = run(executor._search_keyword({
        "keyword": "海克斯大乱斗对局时长", "source_card_id": "海克斯大乱斗人马符文",
    }))
    # resolve 成功 → source_card_id 被解析为真实 ID 传给底层
    search_calls = [c for c in executor._api.calls if c[0] == "search_by_keyword_with_task"]
    assert search_calls and search_calls[0][1]["source_card_id"] == executor._api.c1.id


# ── 失败回喂纠正引导 ─────────────────────────────────────────────────

def test_build_correction_plan_gaps():
    from backend.agent.loop import _build_correction
    c = _build_correction("plan_knowledge_gaps", "some failure")
    assert "cluster_id" in c and "card_id" not in c.split("不是")[0]


def test_build_correction_not_found():
    from backend.agent.loop import _build_correction
    c = _build_correction("get_card_info", "卡片 X 不存在（ID 与标题均未匹配）")
    assert "list_cards" in c


def test_build_correction_param():
    from backend.agent.loop import _build_correction
    c = _build_correction("search_by_keyword", "参数错误：缺少 keyword")
    assert "参数" in c


# ── _strip_xml_tool_blocks 增强 ──────────────────────────────────────def test_strip_xml_plain():
    from backend.agent.loop import _strip_xml_tool_blocks
    assert _strip_xml_tool_blocks("好的 <search_by_keyword>{\"keyword\":\"x\"}</search_by_keyword> 完成") == "好的  完成"


def test_strip_xml_in_code_block():
    from backend.agent.loop import _strip_xml_tool_blocks
    t = "开始\n```xml\n<get_card_info>\n{\"card_id\":\"x\"}\n</get_card_info>\n```\n结束"
    out = _strip_xml_tool_blocks(t)
    assert "<get_card_info>" not in out and "开始" in out and "结束" in out


def test_strip_tool_hint_line():
    from backend.agent.loop import _strip_xml_tool_blocks
    t = "🔧 正在使用工具: search_similar_cards\n好的"
    assert _strip_xml_tool_blocks(t) == "好的"


# ── 标题变体去重（expand 子卡与源卡仅差空格/括号时排除）───────────────

def test_normalize_title_variants():
    from backend.ai.openai_provider import _normalize_title
    assert _normalize_title("Transformer 架构") == _normalize_title("Transformer架构")
    assert _normalize_title("注意力（机器学习）") == _normalize_title("注意力(机器学习)")
    assert _normalize_title("海克斯大乱斗") == _normalize_title(" 海克斯大乱斗 ")


def test_extract_topics_excludes_title_variant():
    """LLM 返回与源卡标题仅差空格的变体时，代码层兜底剔除（实测 'Transformer架构' vs 'Transformer 架构'）。"""
    import backend.ai.openai_provider as op
    provider = op.OpenAIProvider.__new__(op.OpenAIProvider)

    async def fake_generate(prompt):
        assert "仅差空格" in prompt  # prompt 增强已生效
        return '["自注意力机制", "Transformer架构", "多头注意力机制", "Transformer 架构"]'

    provider.generate = fake_generate
    topics = asyncio.run(provider.extract_related_topics(
        "Transformer 相关内容", exclude_title="Transformer 架构",
    ))
    assert "Transformer架构" not in topics
    assert "Transformer 架构" not in topics
    assert "自注意力机制" in topics


def test_extract_topics_no_exclude_title():
    import backend.ai.openai_provider as op
    provider = op.OpenAIProvider.__new__(op.OpenAIProvider)

    async def fake_generate(prompt):
        return '["注意力机制", "位置编码"]'

    provider.generate = fake_generate
    topics = asyncio.run(provider.extract_related_topics("Transformer 相关内容"))
    assert topics == ["注意力机制", "位置编码"]


# ── AGENT_MAX_TURNS 放宽（12 轮仍一层树，20 轮支持更深树）──────────

def test_agent_max_turns_relaxed():
    from backend.config import AGENT_MAX_TURNS
    assert AGENT_MAX_TURNS >= 20


# ── 树构建容错（keyword-merge 反向边污染）───────────────────────────

def _mk_card(title, created, links=(), backlinks=()):
    from backend.models.card import Card
    return Card(
        id=str(uuid.uuid4()), title=title, content="内容" + title,
        metadata={}, created_at=created, updated_at=created, confidence=0.8,
        sources=[], tags=[], links=list(links), backlinks=list(backlinks),
    )


def test_tree_tolerance_polluted_links(tmp_path):
    """旧污染数据经 parent_id 迁移后树恢复正确。

    场景还原：expand「免疫器官」时 keyword-merge 命中「免疫系统」并建反向边——
    B.links 里混入更早创建的 A（父卡），A.backlinks 里混入 B（a↔b 互指污染）。
    """
    import backend.storage.sqlite_card_store as m
    from backend.storage.parent_migration import infer_parent_ids
    from datetime import timedelta
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    a = _mk_card("免疫系统", t0)                          # 根卡（最早）
    b = _mk_card("免疫器官", t0 + timedelta(minutes=1),
                 links=[a.id], backlinks=[a.id])          # 子卡（被污染：links 含父）
    c = _mk_card("淋巴结", t0 + timedelta(minutes=2),
                 backlinks=[b.id])
    a.links = [b.id]
    a.backlinks = [b.id]                                  # 互指污染
    b.links = [a.id, c.id]                                # b→c 真实边 + b→a 污染边
    store = m.SqliteCardStore(base_dir=str(tmp_path), username="u", session_id="s")
    for card in (a, b, c):
        store.create_card(card)

    # 迁移推断：b 晚于 a 创建 → b 的父是 a；c 的父是 b；a 保持根
    mapping = infer_parent_ids([a, b, c])
    assert mapping[b.id] == a.id, f"互指边按创建时间推断子卡父卡: {mapping}"
    assert mapping[c.id] == b.id
    assert a.id not in mapping, "根卡（最早创建）不设父卡"

    for card in (a, b, c):
        card.parent_id = mapping.get(card.id)
        store.update_card(card)

    assert [r.title for r in store.get_root_cards()] == ["免疫系统"]
    assert [x.title for x in store.get_children(a.id)] == ["免疫器官"]
    assert [x.title for x in store.get_children(b.id)] == ["淋巴结"]


def test_tree_tolerance_reroot_links(tmp_path):
    """re-root 场景（Agent 后建父卡挂载先建子卡）：迁移后树完整，不再丢弃子卡。"""
    import backend.storage.sqlite_card_store as m
    from backend.storage.parent_migration import infer_parent_ids
    from datetime import timedelta
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    agile = _mk_card("敏捷开发", t0)                       # 先建（原根）
    testing = _mk_card("软件测试", t0 + timedelta(minutes=1))
    se = _mk_card("软件工程", t0 + timedelta(minutes=2))   # 后建（新根，re-root）
    # 旧 link_card 单向写入：父.links→子，子.backlinks→父（无互指）
    se.links = [agile.id, testing.id]
    agile.backlinks = [se.id]
    testing.backlinks = [se.id]
    store = m.SqliteCardStore(base_dir=str(tmp_path), username="u", session_id="s")
    for card in (agile, testing, se):
        store.create_card(card)

    mapping = infer_parent_ids([agile, testing, se])
    assert mapping[agile.id] == se.id, "非互指边 links 方向权威（re-root 恢复）"
    assert mapping[testing.id] == se.id
    assert se.id not in mapping, "后建根卡不设父卡"

    for card in (agile, testing, se):
        card.parent_id = mapping.get(card.id)
        store.update_card(card)

    assert [r.title for r in store.get_root_cards()] == ["软件工程"]
    children = sorted(x.title for x in store.get_children(se.id))
    assert children == ["敏捷开发", "软件测试"], f"re-root 后所有子卡可见: {children}"


def test_keyword_merge_does_not_link(tmp_path):
    """pipeline keyword merge 命中已有卡时跳过且不建链（源头修复）。"""
    from backend.pipeline import pipeline as pl
    src = open(pl.__file__, encoding="utf-8").read()
    # 旧实现已移除：不再出现 create_link(source_card_id, existing.id)
    assert "create_link(\n                    source_card_id, existing.id" not in src
    assert "已被已有卡片" in src  # 新行为：跳过并记录

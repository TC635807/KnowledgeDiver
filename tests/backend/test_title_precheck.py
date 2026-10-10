"""方案 B 回归测试：标题预检（防重复搜索）+ 锚定职责前移。

锁定行为：
- normalize_title 归一化等价组（空格/全角空格/全半角括号/大小写）
- find_card_by_normalized_title：精确命中；括号限定不同不命中；语义近似不命中；exclude 排除源卡
- _run_query：persist=True 命中同名 → 零搜索 + 提前返回；persist=False 豁免（refresh 场景）
- run_expand：同名 topic 在搜索前拦截（零搜索）；搜索词 == topic 原样（无锚定拼接）
- run() 全链路：explore 子主题 query 无父卡标题拼接
- extract prompt：source_title 传入时含领域锚定约束（输出即锚定）
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
from backend.pipeline.pipeline import Pipeline  # noqa: E402
from backend.pipeline.stages import ExploreTask  # noqa: E402


def _card(title: str) -> Card:
    now = datetime.now(timezone.utc)
    return Card(
        id=str(uuid.uuid4()), title=title, content="内容" + title,
        metadata={}, created_at=now, updated_at=now, confidence=0.8,
        sources=[], tags=[],
    )


def _store(tmp_path):
    from backend.storage.sqlite_card_store import SqliteCardStore
    return SqliteCardStore(base_dir=str(tmp_path), username="u", session_id="s")


def run(coro):
    return asyncio.run(coro)


async def _collect(agen):
    """异步收集 async generator 的事件。"""
    return [x async for x in agen]


# ── 测试桩：只记录搜索词，不触网 ─────────────────────────────────────

class FakeSource:
    def __init__(self):
        self.queries: list[str] = []

    async def search(self, query, max_sources=2, **kwargs):
        self.queries.append(query)
        return []

    async def close(self):
        pass


class FakeProcessor:
    early_stop = 0

    def __init__(self, processed=None):
        from backend.pipeline.stages import ProcessedContent, SearchResult
        # 默认产出占位正文：让 _run_query 能走到 builder/explore 阶段（搜索为空的场景）
        self._processed = processed if processed is not None else [ProcessedContent(
            source=SearchResult(url="http://example.com/a", title="源", snippet="s"),
            text="正文内容",
        )]

    async def process(self, results):
        return self._processed

    async def close(self):
        pass


class FakeBuilder:
    def __init__(self, cards=None):
        self.cards = cards or []

    async def build(self, processed, display_query=None):
        for c in self.cards:
            yield c


class FakePersister:
    last_skipped = 0

    def __init__(self, store=None):
        self._store = store

    def save(self, cards):
        if self._store:
            return self._store.save_cards(cards)   # 真实落库（与 CardStorePersister 一致）
        return cards


class FakeExplorer:
    def __init__(self, topics=None, tasks=None):
        self.topics = topics or []
        self.tasks = tasks or []

    async def extract_topics(self, card_content, max_count=7, level="default",
                             exclude_title=None, source_title=None, exclude_titles=None):
        return self.topics

    async def explore(self, cards):
        for t in self.tasks:
            yield t

    async def close(self):
        pass


def _pipeline(src, store, builder=None, explorer=None, max_depth=0):
    return Pipeline(
        source=src,
        processor=FakeProcessor(),
        builder=builder or FakeBuilder(),
        persister=FakePersister(store),
        explorer=explorer or FakeExplorer(),
        max_explore_depth=max_depth,
        card_store=store,
    )


# ── 归一化等价组 ────────────────────────────────────────────────────

def test_normalize_title_equivalence():
    from backend.utils.titles import normalize_title
    assert normalize_title("PID控制") == normalize_title("PID 控制")
    assert normalize_title("PID　控制") == normalize_title("PID 控制")       # 全角空格
    assert normalize_title("梯度（深度学习）") == normalize_title("梯度(深度学习)")  # 全/半角括号
    assert normalize_title("Transformer架构") == normalize_title("transformer架构")  # 大小写
    assert normalize_title(" 二阶振荡环节 ") == normalize_title("二阶振荡环节")     # 首尾空白
    assert normalize_title("猎人（Silent）") != normalize_title("猎人")        # 限定词参与比较


# ── find_card_by_normalized_title ───────────────────────────────────

def test_find_card_hit_and_miss():
    from backend.utils.titles import find_card_by_normalized_title
    cards = [_card("PID 控制"), _card("梯度（深度学习）"), _card("二阶过阻尼系统")]
    assert find_card_by_normalized_title(cards, "PID控制").title == "PID 控制"
    assert find_card_by_normalized_title(cards, "梯度(深度学习)") is not None
    assert find_card_by_normalized_title(cards, "梯度") is None            # 括号限定不同 → 不命中
    assert find_card_by_normalized_title(cards, "二阶振荡环节") is None    # 语义近似 → 不命中（交给向量层）
    assert find_card_by_normalized_title([], "任意") is None


def test_title_containment_confirms_variants_rejects_siblings():
    """合并判定用的词面确认：靠「包含关系」，不靠字符相似度。

    实测（bge-small-zh 场景）：
    - 「中枢免疫器官」⊃「免疫器官」= 0.80 → 同概念变体，可合并；
    - 「南印度米食料理」vs「印度食物」= 0.00（含相似度 0.55）→ 不同主题，不合并；
    - 「馕（印度烤饼）」vs「罗提（印度烤饼）」字符相似度高达 0.80，但互不包含
      → 兄弟主题，绝不能合并。
    """
    from backend.utils.titles import title_containment
    assert title_containment("中枢免疫器官", "免疫器官") >= 0.7
    assert title_containment("Transformer架构", "Transformer 架构") == 1.0
    assert title_containment("南印度米食料理", "印度食物") == 0.0
    assert title_containment("馕（印度烤饼）", "罗提（印度烤饼）") == 0.0
    assert title_containment("", "任意") == 0.0


def test_find_card_exclude_source():
    from backend.utils.titles import find_card_by_normalized_title
    src = _card("猎人（杀戮尖塔2）")
    cards = [src]
    assert find_card_by_normalized_title(cards, "猎人（杀戮尖塔2）", exclude_id=src.id) is None
    # 排除源卡后仍有另一张同名卡 → 命中
    other = _card("猎人（杀戮尖塔2）")
    assert find_card_by_normalized_title([src, other], "猎人(杀戮尖塔2)", exclude_id=src.id) is other


# ── extract prompt：已有卡片标题清单（防重复提取） ────────────────────

def test_extract_prompt_lists_existing_titles():
    """已有卡片标题必须进 prompt；不传/传空时不出现该规则块（行为与旧版一致）。"""
    from backend.ai.openai_provider import OpenAIProvider
    from backend.ai.provider import AIConfig
    provider = OpenAIProvider(AIConfig(api_url="http://localhost:9", api_key="k", model="m"))

    p = provider._build_extract_prompt(
        "内容", 5, "default", source_title="印度食物",
        exclude_titles=["坦都炉（印度烤炉）", "南印度料理"],
    )
    assert "已有卡片（禁止重复）" in p
    assert "坦都炉（印度烤炉）" in p and "南印度料理" in p

    p2 = provider._build_extract_prompt("内容", 5, "default", source_title="印度食物")
    assert "已有卡片（禁止重复）" not in p2

    p3 = provider._build_extract_prompt("内容", 5, "default", exclude_titles=[])
    assert "已有卡片（禁止重复）" not in p3


def test_extract_prompt_redirect_follows_search_level():
    """「禁止重复之后改提什么」必须跟随搜索颗粒度，不能写死成子概念。

    前端下拉：默认搜索 / 下级搜索(downstream) / 平级搜索(peer) / 上级搜索(upstream)。
    """
    from backend.ai.openai_provider import OpenAIProvider
    from backend.ai.provider import AIConfig
    provider = OpenAIProvider(AIConfig(api_url="http://localhost:9", api_key="k", model="m"))
    titles = ["坦都炉（印度烤炉）"]

    cases = {
        "downstream": "更深一层的子概念",
        "peer": "同层次的兄弟主题",
        "upstream": "更上层的父概念",
        "default": "不同的具体主题",
    }
    for level, hint in cases.items():
        prompt = provider._build_extract_prompt(
            "内容", 5, level, source_title="印度食物", exclude_titles=titles,
        )
        assert hint in prompt, f"{level} 的改提方向应是「{hint}」"
        assert "已有卡片（禁止重复）" in prompt
        # 与颗粒度自带的层级指令一致（不能两条指令打架）
        if level == "upstream":
            assert "更深一层的子概念" not in prompt
        if level == "downstream":
            assert "更上层的父概念" not in prompt

    # 未知 level 退化为 default 的措辞
    unknown = provider._build_extract_prompt("内容", 5, "weird", exclude_titles=titles)
    assert "不同的具体主题" in unknown


class CapturingExplorer(FakeExplorer):
    """记录下发给模型的已有标题清单。"""

    def __init__(self, topics=None):
        super().__init__(topics=topics)
        self.seen_exclude_titles = None

    async def extract_topics(self, card_content, max_count=7, level="default",
                             exclude_title=None, source_title=None, exclude_titles=None):
        self.seen_exclude_titles = list(exclude_titles or [])
        return list(self.topics)


def test_collect_existing_titles_returns_all_when_under_cap():
    from backend.pipeline.exclusions import collect_existing_titles
    src, a, b = _card("源卡"), _card("主题A"), _card("主题B")
    store = StubStore([src, a, b])
    titles = run(collect_existing_titles(store, focus_text="正文", exclude_id=src.id))
    assert titles == ["主题A", "主题B"]           # 源卡自身不在清单里


def test_collect_existing_titles_caps_prioritizing_children_then_neighbours():
    from backend.pipeline.exclusions import collect_existing_titles
    src = _card("源卡")
    child = _card("子主题")
    child.parent_id = src.id
    near, far = _card("近邻主题"), _card("无关主题")
    store = StubStore([src, far, near, child], similar=[(near.id, 0.4)])

    titles = run(collect_existing_titles(
        store, focus_text="正文", exclude_id=src.id, embedder=StubEmbedder(), cap=2,
    ))
    assert titles == ["子主题", "近邻主题"]        # 子卡优先，其次向量近邻


def test_collect_existing_titles_degrades_to_empty():
    from backend.pipeline.exclusions import collect_existing_titles
    store = StubStore([_card("A")])
    assert run(collect_existing_titles(store, cap=0)) == []      # 关闭
    assert run(collect_existing_titles(None)) == []              # 无 store

    class Boom:
        def list_cards(self):
            raise RuntimeError("db down")

    assert run(collect_existing_titles(Boom())) == []            # 异常不打断流水线


def test_run_expand_sends_existing_titles_to_explorer(tmp_path):
    """run_expand 必须把已有卡片标题（除源卡自身）下发给提取 prompt。"""
    store = _store(tmp_path)
    src_card = _card("印度食物")
    store.create_card(src_card)
    store.create_card(_card("坦都炉（印度烤炉）"))
    store.create_card(_card("南印度料理"))
    explorer = CapturingExplorer(topics=[])
    pipe = _pipeline(FakeSource(), store, explorer=explorer)

    run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))

    assert explorer.seen_exclude_titles is not None
    assert "坦都炉（印度烤炉）" in explorer.seen_exclude_titles
    assert "南印度料理" in explorer.seen_exclude_titles
    assert "印度食物" not in explorer.seen_exclude_titles, "源卡自身不应进清单"


# ── extract prompt 领域锚定（锚定职责前移） ──────────────────────────

def test_extract_prompt_domain_anchoring():
    from backend.ai.openai_provider import OpenAIProvider
    from backend.ai.provider import AIConfig
    provider = OpenAIProvider(AIConfig(api_url="http://localhost:9", api_key="k", model="m"))
    p = provider._build_extract_prompt("内容", 5, "default", source_title="杀戮尖塔2")
    assert "领域锚定" in p
    assert "杀戮尖塔2" in p
    assert "猎人（杀戮尖塔2）" in p          # 具体示例
    assert "裸短词" in p
    p2 = provider._build_extract_prompt("内容", 5, "default")
    assert "领域锚定" not in p2             # 无源卡标题时不注入


# ── _run_query 标题预检 ─────────────────────────────────────────────

def test_run_query_precheck_blocks_search(tmp_path):
    store = _store(tmp_path)
    store.create_card(_card("PID控制"))
    src = FakeSource()
    pipe = _pipeline(src, store)
    events = run(_collect(pipe._run_query("PID 控制", persist=True)))
    assert src.queries == [], "同名命中必须零搜索"
    assert any(getattr(e, "stage", "") == "searching" for e in events)
    assert "跳过搜索" in " ".join(getattr(e, "message", "") for e in events)


def test_run_query_precheck_skipped_when_not_persist(tmp_path):
    """persist=False 豁免：refresh_card 场景显式重新收集同名主题。"""
    store = _store(tmp_path)
    store.create_card(_card("PID控制"))
    src = FakeSource()
    pipe = _pipeline(src, store)
    events = run(_collect(pipe._run_query("PID 控制", persist=False)))
    # 搜索 + 重搜（processed 空时最多 2 次补充）都必须是原 query——refresh 必须放行
    assert src.queries and all(q == "PID 控制" for q in src.queries)


def test_run_query_precheck_no_store(tmp_path):
    """无 card_store 时预检静默跳过（文档流水线等场景）。"""
    src = FakeSource()
    pipe = _pipeline(src, None)
    run(_collect(pipe._run_query("PID 控制", persist=True)))
    assert src.queries and all(q == "PID 控制" for q in src.queries)


# ── run_expand：循环预检 + 无锚定拼接 ───────────────────────────────

def test_run_expand_title_precheck_blocks_topic(tmp_path):
    store = _store(tmp_path)
    src_card = _card("杀戮尖塔2")
    store.create_card(src_card)
    store.create_card(_card("猎人（杀戮尖塔2）"))   # 已有同名卡
    src = FakeSource()
    explorer = FakeExplorer(topics=["猎人（杀戮尖塔2）"])
    pipe = _pipeline(src, store, explorer=explorer)
    events = run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))
    assert src.queries == [], "同名 topic 必须在搜索前拦截"
    assert any(getattr(e, "stage", "") == "complete" for e in events)


def test_run_expand_query_not_anchored(tmp_path):
    """方案 B：搜索词 == topic 原样，不拼接源卡标题。"""
    store = _store(tmp_path)
    src_card = _card("杀戮尖塔2")
    store.create_card(src_card)
    src = FakeSource()
    explorer = FakeExplorer(topics=["猎人（杀戮尖塔2）"])
    pipe = _pipeline(src, store, explorer=explorer)
    events = run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))
    assert src.queries, "topic 应进入搜索"
    # 旧实现: "猎人（杀戮尖塔2） 杀戮尖塔2"；方案 B: 原样直通（含重搜次数）
    assert all(q == "猎人（杀戮尖塔2）" for q in src.queries)


# ── 方案 C：软锚定（短词 + 有源卡才拼接，防 agent 裸短词漂移）────────

def test_soft_anchor_short_word_with_source(tmp_path):
    """短词 + 源卡 → 搜索词软锚定（e2e 实证「Zero」漂移到区块链）。"""
    store = _store(tmp_path)
    src = _card("合金装备5：幻痛")
    store.create_card(src)
    s = FakeSource()
    pipe = _pipeline(s, store)
    run(_collect(pipe._run_query("Zero", source_card_id=src.id)))
    assert s.queries and all(q == "Zero 合金装备5：幻痛" for q in s.queries)


def test_soft_anchor_skipped_long_or_qualified(tmp_path):
    """长词 / 带括号限定词已自包含，不拼接。"""
    store = _store(tmp_path)
    src = _card("合金装备5：幻痛")
    store.create_card(src)
    s1 = FakeSource()
    run(_collect(_pipeline(s1, store)._run_query("软件生命周期", source_card_id=src.id)))
    assert all(q == "软件生命周期" for q in s1.queries)
    s2 = FakeSource()
    run(_collect(_pipeline(s2, store)._run_query("猎人（杀戮尖塔2）", source_card_id=src.id)))
    assert all(q == "猎人（杀戮尖塔2）" for q in s2.queries)


def test_soft_anchor_uses_root_when_no_source(tmp_path):
    """无源卡但有根卡 → 用根卡标题锚定（会话领域=根卡主题）。

    MGSV 建库实证：agent 无源卡裸搜「Zero」→ LayerZero 区块链漂移卡。
    """
    store = _store(tmp_path)
    store.create_card(_card("合金装备5：幻痛"))  # 根卡
    s = FakeSource()
    pipe = _pipeline(s, store)
    run(_collect(pipe._run_query("Zero")))
    assert s.queries and all(q == "Zero 合金装备5：幻痛" for q in s.queries)


def test_soft_anchor_skipped_no_root_and_not_persist(tmp_path):
    """空库（无根卡）不锚定——首建根卡场景；persist=False（refresh）豁免。"""
    store = _store(tmp_path)
    s1 = FakeSource()
    run(_collect(_pipeline(s1, store)._run_query("Zero")))
    assert all(q == "Zero" for q in s1.queries), "空库无领域锚，裸搜"
    store.create_card(_card("合金装备5：幻痛"))
    s2 = FakeSource()
    run(_collect(_pipeline(s2, store)._run_query("Zero", persist=False)))
    assert all(q == "Zero" for q in s2.queries), "refresh 豁免锚定"


def test_precheck_uses_display_query_before_anchor(tmp_path):
    """预检比较原词（display_query）：同名卡存在时锚定前即拦截。"""
    store = _store(tmp_path)
    src = _card("合金装备5：幻痛")
    store.create_card(src)
    store.create_card(_card("Zero"))
    s = FakeSource()
    pipe = _pipeline(s, store)
    run(_collect(pipe._run_query("Zero", source_card_id=src.id, display_query="Zero")))
    assert s.queries == [], "短词同名卡应被预检拦截，不触发锚定搜索"


def test_run_expand_short_topic_soft_anchored(tmp_path):
    """run_expand 短词 topic 也走软锚定（进度显示原词，搜索带锚）。"""
    store = _store(tmp_path)
    src_card = _card("杀戮尖塔2")
    store.create_card(src_card)
    src = FakeSource()
    explorer = FakeExplorer(topics=["毒蛇"])
    pipe = _pipeline(src, store, explorer=explorer)
    run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))
    assert src.queries and all(q == "毒蛇 杀戮尖塔2" for q in src.queries)


# ── run() 全链路：explore 子主题无拼接 ──────────────────────────────

def test_run_query_mount_sets_parent_id(tmp_path):
    """挂载段必须真正写 parent_id（回归：persister.save 标题查重吞更新）。

    e2e 实证：CardStorePersister.save 对已入库卡按标题查重跳过，旧代码
    `self.persister.save([card])` 更新 parent_id 被吞——links 对称正常但
    parent_id 全空。挂载段应走 store.update_parent 直接写库。
    """
    store = _store(tmp_path)
    parent = _card("二阶系统")
    store.create_card(parent)
    src = FakeSource()
    builder = FakeBuilder(cards=[_card("阻尼比")])
    pipe = _pipeline(src, store, builder=builder, max_depth=0)
    run(_collect(pipe.run("二阶系统概述", source_card_id=parent.id)))
    new = [c for c in store.list_cards() if c.title == "阻尼比"]
    assert new, "新卡应生成"
    assert new[0].parent_id == parent.id, "parent_id 必须写库（update_parent）"
    p = store.read_card(parent.id)
    assert new[0].id in p.links and p.id in new[0].links, "无向链接对称"


def test_run_explore_task_not_anchored(tmp_path):
    """方案 B+C：explore 子主题长词原样直通，不拼接父卡标题（短词走软锚定）。"""
    store = _store(tmp_path)
    parent = _card("二阶系统")
    store.create_card(parent)
    src = FakeSource()
    # 顶层生成的卡标题为「阻尼比」；explore 子主题「时域分析方法」（6 字长词，
    # 不触发软锚定）与它不同名，否则子 pipeline 的标题预检会正确拦截。
    builder = FakeBuilder(cards=[_card("阻尼比")])
    explorer = FakeExplorer(tasks=[ExploreTask(query="时域分析方法", parent_card_id=parent.id)])
    pipe = _pipeline(src, store, builder=builder, explorer=explorer, max_depth=1)
    events = run(_collect(pipe.run("二阶系统概述", source_card_id=parent.id)))
    assert any(getattr(e, "stage", "") == "complete" for e in events)
    subtopic_queries = [q for q in src.queries if "时域分析方法" in q]
    assert subtopic_queries, "explore 子主题应进入搜索"
    # 旧实现: "时域分析方法 二阶系统"；方案 B: 长词原样直通（含重搜次数）
    assert all(q == "时域分析方法" for q in subtopic_queries)


# ── run_expand 关键词合并：向量近 ≠ 同一主题 ─────────────────────────

class StubStore:
    """只实现 run_expand 去重路径用到的方法（不依赖 sqlite-vec）。"""

    def __init__(self, cards, similar=None):
        self._cards = {c.id: c for c in cards}
        self._similar = list(similar or [])      # [(card_id, distance)]

    def list_cards(self):
        return list(self._cards.values())

    def read_card(self, card_id):
        return self._cards.get(card_id)

    def get_root_cards(self):
        return list(self._cards.values())

    def vector_search(self, embedding, limit=3, threshold=0.7):
        # 与 SqliteCardStore 一致：只返回 distance < threshold 的前 limit 条
        return [(cid, d) for cid, d in self._similar if d < threshold][:limit]


class StubEmbedder:
    async def encode_one(self, text):
        return [0.0]

    async def encode(self, texts):
        return [[0.0] for _ in texts]


def _expand_pipe(store, explorer, src):
    return Pipeline(
        source=src,
        processor=FakeProcessor(),
        builder=FakeBuilder(),
        persister=FakePersister(),
        explorer=explorer,
        max_explore_depth=0,
        card_store=store,
        embedder=StubEmbedder(),
    )


def test_run_expand_does_not_merge_other_topic_with_close_vector():
    """向量距离 0.364（< 0.40 阈值）但标题无包含关系 → 不同主题，必须建卡。

    实测回归：「南印度米食料理」被已有卡「印度食物」误合并（dist=0.364），
    主题被静默丢掉——bge-small-zh 对「子概念 vs 上位概念」同样给很小的距离。
    """
    src_card = _card("印度食物")
    existing = _card("印度食物")          # 已有卡（与源卡不同 id）
    store = StubStore([src_card, existing], similar=[(existing.id, 0.364)])
    src = FakeSource()
    explorer = FakeExplorer(topics=["南印度米食料理"])
    pipe = _expand_pipe(store, explorer, src)

    events = run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))

    assert "南印度米食料理" in src.queries, "不同主题必须照常搜索建卡"
    assert not any("已覆盖" in getattr(e, "message", "") for e in events)


def test_run_expand_merges_title_variant_even_when_only_vector_is_close():
    """向量近 + 标题包含（「中枢免疫器官」⊃「免疫器官」）→ 判为重复，跳过搜索。"""
    src_card = _card("免疫系统")
    existing = _card("免疫器官")
    store = StubStore([src_card, existing], similar=[(existing.id, 0.352)])
    src = FakeSource()
    explorer = FakeExplorer(topics=["中枢免疫器官"])
    pipe = _expand_pipe(store, explorer, src)

    events = run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))

    assert src.queries == [], "同概念变体应被合并，零搜索"
    assert any("已合并" in getattr(e, "message", "") for e in events)


def test_run_expand_merges_when_vector_is_strictly_close():
    """距离 ≤ MERGE_DISTANCE_STRICT（0.30）→ 语义重复，无需词面确认。"""
    src_card = _card("深度学习")
    existing = _card("反向传播算法")
    store = StubStore([src_card, existing], similar=[(existing.id, 0.25)])
    src = FakeSource()
    explorer = FakeExplorer(topics=["梯度下降法"])
    pipe = _expand_pipe(store, explorer, src)

    events = run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))

    assert src.queries == [], "强语义重复应被合并"
    assert any("已合并" in getattr(e, "message", "") for e in events)


# ── 提取关键词期间的心跳进度 ─────────────────────────────────────────

class SlowExplorer(FakeExplorer):
    """模拟 AI 关键词提取耗时（上游实测 3s~70s 波动）。"""

    def __init__(self, delay=0.35, topics=None):
        super().__init__(topics=topics)
        self.delay = delay

    async def extract_topics(self, card_content, max_count=7, level="default",
                             exclude_title=None, source_title=None, exclude_titles=None):
        await asyncio.sleep(self.delay)
        return list(self.topics)


def test_run_expand_emits_heartbeat_while_extracting(tmp_path, monkeypatch):
    """提取关键词期间必须持续发进度，避免 UI 一直停在 0.1 的「提取关键词」。"""
    from backend.pipeline import pipeline as pipeline_mod

    monkeypatch.setattr(pipeline_mod, "EXTRACT_KEYWORD_HEARTBEAT_SECONDS", 0.1)
    store = _store(tmp_path)
    src_card = _card("源卡")
    store.create_card(src_card)
    pipe = _pipeline(FakeSource(), store, explorer=SlowExplorer(delay=0.35))

    events = run(_collect(pipe.run_expand("源内容", src_card.id, max_topics=5)))

    heartbeats = [e for e in events if "已等待" in getattr(e, "message", "")]
    assert heartbeats, "提取关键词期间必须发心跳进度"
    assert all(getattr(e, "stage", "") == "expanding" for e in heartbeats)
    assert all(getattr(e, "progress", 1) == 0.1 for e in heartbeats)

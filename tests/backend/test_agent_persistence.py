"""P0-2 / P1-1 / P1-2 回归测试：

- AgentSession 持久化异步化：去抖落盘、turn 边界 flush、同步路径兜底
- 树摘要增量缓存：命中/失效
- decide 消息结构：动态上下文合并到末尾 user 消息，历史前缀保持稳定
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import backend.agent.context as ctx

    monkeypatch.setattr(ctx, "_session_dir", lambda u: tmp_path / u)
    monkeypatch.setattr(ctx, "_session_path", lambda u, s: tmp_path / u / f"{s}.json")
    ctx.AgentContext._instance = None
    from backend.agent.tree_cache import TreeSummaryCache

    TreeSummaryCache.instance().clear()
    yield
    TreeSummaryCache.instance().clear()


def _session_file(tmp_path, username: str, session_id: str) -> Path:
    return tmp_path / username / f"{session_id}.json"


# ── P0-2：持久化异步化 ──────────────────────────────────────────────

def test_add_messages_do_not_block_and_debounce_saves(tmp_path):
    """追加消息立即返回（不阻塞），去抖后台任务最终落盘。"""
    from backend.agent.context import AgentSession

    async def run():
        s = AgentSession("u1", "s1")
        t0 = time.monotonic()
        for i in range(5):
            s.add_user_message(f"消息{i}")
        dt = time.monotonic() - t0
        assert dt < 0.05, f"5 次追加应即时返回（异步持久化），实际 {dt:.3f}s"
        assert not _session_file(tmp_path, "u1", "s1").exists(), "去抖窗口内不应立即写盘"
        await asyncio.sleep(0.8)
        assert _session_file(tmp_path, "u1", "s1").exists(), "去抖后应已落盘"
        data = json.loads(_session_file(tmp_path, "u1", "s1").read_text(encoding="utf-8"))
        user_msgs = [m for m in data if m["role"] == "user"]
        assert len(user_msgs) == 5, f"落盘应包含全部 5 条消息: {len(user_msgs)}"

    asyncio.run(run())


def test_flush_persists_immediately(tmp_path):
    """turn 边界 flush：立即落盘，无需等待去抖。"""
    from backend.agent.context import AgentSession

    async def run():
        s = AgentSession("u2", "s2")
        s.add_user_message("你好")
        await s.flush()
        assert _session_file(tmp_path, "u2", "s2").exists(), "flush 后应立即落盘"
        data = json.loads(_session_file(tmp_path, "u2", "s2").read_text(encoding="utf-8"))
        assert data[-1]["content"] == "你好"

    asyncio.run(run())


def test_flush_cancels_pending_debounce(tmp_path):
    """flush 取消去抖等待，不会产生双重写竞争或数据丢失。"""
    from backend.agent.context import AgentSession

    async def run():
        s = AgentSession("u3", "s3")
        s.add_user_message("第一条")
        await asyncio.sleep(0.05)  # 去抖任务已调度但未到 0.5s
        s.add_assistant_message("回答一")
        await s.flush()
        data = json.loads(_session_file(tmp_path, "u3", "s3").read_text(encoding="utf-8"))
        contents = [m["content"] for m in data if m["role"] in ("user", "assistant")]
        assert "第一条" in contents and "回答一" in contents
        # 让被取消的任务充分结束，确认无异常冒泡
        await asyncio.sleep(0.1)

    asyncio.run(run())


def test_sync_context_falls_back_to_direct_save(tmp_path):
    """无事件循环的同步路径：直接同步写盘（保持旧行为）。"""
    from backend.agent.context import AgentSession

    s = AgentSession("u4", "s4")
    s.add_user_message("同步上下文")
    assert _session_file(tmp_path, "u4", "s4").exists(), "同步路径应直接写盘"
    data = json.loads(_session_file(tmp_path, "u4", "s4").read_text(encoding="utf-8"))
    assert data[-1]["content"] == "同步上下文"


def test_prune_marks_dirty_and_saves(tmp_path):
    from backend.agent.context import AgentSession

    async def run():
        s = AgentSession("u5", "s5")
        for i in range(10):
            s.add_user_message(f"m{i}")
        await s.flush()
        s.prune(3)  # 裁到 3 条 → 标记脏并调度落盘
        await s.flush()
        data = json.loads(_session_file(tmp_path, "u5", "s5").read_text(encoding="utf-8"))
        user_msgs = [m for m in data if m["role"] == "user"]
        assert len(user_msgs) == 3, f"prune 后应只保留 3 条: {len(user_msgs)}"

    asyncio.run(run())


# ── P1-2：树摘要增量缓存 ────────────────────────────────────────────

def test_tree_cache_hit_and_invalidate():
    from backend.agent.tree_cache import TreeSummaryCache

    cache = TreeSummaryCache.instance()
    calls = {"n": 0}

    def builder():
        calls["n"] += 1
        return f"tree-{calls['n']}"

    assert cache.get("u", "s", builder) == "tree-1"
    assert calls["n"] == 1
    assert cache.get("u", "s", builder) == "tree-1", "命中缓存不应重建"
    assert calls["n"] == 1
    cache.invalidate("u", "s")
    assert cache.get("u", "s", builder) == "tree-2", "失效后应重建"
    assert calls["n"] == 2
    # 不同会话互不影响
    assert cache.get("u", "s2", builder) == "tree-3"


def test_tree_cache_bounded_eviction():
    from backend.agent.tree_cache import TreeSummaryCache

    cache = TreeSummaryCache.instance()
    cache.clear()
    for i in range(130):
        cache.get("u", f"s{i}", lambda i=i: f"v{i}")
    with cache._lock:
        size = len(cache._store)
    assert size <= 128, f"缓存应受限: {size}"


# ── P1-1：稳定前缀 + 动态内容放末尾 ─────────────────────────────────

def test_decision_messages_dynamic_content_at_tail(tmp_path):
    """动态上下文（树摘要/最新指令）合并为末尾 user 消息，不插在历史中间。"""
    from backend.agent.context import AgentSession
    from backend.agent.loop import _build_decision_messages

    async def run():
        s = AgentSession("u6", "s6")
        s.add_user_message("用户问题")
        s.add_assistant_message("第一轮回答")
        msgs = _build_decision_messages(s, "最新指令XYZ", "树摘要ABC")
        # 前缀 = [system, user, assistant] 原样保留
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user" and msgs[1]["content"] == "用户问题"
        assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "第一轮回答"
        # 动态内容全部在最后一条 user 消息里
        assert msgs[-1]["role"] == "user"
        assert "树摘要ABC" in msgs[-1]["content"] and "最新指令XYZ" in msgs[-1]["content"]
        # 历史中间不允许出现动态 system 注入
        middle = msgs[1:-1]
        assert not any(
            m["role"] == "system" and ("树摘要" in m["content"] or "最新指令" in m["content"])
            for m in middle
        ), f"动态上下文不应插入历史中间: {middle}"

    asyncio.run(run())

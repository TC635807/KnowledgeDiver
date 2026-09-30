"""Agent loop 流式输出回归测试。

锁定行为：最终回答必须逐 token 产出多个独立 text SSE 事件（而非单个全量事件），
且完整文本持久化到会话历史。
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent.loop import run_agent_loop  # noqa: E402
from backend.agent.provider import ToolDecision  # noqa: E402


class FakeLLM:
    """模拟流式 LLM：decide_stream 逐 token 产出文本增量后返回决策。"""

    def __init__(self, tokens=("你好", "，", "世界")):
        self._tokens = list(tokens)

    async def decide_stream(self, messages, tools, max_tokens=None):
        for tok in self._tokens:
            yield (None, tok)
        yield (ToolDecision(text="".join(self._tokens)), None)

    async def stream(self, messages, tools, max_tokens=None):
        for tok in self._tokens:
            yield tok


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Agent 会话持久化重定向到临时目录 + 重置单例，避免污染 data/ 与跨测试残留。"""
    import backend.agent.context as ctx

    monkeypatch.setattr(ctx, "_session_dir", lambda u: tmp_path / u)
    monkeypatch.setattr(ctx, "_session_path", lambda u, s: tmp_path / u / f"{s}.json")
    ctx.AgentContext._instance = None


def _sse_events(raw: str) -> list[dict]:
    return [
        json.loads(line[len("data: "):])
        for line in raw.strip().splitlines()
        if line.startswith("data: ")
    ]


def _collect_loop(username: str, session_id: str, message: str) -> str:
    async def _run():
        raw = ""
        async for event in run_agent_loop(username, session_id, message):
            raw += event
        return raw

    return asyncio.run(_run())


def test_final_answer_streams_as_multiple_text_events(monkeypatch, tmp_path):
    """无工具路径：逐 token 流式，禁止单事件全量输出。"""
    monkeypatch.setattr("backend.agent.loop.AgentLLM", lambda: FakeLLM())

    raw = _collect_loop("tester", "sess1", "你好")

    events = _sse_events(raw)
    text_events = [e for e in events if e["type"] == "text"]
    assert len(text_events) >= 3, (
        f"期望逐 token 流式（≥3 个 text 事件），实际 {len(text_events)} 个: {events}"
    )
    contents = "".join(e["data"]["content"] for e in text_events)
    assert contents == "你好，世界"
    assert events[-1]["type"] == "complete"

    # 完整文本已持久化到会话历史（刷新页面可恢复）
    session_file = tmp_path / "tester" / "sess1.json"
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assistant_msgs = [m for m in data if m["role"] == "assistant"]
    assert assistant_msgs and assistant_msgs[-1]["content"] == "你好，世界"


def test_tool_round_summary_streams(monkeypatch):
    """工具路径：工具执行后的强制总结经 _stream_text 逐 token 产出。"""

    class ToolLLM:
        def __init__(self):
            self.decide_calls = 0

        async def decide_stream(self, messages, tools, max_tokens=None):
            # 第一轮返回一个工具调用（无文本增量）
            if self.decide_calls == 0:
                self.decide_calls += 1
                yield (ToolDecision(tool_calls=[], text=None), None)
                return
            yield (ToolDecision(text="直接回答"), None)

        async def stream(self, messages, tools, max_tokens=None):
            for tok in ("总结", "完成"):
                yield tok

    # 空工具调用会导致 _execute_tools 直接结束（无真实执行），走总结路径
    monkeypatch.setattr("backend.agent.loop.AgentLLM", lambda: ToolLLM())

    raw = _collect_loop("tester", "sess2", "搜索一下")

    events = _sse_events(raw)
    text_events = [e for e in events if e["type"] == "text"]
    assert text_events, f"工具轮后应有总结文本事件: {events}"
    assert text_events[-1]["data"]["content"] in ("总结完成", "总结", "完成"), (
        f"总结应流式产出（含 _stream_text 的 token）: {text_events[-1]}"
    )


def test_tool_round_skips_forced_summary(monkeypatch, tmp_path):
    """P0-1 锁定：真实工具轮后不再发起强制总结调用（每轮省 1 次 LLM 往返）。

    第一轮 decide 返回真实工具调用 → 执行工具 → 直接进入第二轮 decide，
    其流式文本即工具后的可见反馈；`stream()`（强制总结路径）不得被调用。
    """
    from backend.agent.schemas import ToolCall

    instances = []

    class ToolLLM:
        def __init__(self):
            self.decide_calls = 0
            instances.append(self)

        async def decide_stream(self, messages, tools, max_tokens=None):
            self.decide_calls += 1
            if self.decide_calls == 1:
                yield (ToolDecision(
                    tool_calls=[ToolCall(id="c1", name="list_cards", args={})],
                    text=None,
                ), None)
                return
            for tok in ("答案", "文本"):
                yield (None, tok)
            yield (ToolDecision(text="答案文本"), None)

        async def stream(self, messages, tools, max_tokens=None):
            raise AssertionError("工具轮后不应再发起强制总结调用（stream 不应被调用）")

    class FakeAPI:
        def __init__(self, username=None, session_id=None):
            self.username = username or "tester"
            self.session_id = session_id or "sess3"

        def get_card_tree(self):
            return []

        def list_cards(self):
            return []

    class FakeExecutor:
        def __init__(self, api):
            self._api = api

        def prepare_task_id(self, name, args):
            return None

        async def execute(self, name, args):
            from backend.agent.schemas import ToolResult
            return ToolResult(tool=name, success=True, summary="ok", data={})

    import backend.agent.loop as loop_mod
    monkeypatch.setattr(loop_mod, "AgentLLM", lambda: ToolLLM())
    monkeypatch.setattr(loop_mod, "PipelineAPI", FakeAPI)
    monkeypatch.setattr(loop_mod, "ToolExecutor", FakeExecutor)

    raw = _collect_loop("tester", "sess3", "查一下卡片")

    events = _sse_events(raw)
    assert events[-1]["type"] == "complete", events
    assert any(e["type"] == "tool_call" for e in events), "应有工具调用事件（可见反馈）"
    assert any(e["type"] == "tool_result" for e in events), "应有工具结果事件（可见反馈）"
    text_events = [e for e in events if e["type"] == "text"]
    assert text_events, "最终回答应由第二轮 decide 流式产出"
    assert instances and instances[0].decide_calls == 2, (
        f"一轮工具应只触发 2 次 decide（工具轮 + 回答），实际 {instances[0].decide_calls if instances else '无实例'}"
    )


def test_loop_mode_skips_forced_summary(monkeypatch, tmp_path):
    """P0-1（loop 模式）：自主迭代同样不再每轮强制总结——下一轮 decide 的文本即总结。"""
    from backend.agent.schemas import ToolCall

    instances = []

    class ToolLLM:
        def __init__(self):
            self.decide_calls = 0
            instances.append(self)

        async def decide_stream(self, messages, tools, max_tokens=None):
            self.decide_calls += 1
            if self.decide_calls == 1:
                yield (ToolDecision(
                    tool_calls=[ToolCall(id="c1", name="list_cards", args={})],
                    text=None,
                ), None)
                return
            for tok in ("总结", "完成"):
                yield (None, tok)
            yield (ToolDecision(text="总结完成"), None)

        async def stream(self, messages, tools, max_tokens=None):
            raise AssertionError("loop 模式工具轮后不应发起强制总结调用")

    class FakeAPI:
        def __init__(self, username=None, session_id=None):
            self.username = username or "tester"
            self.session_id = session_id or "sess5"

        def get_card_tree(self):
            return []

        def list_cards(self):
            return []

    class FakeExecutor:
        def __init__(self, api):
            self._api = api

        def prepare_task_id(self, name, args):
            return None

        async def execute(self, name, args):
            from backend.agent.schemas import ToolResult
            return ToolResult(tool=name, success=True, summary="ok", data={})

    import backend.agent.loop as loop_mod
    monkeypatch.setattr(loop_mod, "AgentLLM", lambda: ToolLLM())
    monkeypatch.setattr(loop_mod, "PipelineAPI", FakeAPI)
    monkeypatch.setattr(loop_mod, "ToolExecutor", FakeExecutor)

    raw = _collect_loop("tester", "sess5", "/loop 持续改进知识库质量")

    events = _sse_events(raw)
    assert events[-1]["type"] == "complete", events
    assert any(e["type"] == "tool_call" for e in events)
    text_events = [e for e in events if e["type"] == "text"]
    assert text_events, "loop 模式的总结文本应由下一轮 decide 流式产出"
    assert instances and instances[0].decide_calls == 2, (
        f"一轮工具应只触发 2 次 decide，实际 {instances[0].decide_calls if instances else '无实例'}"
    )


def test_tool_round_flush_checkpoint_persists(monkeypatch, tmp_path):
    """P0-2 锁定：工具轮后（finally 检查点）历史已落盘，即使没有等待去抖。"""
    from backend.agent.schemas import ToolCall

    class ToolLLM:
        def __init__(self):
            self.decide_calls = 0

        async def decide_stream(self, messages, tools, max_tokens=None):
            self.decide_calls += 1
            if self.decide_calls == 1:
                yield (ToolDecision(
                    tool_calls=[ToolCall(id="c1", name="list_cards", args={})],
                    text=None,
                ), None)
                return
            yield (ToolDecision(text="回答完成"), None)

        async def stream(self, messages, tools, max_tokens=None):
            raise AssertionError("不应触发强制总结")

    class FakeAPI:
        def __init__(self, username=None, session_id=None):
            self.username = username or "tester"
            self.session_id = session_id or "sess4"

        def get_card_tree(self):
            return []

        def list_cards(self):
            return []

    class FakeExecutor:
        def __init__(self, api):
            self._api = api

        def prepare_task_id(self, name, args):
            return None

        async def execute(self, name, args):
            from backend.agent.schemas import ToolResult
            return ToolResult(tool=name, success=True, summary="ok", data={})

    import backend.agent.loop as loop_mod
    monkeypatch.setattr(loop_mod, "AgentLLM", lambda: ToolLLM())
    monkeypatch.setattr(loop_mod, "PipelineAPI", FakeAPI)
    monkeypatch.setattr(loop_mod, "ToolExecutor", FakeExecutor)

    raw = _collect_loop("tester", "sess4", "查一下")

    assert '"complete"' in raw
    # 循环结束后（finally flush）历史必须已落盘
    session_file = tmp_path / "tester" / "sess4.json"
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert any(m.get("tool_calls") for m in data if m["role"] == "assistant"), "工具调用应已持久化"
    assert any(m["role"] == "tool" for m in data), "工具结果应已持久化"
    assert data[-1]["role"] == "assistant" and data[-1]["content"] == "回答完成"

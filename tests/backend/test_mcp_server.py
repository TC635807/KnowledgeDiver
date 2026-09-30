"""MCP 只读 server 回归测试（T15）。

锁定三件事：
1. 暴露集合 == registry 的 read 层（8 个）；write / prescribe 层永不出现；
2. 越权调用永远到不了 executor（白名单 → 实时层级复核 → enabled_tools 三层防御）；
3. JSON-RPC / MCP 语义：initialize、tools/list、tools/call、ping、-32700/-32601/-32602、
   工具失败用 result.isError 表达。
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent import tool_registry as registry  # noqa: E402
from backend.agent.schemas import ToolResult  # noqa: E402
from backend.mcp import runtime  # noqa: E402
from backend.mcp.gateway import (  # noqa: E402
    READ_TOOL_NAMES,
    McpToolError,
    ReadOnlyGateway,
    mcp_tools,
    validate_arguments,
)
from backend.mcp.server import PROTOCOL_VERSIONS, McpServer  # noqa: E402
from backend.mcp.stdio import serve_async  # noqa: E402

WRITE_TOOLS = registry.tools_by_tier(registry.TIER_WRITE)
PRESCRIBE_TOOLS = registry.tools_by_tier(registry.TIER_PRESCRIBE)
EXPECTED_READ_TOOLS = {
    "assess_card_quality", "assess_exploration_need", "assess_knowledge_base",
    "get_card_info", "get_card_tree", "get_linked_cards", "list_cards",
    "search_similar_cards",
}


def run(coro):
    return asyncio.run(coro)


class FakeExecutor:
    """记录调用的假执行器；默认返回成功的 ToolResult。"""

    def __init__(self, result=None, exc=None):
        self.calls: list[tuple[str, dict]] = []
        self.result = result
        self.exc = exc

    async def execute(self, name: str, args: dict):
        self.calls.append((name, dict(args)))
        if self.exc is not None:
            raise self.exc
        if self.result is not None:
            return self.result
        return ToolResult(tool=name, success=True, summary=f"{name} ok", data={"tool": name})


def make_gateway(executor=None) -> tuple[ReadOnlyGateway, FakeExecutor]:
    executor = executor or FakeExecutor()
    return ReadOnlyGateway(executor), executor


def make_server(executor=None) -> tuple[McpServer, FakeExecutor]:
    gateway, ex = make_gateway(executor)
    return McpServer(gateway), ex


# ── 1. 暴露集合来自 registry，且严格只读 ────────────────────────────

def test_read_tool_names_match_registry_read_tier():
    assert set(READ_TOOL_NAMES) == set(registry.tools_by_tier(registry.TIER_READ))
    assert set(READ_TOOL_NAMES) == EXPECTED_READ_TOOLS
    assert len(READ_TOOL_NAMES) == 8


def test_mcp_tools_are_all_read_tier():
    tools = mcp_tools()
    assert {t["name"] for t in tools} == EXPECTED_READ_TOOLS
    for t in tools:
        assert t["description"], f"{t['name']} 缺少 description"
        assert t["inputSchema"]["type"] == "object"
        assert registry.tool_tier(t["name"]) == registry.TIER_READ


def test_tools_list_schema_matches_registry_parameters():
    gateway, _ = make_gateway()
    by_name = {s["function"]["name"]: s["function"]["parameters"] for s in registry.TOOL_SCHEMAS}
    for tool in gateway.list_tools():
        assert tool["inputSchema"] == by_name[tool["name"]]


def test_write_and_prescribe_tools_are_not_exposed():
    gateway, ex = make_gateway()
    exposed = set(gateway.tool_names())
    for name in (*WRITE_TOOLS, *PRESCRIBE_TOOLS):
        assert name not in exposed
        assert gateway.is_exposed(name) is False
    # 白名单本身也不含任何写层/处方层名字
    assert not (set(READ_TOOL_NAMES) & set(WRITE_TOOLS))
    assert not (set(READ_TOOL_NAMES) & set(PRESCRIBE_TOOLS))
    assert ex.calls == []


@pytest.mark.parametrize("name", sorted((*WRITE_TOOLS, *PRESCRIBE_TOOLS)))
def test_forbidden_tool_call_denied_without_dispatch(name):
    gateway, ex = make_gateway()
    with pytest.raises(McpToolError) as ei:
        run(gateway.call_tool(name, {}))
    assert ei.value.code == -32601
    assert registry.tool_tier(name) in ei.value.message
    # 关键断言：越权调用没有触达 executor
    assert ex.calls == []


def test_gateway_rechecks_tier_even_if_whitelisted(monkeypatch):
    """第 2 层防御：即使名字在只读白名单里，tier 复核为非 read 也必须拒绝。"""
    gateway, ex = make_gateway()
    original = registry.tool_tier

    def fake_tier(name: str) -> str:
        return registry.TIER_WRITE if name == "list_cards" else original(name)

    monkeypatch.setattr(registry, "tool_tier", fake_tier)
    with pytest.raises(McpToolError):
        run(gateway.call_tool("list_cards", {}))
    assert ex.calls == []


def test_gateway_rejects_illegal_tier_at_construction(monkeypatch):
    """导入期断言：若注册表把写层误放进只读导出集，网关直接拒绝启动。"""
    original = registry.schemas_for_tiers

    def fake_schemas(tiers):
        return [s for s in original(tiers)]

    monkeypatch.setattr(registry, "schemas_for_tiers", fake_schemas)
    monkeypatch.setattr(registry, "tool_tier", lambda name: registry.TIER_WRITE)
    with pytest.raises(RuntimeError):
        ReadOnlyGateway(FakeExecutor())


# ── 2. 参数校验 ────────────────────────────────────────────────────

def test_validate_arguments_required_missing():
    gateway, ex = make_gateway()
    with pytest.raises(McpToolError) as ei:
        run(gateway.call_tool("get_card_info", {}))
    assert ei.value.code == -32602
    assert "card_id" in ei.value.message
    assert ex.calls == []


def test_validate_arguments_type_mismatch():
    with pytest.raises(McpToolError) as ei:
        validate_arguments({"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                           {"query": 123})
    assert "类型错误" in ei.value.message


def test_validate_arguments_rejects_bool_for_integer():
    with pytest.raises(McpToolError):
        validate_arguments({"type": "object", "properties": {"limit": {"type": "integer"}}}, {"limit": True})


def test_validate_arguments_unknown_param_rejected():
    with pytest.raises(McpToolError) as ei:
        validate_arguments({"type": "object", "properties": {"card_id": {"type": "string"}}},
                           {"card_id": "a", "unexpected": 1})
    assert "不支持的参数" in ei.value.message


def test_validate_arguments_enum():
    with pytest.raises(McpToolError) as ei:
        validate_arguments(
            {"type": "object", "properties": {"parent": {"type": "string", "enum": ["a", "b"]}}},
            {"parent": "c"},
        )
    assert "取值非法" in ei.value.message


def test_validate_arguments_not_object():
    with pytest.raises(McpToolError):
        validate_arguments({"type": "object", "properties": {}}, ["not", "an", "object"])


def test_defaults_are_filled_before_dispatch():
    gateway, ex = make_gateway()
    run(gateway.call_tool("search_similar_cards", {"query": "图论"}))
    assert ex.calls == [("search_similar_cards", {"query": "图论", "limit": 5, "threshold": 0.35})]


def test_empty_object_tool_accepts_no_args():
    gateway, ex = make_gateway()
    result = run(gateway.call_tool("get_card_tree", {}))
    assert ex.calls == [("get_card_tree", {})]
    assert result.success is True


def test_call_tool_returns_executor_result():
    expected = ToolResult(tool="list_cards", success=True, summary="共 3 张卡", data={"count": 3})
    gateway, _ = make_gateway(FakeExecutor(result=expected))
    assert run(gateway.call_tool("list_cards", {})) is expected


def test_executor_exception_maps_to_internal_error():
    gateway, _ = make_gateway(FakeExecutor(exc=RuntimeError("db down")))
    with pytest.raises(McpToolError) as ei:
        run(gateway.call_tool("list_cards", {}))
    assert ei.value.code == -32603
    assert "db down" in ei.value.message


# ── 3. JSON-RPC / MCP 协议语义 ─────────────────────────────────────

def test_initialize_negotiates_protocol_version():
    server, _ = make_server()
    known = PROTOCOL_VERSIONS[-1]
    resp = run(server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": known, "clientInfo": {"name": "test", "version": "0"}},
    }))
    assert resp["result"]["protocolVersion"] == known
    assert resp["result"]["capabilities"]["tools"] == {"listChanged": False}
    assert resp["result"]["serverInfo"]["name"] == "knowledgediver-readonly"
    assert server.initialized is True


def test_initialize_unknown_version_falls_back_to_latest():
    server, _ = make_server()
    resp = run(server.handle_message({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "1999-01-01"},
    }))
    assert resp["result"]["protocolVersion"] == PROTOCOL_VERSIONS[0]


def test_initialized_notification_has_no_response():
    server, _ = make_server()
    assert run(server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


def test_ping_and_unknown_method():
    server, _ = make_server()
    assert run(server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "ping"}))["result"] == {}
    resp = run(server.handle_message({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}))
    assert resp["error"]["code"] == -32601


def test_invalid_request_forms():
    server, _ = make_server()
    assert run(server.handle_message("not-a-dict"))["error"]["code"] == -32600
    assert run(server.handle_message({"id": 1, "method": "ping"}))["error"]["code"] == -32600
    assert run(server.handle_message({"jsonrpc": "2.0", "id": 1}))["error"]["code"] == -32600


def test_tools_list_response_is_read_only():
    server, _ = make_server()
    resp = run(server.handle_message({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == EXPECTED_READ_TOOLS
    assert not (names & set(WRITE_TOOLS))


def test_tools_call_success_and_failure_semantics():
    good, _ = make_server()
    resp = run(good.handle_message({
        "jsonrpc": "2.0", "id": 5, "method": "tools/call",
        "params": {"name": "list_cards", "arguments": {}},
    }))
    assert resp["result"]["isError"] is False
    assert resp["result"]["content"][0]["type"] == "text"
    assert "list_cards ok" in resp["result"]["content"][0]["text"]

    bad_result = ToolResult(tool="list_cards", success=False, summary="库为空", data={"blocked": True})
    bad, _ = make_server(FakeExecutor(result=bad_result))
    resp = run(bad.handle_message({
        "jsonrpc": "2.0", "id": 6, "method": "tools/call",
        "params": {"name": "list_cards", "arguments": {}},
    }))
    assert resp["result"]["isError"] is True
    assert "库为空" in resp["result"]["content"][0]["text"]


def test_tools_call_write_tool_returns_jsonrpc_error():
    server, ex = make_server()
    resp = run(server.handle_message({
        "jsonrpc": "2.0", "id": 7, "method": "tools/call",
        "params": {"name": "refresh_card", "arguments": {"card_id": "x"}},
    }))
    assert resp["error"]["code"] == -32601
    assert ex.calls == []


def test_tools_call_bad_params_returns_jsonrpc_error():
    server, ex = make_server()
    resp = run(server.handle_message({
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "get_card_info", "arguments": {}},
    }))
    assert resp["error"]["code"] == -32602
    assert ex.calls == []


def test_tools_call_missing_name():
    server, _ = make_server()
    resp = run(server.handle_message({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"arguments": {}},
    }))
    assert resp["error"]["code"] == -32602


# ── 4. stdio 传输 ──────────────────────────────────────────────────

def test_stdio_roundtrip_and_parse_error():
    server, ex = make_server()
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        "",  # 空行忽略
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "get_card_tree", "arguments": {}}}),
        "{ not json",
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "ping"}),
    ]
    stdin = io.StringIO("\n".join(lines) + "\n")
    stdout = io.StringIO()
    handled = run(serve_async(server, stdin, stdout))
    out_lines = [l for l in stdout.getvalue().splitlines() if l.strip()]
    assert handled == 5  # initialize, tools/list, tools/call, parse-error, ping
    parsed = [json.loads(l) for l in out_lines]
    assert all(p["jsonrpc"] == "2.0" for p in parsed)
    assert any(p.get("error", {}).get("code") == -32700 for p in parsed)
    assert parsed[-1]["result"] == {}
    assert ex.calls == [("get_card_tree", {})]


def test_stdio_eof_returns_zero():
    server, _ = make_server()
    assert run(serve_async(server, io.StringIO(""), io.StringIO())) == 0


# ── 5. runtime 组装：第三层兜底（enabled_tools 只含 read） ──────────

def test_build_executor_enables_read_only_and_passes_identity():
    seen: dict = {}

    def api_factory(username, session_id):
        seen["api"] = (username, session_id)
        return object()

    def executor_factory(api, enabled):
        seen["enabled"] = set(enabled)
        seen["api_obj"] = api
        return FakeExecutor()

    executor = runtime.build_executor("alice", "s1", api_factory=api_factory, executor_factory=executor_factory)
    assert isinstance(executor, FakeExecutor)
    assert seen["api"] == ("alice", "s1")
    assert seen["enabled"] == set(READ_TOOL_NAMES)
    assert not (seen["enabled"] & set(WRITE_TOOLS))
    assert not (seen["enabled"] & set(PRESCRIBE_TOOLS))


def test_build_gateway_end_to_end_with_injected_factories():
    def api_factory(username, session_id):
        return object()

    def executor_factory(api, enabled):
        return FakeExecutor()

    gateway = runtime.build_gateway("alice", "s1", api_factory=api_factory, executor_factory=executor_factory)
    assert set(gateway.tool_names()) == EXPECTED_READ_TOOLS


def test_resolve_identity_from_env_and_required(monkeypatch):
    monkeypatch.setenv("KD_MCP_USERNAME", "bob")
    monkeypatch.setenv("KD_MCP_SESSION", "s2")
    assert runtime.resolve_identity() == ("bob", "s2")
    assert runtime.resolve_identity("carol", None) == ("carol", "s2")  # 显式参数优先
    monkeypatch.delenv("KD_MCP_USERNAME")
    with pytest.raises(runtime.McpConfigError):
        runtime.resolve_identity(None, None)


def test_server_uses_gateway_without_extra_tools():
    """端到端只读断言：任何写层名字既不出现在 tools/list，也无法 call。"""
    server, ex = make_server()
    listed = {t["name"] for t in run(server.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))["result"]["tools"]}
    assert listed == EXPECTED_READ_TOOLS
    for name in WRITE_TOOLS:
        resp = run(server.handle_message({
            "jsonrpc": "2.0", "id": 99, "method": "tools/call",
            "params": {"name": name, "arguments": {}},
        }))
        assert "error" in resp
    assert ex.calls == []

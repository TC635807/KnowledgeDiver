"""MCP JSON-RPC 2.0 方法处理（协议层，与传输解耦）。

支持 MCP 的最小可用子集：
  initialize                 → 协议版本协商 + capabilities(tools) + serverInfo
  notifications/initialized  → 通知，无响应
  ping                       → {}
  tools/list                 → 只读工具描述（来自 gateway，registry 派生）
  tools/call                 → 执行只读工具

错误语义（与 MCP 一致）：
  * 协议/参数/越权错误 → JSON-RPC error（-32601/-32602/-32603）
  * 工具自身执行失败   → result.isError = true + 文本内容（让模型看到原因，可自我修正）

协议版本从已安装的官方 SDK 动态读取（未安装则用内置列表），保证与客户端协商一致。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.agent import tool_registry as registry
from backend.agent import untrusted
from backend.mcp.gateway import (
    ERR_INVALID_PARAMS,
    ERR_METHOD_NOT_FOUND,
    McpToolError,
    ReadOnlyGateway,
)

logger = logging.getLogger(__name__)

SERVER_NAME = "knowledgediver-readonly"
SERVER_VERSION = "0.1.0"

# 官方 SDK 可用时以其 LATEST_PROTOCOL_VERSION 为准（本机 mcp 2.2.0 → 2026-07-28）
_BUILTIN_PROTOCOL_VERSIONS: tuple[str, ...] = ("2025-06-18", "2025-03-26", "2024-11-05")


def _detect_protocol_versions() -> tuple[str, ...]:
    try:  # 可选依赖：仅在已安装官方 SDK 时用于版本对齐
        from mcp.types import LATEST_PROTOCOL_VERSION  # type: ignore

        latest = str(LATEST_PROTOCOL_VERSION)
        return (latest, *_BUILTIN_PROTOCOL_VERSIONS)
    except Exception:  # noqa: BLE001 - 无 SDK 时零影响
        return _BUILTIN_PROTOCOL_VERSIONS


PROTOCOL_VERSIONS: tuple[str, ...] = _detect_protocol_versions()
DEFAULT_PROTOCOL_VERSION = PROTOCOL_VERSIONS[0]

ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600


def _result_fields(result: Any) -> tuple[bool, str, Any]:
    """兼容 ToolResult(pydantic/dataclass) 与 dict 两种返回。"""
    if isinstance(result, dict):
        return (
            bool(result.get("success", True)),
            str(result.get("summary") or ""),
            result.get("data"),
        )
    return (
        bool(getattr(result, "success", True)),
        str(getattr(result, "summary", "") or ""),
        getattr(result, "data", None),
    )


def _result_tool(result: Any) -> str:
    """取工具名（ToolResult.tool / dict['tool'] / 未知）。"""
    if isinstance(result, dict):
        return str(result.get("tool") or "")
    return str(getattr(result, "tool", "") or "")


def to_mcp_content(result: Any, wrap: bool | None = None) -> tuple[list[dict], bool]:
    """把工具返回值转成 MCP content 数组 + isError。

    task-44 E4/E5：出站文本跨越 MCP 边界（外部 client 的模型会把它当指令读），
    因此 success 且该工具返回值可能携带外部派生文本时，按入口开关做不可信包裹。
    legacy（默认 inherit + 主开关 legacy）下逐字段不变；wrap 参数仅用于测试/显式覆盖。
    """
    success, summary, data = _result_fields(result)
    text = summary
    if data:
        try:
            rendered = json.dumps(data, ensure_ascii=False, default=str, indent=None)
        except Exception:  # noqa: BLE001
            rendered = str(data)
        text = f"{text}\n\n{rendered}" if text else rendered
    if not text:
        text = "（工具执行完成，无文本输出）"

    tool = _result_tool(result)
    enabled = untrusted.entry_enabled("mcp") or untrusted.entry_enabled("tool_result")
    if wrap is not None:
        enabled = bool(wrap)
    if success and enabled and registry.returns_untrusted_text(tool) and not untrusted.is_wrapped(text):
        text = untrusted.wrap_tool_result_text(text, tool=tool, override=untrusted.ENTRY_ON)

    return [{"type": "text", "text": text}], (not success)


class McpServer:
    """无状态请求处理器；传输由 stdio.py 负责。"""

    def __init__(
        self,
        gateway: ReadOnlyGateway,
        server_name: str = SERVER_NAME,
        server_version: str = SERVER_VERSION,
        protocol_versions: tuple[str, ...] | None = None,
    ) -> None:
        self.gateway = gateway
        self.server_name = server_name
        self.server_version = server_version
        self.protocol_versions = protocol_versions or PROTOCOL_VERSIONS
        self.initialized = False
        self.client_info: dict | None = None

    # ── 响应构造 ──────────────────────────────────────────────
    @staticmethod
    def _ok(msg_id: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    @staticmethod
    def _error(msg_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    # ── 方法实现 ──────────────────────────────────────────────
    def _initialize(self, params: dict) -> dict:
        requested = params.get("protocolVersion")
        version = requested if requested in self.protocol_versions else self.protocol_versions[0]
        self.client_info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else None
        self.initialized = True
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.server_name, "version": self.server_version},
            "instructions": (
                "KnowledgeDiver 只读网关：可语义检索卡片、读取卡片/链接/树结构、诊断质量。"
                "本服务不提供联网搜索或写库能力。"
            ),
        }

    def _tools_list(self) -> dict:
        return {"tools": self.gateway.list_tools()}

    async def _tools_call(self, params: dict, msg_id: Any) -> dict:
        name = params.get("name")
        arguments = params.get("arguments") if "arguments" in params else {}
        if not isinstance(name, str) or not name.strip():
            return self._error(msg_id, ERR_INVALID_PARAMS, "tools/call 缺少字符串参数 name")
        try:
            result = await self.gateway.call_tool(name, arguments)
        except McpToolError as exc:
            return self._error(msg_id, exc.code, exc.message)
        content, is_error = to_mcp_content(result)
        return self._ok(msg_id, {"content": content, "isError": is_error})

    # ── 入口 ──────────────────────────────────────────────────
    async def handle_message(self, message: Any) -> dict | None:
        """处理一条 JSON-RPC 消息；通知（无 id）返回 None。"""
        if not isinstance(message, dict):
            return self._error(None, ERR_INVALID_REQUEST, "请求必须是 JSON 对象")
        msg_id = message.get("id")
        is_notification = "id" not in message or msg_id is None

        if message.get("jsonrpc") != "2.0":
            return None if is_notification else self._error(msg_id, ERR_INVALID_REQUEST, "jsonrpc 必须是 '2.0'")

        method = message.get("method")
        if not isinstance(method, str) or not method:
            return None if is_notification else self._error(msg_id, ERR_INVALID_REQUEST, "缺少 method")

        params = message.get("params")
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return None if is_notification else self._error(msg_id, ERR_INVALID_PARAMS, "params 必须是对象")

        if method == "initialize":
            return None if is_notification else self._ok(msg_id, self._initialize(params))
        if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
            return None
        if method == "ping":
            return None if is_notification else self._ok(msg_id, {})
        if method == "tools/list":
            return None if is_notification else self._ok(msg_id, self._tools_list())
        if method == "tools/call":
            if is_notification:
                return None
            return await self._tools_call(params, msg_id)

        return None if is_notification else self._error(
            msg_id, ERR_METHOD_NOT_FOUND, f"未实现的方法: {method}"
        )

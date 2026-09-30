"""MCP 只读网关核心（协议无关）。

唯一真相源
==========
本模块**不自己维护工具清单**：暴露哪些工具完全由
backend/agent/tool_registry.py 的 `tool_tier()` / `schemas_for_tiers()` 派生：

  TIER_READ     8 个 → 暴露（search_similar_cards / get_card_info / get_linked_cards /
                       list_cards / get_card_tree / assess_card_quality /
                       assess_knowledge_base / assess_exploration_need）
  TIER_PRESCRIBE 1 个 → **不暴露**（plan_knowledge_gaps 会消耗一次 AI 调用）
  TIER_WRITE     4 个 → **不暴露**（联网/写库：search_by_keyword / expand_from_card /
                       refresh_card / link_card）

只读边界有三层，见 ReadOnlyGateway.call_tool：
  1. 白名单：名字必须出现在导入时由 read 层派生的 READ_TOOL_NAMES；
  2. 实时复核：调用时再次 `tool_tier(name) == TIER_READ`（防注册表被改动/误用）；
  3. 执行器兜底：runtime 构造 ToolExecutor 时 `enabled_tools=READ_TOOL_NAMES`，
     即使前两层被绕过，ToolExecutor 仍会拒绝写层工具。
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Protocol, runtime_checkable

from backend.agent import tool_registry as registry

logger = logging.getLogger(__name__)

# 严格只读：不含 prescribe（plan_knowledge_gaps 消耗 AI 调用，不属于"只读"承诺）
READ_TIERS: tuple[str, ...] = (registry.TIER_READ,)
READ_TOOL_NAMES: frozenset[str] = frozenset(registry.tools_by_tier(registry.TIER_READ))
FORBIDDEN_TIERS: frozenset[str] = frozenset({registry.TIER_PRESCRIBE, registry.TIER_WRITE})

# JSON-RPC / MCP 错误码
ERR_INVALID_PARAMS = -32602
ERR_METHOD_NOT_FOUND = -32601
ERR_INTERNAL = -32603


class McpToolError(Exception):
    """工具层错误（映射到 JSON-RPC error）。仅用于协议/参数/权限问题。"""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@runtime_checkable
class ExecutorLike(Protocol):
    """与 backend.agent.tools.ToolExecutor 的最小接口约定。"""

    async def execute(self, name: str, args: dict) -> Any:  # pragma: no cover - Protocol
        ...


def mcp_tools() -> list[dict]:
    """MCP tools/list 描述：由 registry 读层 schema 逐字段派生（name/description/inputSchema）。"""
    tools: list[dict] = []
    for schema in registry.schemas_for_tiers(READ_TIERS):
        fn = schema["function"]
        tools.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            # OpenAI function.parameters 与 MCP inputSchema 同为 JSON Schema 子集，直接复用
            "inputSchema": copy.deepcopy(fn.get("parameters") or {"type": "object", "properties": {}}),
        })
    tools.sort(key=lambda t: t["name"])
    return tools


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "object": (dict,),
    "array": (list,),
}


def _type_ok(value: Any, expected: tuple[type, ...]) -> bool:
    # bool 是 int 的子类：integer/number 不接受 bool
    if isinstance(value, bool) and expected != (bool,):
        return False
    return isinstance(value, expected)


def validate_arguments(schema: dict, arguments: Any) -> dict:
    """按 inputSchema 校验并规整参数：填默认值、拦未知参数、拦类型/枚举错误。

    Raises:
        McpToolError(-32602): 参数不合法。
    """
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise McpToolError(ERR_INVALID_PARAMS, "arguments 必须是 JSON 对象")

    properties: dict = schema.get("properties") or {}
    required: list = list(schema.get("required") or [])

    unknown = sorted(k for k in arguments if k not in properties)
    if unknown:
        raise McpToolError(
            ERR_INVALID_PARAMS,
            f"不支持的参数: {', '.join(unknown)}（可用参数: {', '.join(sorted(properties)) or '无'}）",
        )

    normalized: dict = {}
    for key, spec in properties.items():
        if key in arguments:
            value = arguments[key]
        elif "default" in spec:
            value = spec["default"]
        else:
            continue
        expected = _TYPE_MAP.get(str(spec.get("type") or ""))
        if expected is not None and not _type_ok(value, expected):
            raise McpToolError(
                ERR_INVALID_PARAMS,
                f"参数 {key} 类型错误：期望 {spec.get('type')}，实际 {type(value).__name__}",
            )
        enum_values = spec.get("enum")
        if enum_values and value not in enum_values:
            raise McpToolError(
                ERR_INVALID_PARAMS,
                f"参数 {key} 取值非法：{value!r}，允许值 {enum_values}",
            )
        normalized[key] = value

    missing = [k for k in required if k not in normalized]
    if missing:
        raise McpToolError(ERR_INVALID_PARAMS, f"缺少必填参数: {', '.join(missing)}")
    return normalized


class ReadOnlyGateway:
    """只读工具网关：list_tools() 供 tools/list，call_tool() 供 tools/call。"""

    def __init__(self, executor: ExecutorLike) -> None:
        self._executor = executor
        self._tools: dict[str, dict] = {t["name"]: t for t in mcp_tools()}
        # 导入期断言：白名单必须全部是 read 层（防注册表误改后静默放宽）
        illegal = sorted(n for n in self._tools if registry.tool_tier(n) != registry.TIER_READ)
        if illegal:
            raise RuntimeError(f"只读网关拒绝注册非 read 层工具: {illegal}")

    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def list_tools(self) -> list[dict]:
        return [copy.deepcopy(self._tools[name]) for name in sorted(self._tools)]

    def is_exposed(self, name: str) -> bool:
        return name in self._tools

    def deny_reason(self, name: str) -> str:
        tier = registry.tool_tier(name)
        if tier in FORBIDDEN_TIERS:
            return (
                f"工具 {name} 属于 {tier} 层，只读 MCP 网关不暴露。"
                f"本网关仅提供只读能力（{', '.join(self.tool_names())}）；"
                f"如需联网或写库，请在 KnowledgeDiver 应用内操作。"
            )
        return f"未知工具: {name}。可用工具: {', '.join(self.tool_names())}。"

    async def call_tool(self, name: str, arguments: Any) -> Any:
        """校验 → 分发到 executor。任何越权/未知/参数错误都抛 McpToolError。"""
        if not isinstance(name, str) or not name:
            raise McpToolError(ERR_INVALID_PARAMS, "工具名必须是非空字符串")
        # 第 1 层：白名单
        if name not in self._tools:
            raise McpToolError(ERR_METHOD_NOT_FOUND, self.deny_reason(name))
        # 第 2 层：实时复核层级（注册表若被改动，这里立刻拦下）
        tier = registry.tool_tier(name)
        if tier != registry.TIER_READ:
            raise McpToolError(ERR_METHOD_NOT_FOUND, self.deny_reason(name))

        args = validate_arguments(self._tools[name]["inputSchema"], arguments)
        logger.debug("[mcp] call %s args=%s", name, sorted(args))
        try:
            return await self._executor.execute(name, args)
        except McpToolError:
            raise
        except Exception as exc:  # noqa: BLE001 - 执行异常映射为工具错误，不泄漏栈
            logger.exception("[mcp] 工具执行异常: %s", name)
            raise McpToolError(ERR_INTERNAL, f"工具 {name} 执行异常: {exc}") from exc

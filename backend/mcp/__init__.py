"""KnowledgeDiver MCP 只读网关（T15）。

把知识库的**只读能力**以本地 stdio MCP server 暴露给 Claude Desktop / Cursor：
语义检索、卡片信息/链路、卡片树、质量诊断。写层（search_by_keyword /
expand_from_card / refresh_card / link_card）与处方层（plan_knowledge_gaps）
**永不注册**，由 backend/agent/tool_registry.py 的层级元数据在导入时派生。

模块划分
========
  gateway.py  协议无关核心：工具描述（来自 registry）+ 参数校验 + 调用分发
  server.py   MCP JSON-RPC 2.0 方法处理（initialize / tools/list / tools/call / ping）
  stdio.py    换行分隔 JSON-RPC 的 stdio 传输（stdlib，零新增依赖）
  runtime.py  身份解析 + 真实 ToolExecutor 组装（只读 enabled_tools）
  __main__.py 入口：python -m backend.mcp

为什么手写而不是用官方 mcp SDK
==============================
当前 venv 内已存在官方 mcp 2.2.0，但 requirements.txt 未声明它，且本任务写作用域
不含 requirements.txt。为避免"在干净环境里装不上"的部署风险，这里用标准库实现
MCP 所需的最小 JSON-RPC 子集（换行分隔、initialize/tools/list/tools/call/ping），
零新增依赖；工具描述与调用核心（gateway.py）与传输解耦，日后若切官方 SDK 可直接复用。
"""

from backend.mcp.gateway import (  # noqa: F401
    READ_TOOL_NAMES,
    McpToolError,
    ReadOnlyGateway,
    mcp_tools,
    validate_arguments,
)
from backend.mcp.server import McpServer, PROTOCOL_VERSIONS  # noqa: F401

__all__ = [
    "READ_TOOL_NAMES",
    "McpServer",
    "McpToolError",
    "PROTOCOL_VERSIONS",
    "ReadOnlyGateway",
    "mcp_tools",
    "validate_arguments",
]

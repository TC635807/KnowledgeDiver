"""运行时组装：身份解析 + 真实只读 ToolExecutor。

用法（Claude Desktop / Cursor 由配置传入）：
  python -m backend.mcp --username <KD 用户名> [--session default]
或环境变量 KD_MCP_USERNAME / KD_MCP_SESSION。

安全默认：
  * executor 以 `enabled_tools=READ_TOOL_NAMES`（8 个只读工具）构造 —— 写层/处方层
    连"可执行集合"都不包含，构成第三层兜底；
  * 不修改任何全局开关（不写 os.environ），不新增网络端口，全程本地 stdio。
"""

from __future__ import annotations

import os
from typing import Any, Callable

from backend.config import DEFAULT_SESSION_ID
from backend.mcp.gateway import READ_TOOL_NAMES, ReadOnlyGateway

ENV_USERNAME = "KD_MCP_USERNAME"
ENV_SESSION = "KD_MCP_SESSION"

ApiFactory = Callable[[str, str], Any]
ExecutorFactory = Callable[[Any, set], Any]


class McpConfigError(RuntimeError):
    """配置缺失（如未指定用户名）。"""


def resolve_identity(username: str | None = None, session_id: str | None = None) -> tuple[str, str]:
    resolved_user = (username or os.getenv(ENV_USERNAME) or "").strip()
    if not resolved_user:
        raise McpConfigError(
            f"未指定 KnowledgeDiver 用户名：请用 --username 或设置环境变量 {ENV_USERNAME}。"
            "用户名对应 data/ 下的用户存储目录。"
        )
    resolved_session = (session_id or os.getenv(ENV_SESSION) or DEFAULT_SESSION_ID).strip()
    return resolved_user, resolved_session or DEFAULT_SESSION_ID


def build_executor(
    username: str,
    session_id: str,
    api_factory: ApiFactory | None = None,
    executor_factory: ExecutorFactory | None = None,
) -> Any:
    """构造只读 ToolExecutor。工厂可注入（单测用，避免真实数据库/嵌入模型）。"""
    if api_factory is None:
        def api_factory(u: str, s: str) -> Any:  # type: ignore[misc]
            from backend.pipeline.api import PipelineAPI

            return PipelineAPI(username=u, session_id=s)

    if executor_factory is None:
        def executor_factory(api: Any, enabled: set) -> Any:  # type: ignore[misc]
            from backend.agent.tools import ToolExecutor

            return ToolExecutor(api, enabled_tools=enabled)

    api = api_factory(username, session_id)
    return executor_factory(api, set(READ_TOOL_NAMES))


def build_gateway(
    username: str | None = None,
    session_id: str | None = None,
    api_factory: ApiFactory | None = None,
    executor_factory: ExecutorFactory | None = None,
) -> ReadOnlyGateway:
    user, session = resolve_identity(username, session_id)
    executor = build_executor(user, session, api_factory=api_factory, executor_factory=executor_factory)
    return ReadOnlyGateway(executor)

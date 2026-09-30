"""Agent 模块的数据类型。"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class ToolCall(BaseModel):
    id: str
    name: str
    args: dict[str, Any]


class ToolResult(BaseModel):
    tool: str
    success: bool
    summary: str
    data: Optional[dict[str, Any]] = None


class AgentChatRequest(BaseModel):
    message: str
    session_id: str

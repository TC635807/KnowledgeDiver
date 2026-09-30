"""
会话数据模型。

定义知识收集会话（Session）的数据结构，
每个会话包含一组卡片，支持 CRUD 操作。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


def _validate_name(v: str) -> str:
    """Validate and sanitize session name.

    Strips leading/trailing whitespace, replaces consecutive internal
    whitespace with single spaces, and rejects names that become empty
    or contain only special characters after sanitization.
    """
    if not isinstance(v, str):
        raise ValueError("Session name must be a string")
    v = v.strip()
    v = re.sub(r"\s+", " ", v)
    if not v:
        raise ValueError("Session name cannot be empty")
    if len(v) > 100:
        raise ValueError("Session name must be at most 100 characters")
    return v


class Session(BaseModel):
    """知识收集会话模型。

    会话是卡片的容器，一个会话包含多次收集/展开操作生成的卡片集合。
    """
    id: str                    # UUID 唯一标识
    name: str                  # 会话名称
    created_at: datetime       # 创建时间
    updated_at: datetime       # 更新时间
    card_count: int = 0        # 卡片数量（缓存字段）


class SessionCreate(BaseModel):
    """创建会话请求模型。"""
    name: str = Field(..., min_length=1, max_length=100, description="会话名称，1-100个字符")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)


class SessionUpdate(BaseModel):
    """更新会话请求模型。"""
    name: str = Field(..., min_length=1, max_length=100, description="会话名称，1-100个字符")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_name(v)
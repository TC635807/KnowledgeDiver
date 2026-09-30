"""
Hub 论坛数据模型。

定义论坛分享的会话 manifest、评论、摘要等信息的数据结构，
支持点赞/点踩/评论等社区功能。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class HubComment(BaseModel):
    """Hub 评论模型。"""
    username: str           # 评论者用户名
    content: str            # 评论内容
    created_at: datetime = Field(default_factory=datetime.utcnow)  # 评论时间


class HubManifest(BaseModel):
    """Hub 会话 Manifest 模型。

    存储已分享会话的元数据、图谱结构和社区交互数据（点赞/评论）。
    """
    name: str
    description: str = ""
    creator: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    card_count: int = 0
    topics: List[str] = Field(default_factory=list)
    graph: dict = Field(default_factory=dict)
    likes: int = 0
    dislikes: int = 0
    liked_by: List[str] = Field(default_factory=list)
    disliked_by: List[str] = Field(default_factory=list)
    comments: List[HubComment] = Field(default_factory=list)


class HubSessionSummary(BaseModel):
    """Hub 会话摘要（用于列表展示）。"""
    name: str
    description: str = ""
    creator: str
    created_at: datetime
    updated_at: datetime
    card_count: int = 0
    topics: List[str] = Field(default_factory=list)
    likes: int = 0
    dislikes: int = 0
    comment_count: int = 0


class HubSessionDetail(BaseModel):
    """Hub 会话详情（manifest + 卡片列表）。"""
    manifest: HubManifest
    cards: list = Field(default_factory=list)


class HubCommentCreate(BaseModel):
    """创建评论请求模型。"""
    content: str = Field(..., min_length=1, max_length=2000, description="评论内容，1-2000个字符")

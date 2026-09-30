"""
知识卡片数据模型。

定义 Card 的核心数据结构，包含标题、内容、元数据、
双向链接、来源追踪和 AI 置信度等字段。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Union
from datetime import datetime
import uuid

from pydantic import BaseModel, Field, validator


class Card(BaseModel):
    """知识卡片模型。

    表示知识图谱中的一个节点，包含从网页抓取/AI 生成的内容，
    通过 links/backlinks 实现双向链接，支持树形组织。
    """
    id: str                     # UUID 唯一标识
    title: str                  # 卡片标题
    content: str                # 卡片内容（Markdown 格式）
    metadata: Dict[str, Union[str, int, float, List[str]]]  # 自定义键值对元数据
    links: List[str] = Field(default_factory=list)          # 无向引用链接（对称维护：a↔b 时双方 links 均含对方）
    backlinks: List[str] = Field(default_factory=list)      # 入链镜像（对称维护，与 links 互为补集方向）
    parent_id: Optional[str] = None                          # 树形父卡 ID（显式方向；None/空 = 根卡）
    created_at: datetime                                     # 创建时间
    updated_at: datetime                                     # 更新时间
    sources: List[str] = Field(default_factory=list)         # 来源 URL 列表
    confidence: float                                        # AI 置信度（0.0~1.0）
    tags: List[str] = Field(default_factory=list)            # 标签

    @validator("id")
    def id_must_be_uuid(cls, v: str) -> str:
        """验证卡片 ID 为合法的 UUID 格式。"""
        try:
            uuid.UUID(str(v))
        except Exception:
            raise ValueError("id must be a valid UUID string")
        return v

    @validator("metadata")
    def metadata_munsure(cls, v: Dict[str, Union[str, int, float, List[str]]]) -> Dict[str, Union[str, int, float, List[str]]]:
        """验证 metadata 的 key 为字符串，value 为允许的类型（str/int/float/list[str]）。"""
        for k, val in v.items():
            if not isinstance(k, str):
                raise ValueError("metadata keys must be strings")
            if not isinstance(val, (str, int, float, list)):
                raise ValueError("metadata values must be string, int, float, or list[str]")
            if isinstance(val, list):
                if not all(isinstance(item, str) for item in val):
                    raise ValueError("metadata string lists must contain only strings")
        return v

    @validator("confidence")
    def confidence_in_range(cls, v: float) -> float:
        """验证置信度在 0.0~1.0 范围内。"""
        if not (0.0 <= v <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v

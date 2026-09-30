"""
YAML frontmatter 解析与生成工具函数。

使用 python-frontmatter 库进行 Markdown 文件中 YAML frontmatter
的解析和生成，支持 Card 对象到 frontmatter 的序列化。
"""

from __future__ import annotations

from typing import Dict, Tuple
from datetime import datetime

import frontmatter

from backend.models.card import Card


def parse_frontmatter(content: str) -> Tuple[Dict, str]:
    """从 Markdown 文本中解析 YAML frontmatter。

    Args:
        content: 包含 frontmatter 的 Markdown 文本

    Returns:
        (metadata_dict, body_str) 元组

    Raises:
        ValueError: frontmatter 格式无效
    """
    try:
        post = frontmatter.loads(content)
    except Exception as exc:
        raise ValueError("Invalid frontmatter") from exc

    metadata = getattr(post, "metadata", None)
    body = getattr(post, "content", "")
    if not isinstance(metadata, dict):
        raise ValueError("Invalid frontmatter metadata format")
    return metadata, body


def generate_frontmatter(card: Card) -> str:
    """从 Card 对象生成 YAML frontmatter 字符串。

    Args:
        card: Card 对象

    Returns:
        含 YAML frontmatter 的 Markdown 字符串（--- 分隔）
    """
    metadata: Dict = {
        "id": card.id,
        "title": card.title,
        "links": card.links,
        "backlinks": card.backlinks,
        "created_at": card.created_at.isoformat(),
        "updated_at": card.updated_at.isoformat(),
        "sources": card.sources,
        "confidence": card.confidence,
        "tags": card.tags,
        "metadata": card.metadata,
    }
    post = frontmatter.Post("", **metadata)
    return frontmatter.dumps(post)

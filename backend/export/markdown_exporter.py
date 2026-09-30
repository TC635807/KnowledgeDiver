"""
Markdown 导出模块。

将知识卡片树导出为单页 Markdown 文件，
支持目录生成、元数据、来源引用和链接引用。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

from pydantic import BaseModel

from backend.models import Card
from backend.storage import CardStore


class ExportOptions(BaseModel):
    """导出选项配置。"""
    include_metadata: bool = True         # 是否包含元数据
    include_sources: bool = True          # 是否包含来源链接
    include_confidence: bool = True       # 是否包含置信度
    heading_level_start: int = 1          # 标题起始级别
    include_toc: bool = True              # 是否生成目录


def _slugify(title: str) -> str:
    """将标题转换为 Markdown 锚点 slug。"""
    import re
    s = title.lower()
    s = re.sub(r"[^a-z0-9\s-]", "", s)
    s = re.sub(r"[\s-]+", "-", s)
    return s.strip("-")


class MarkdownExporter:
    """Markdown 导出器。

    将卡片树导出为单页结构化 Markdown 文档，
    支持目录、元数据块、链接引用等功能。
    """

    def __init__(self, card_store: CardStore):
        self.store = card_store

    async def export_tree(
        self, root_id: Optional[str] = None, options: Optional[ExportOptions] = None
    ) -> str:
        """导出完整卡片树为 Markdown。"""
        markdown, _ = await self.export_tree_with_count(root_id=root_id, options=options)
        return markdown

    async def export_tree_with_count(
        self, root_id: Optional[str] = None, options: Optional[ExportOptions] = None
    ) -> tuple[str, int]:
        """导出卡片树并返回 (markdown, 卡片数量) 元组。"""
        if options is None:
            options = ExportOptions()
        
        all_cards = self.store.list_cards()
        all_cards = sorted(all_cards, key=lambda c: c.id)

        id_to_card: Dict[str, Card] = {c.id: c for c in all_cards}
        slug_map: Dict[str, str] = {c.id: _slugify(c.title) for c in all_cards}
        
        md_lines: List[str] = []
        
        if options.include_toc and all_cards:
            md_lines.append("# Table of Contents\n")
            for card in all_cards:
                md_lines.append(f"- [{card.title}](#{slug_map[card.id]})")
            md_lines.append("")
            md_lines.append("---")
            md_lines.append("")
        
        for card in all_cards:
            md_lines.append(self._format_card(card, options.heading_level_start, options, slug_map, id_to_card))
            md_lines.append("")

        markdown = "\n".join(md_lines).strip() + "\n"
        return markdown, len(all_cards)

    async def export_subtree(
        self, card_id: str, options: Optional[ExportOptions] = None
    ) -> str:
        """导出指定卡片为根的子树。"""
        return await self.export_tree(root_id=card_id, options=options)

    async def export_to_file(
        self, file_path: str, root_id: Optional[str] = None, options: Optional[ExportOptions] = None
    ) -> str:
        """导出卡片树到指定文件路径。"""
        markdown, _ = await self.export_tree_with_count(root_id=root_id, options=options)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        return file_path

    def _format_card(
        self,
        card: Card,
        heading_level: int,
        options: ExportOptions,
        slug_map: Dict[str, str],
        id_to_card: Dict[str, Card],
    ) -> str:
        lines: List[str] = []
        header = "#" * max(heading_level, 1) + " " + card.title
        lines.append(header)
        lines.append("")
        
        if options.include_metadata or options.include_sources or options.include_confidence:
            metadata_lines: List[str] = []
            if options.include_metadata:
                if card.metadata:
                    metadata_lines.append(": Metadata: " + ", ".join([f"{k}: {v}" for k, v in card.metadata.items()]))
            if options.include_sources and card.sources:
                metadata_lines.append("Sources: " + ", ".join(card.sources))
            if options.include_confidence and card.confidence is not None:
                metadata_lines.append("Confidence: " + str(card.confidence))
            for md in metadata_lines:
                lines.append(f"> {md}")
            if metadata_lines:
                lines.append("")
        
        if card.content:
            lines.append(card.content)
        
        if card.links:
            link_refs = []
            for link_id in card.links:
                linked_card = id_to_card.get(link_id)
                if linked_card:
                    link_refs.append(f"[{linked_card.title}](#{slug_map.get(link_id, _slugify(link_id))})")
            if link_refs:
                lines.append("")
                lines.append("Related: " + ", ".join(link_refs))
        
        return "\n".join(lines)

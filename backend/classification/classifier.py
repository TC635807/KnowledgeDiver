"""
卡片分类器模块。

使用 AI 将卡片分类到树形结构的父节点下，
提供自动分类建议、手动重分类和树形查询功能。
"""

from __future__ import annotations

import inspect
import json
from typing import List, Optional

from pydantic import BaseModel
from backend.storage import CardStore
from backend.ai import AIProvider
from backend.models import Card
from .prompts import CLASSIFY_PROMPT


class ClassificationSuggestion(BaseModel):
    """分类建议结果。"""
    suggested_parent_id: Optional[str]   # 建议的父卡片 ID
    confidence: float                     # 置信度 0.0~1.0
    reasoning: str                        # 分类理由
    alternative_parents: List[str] = []   # 备选父卡片列表


class CardClassifier:
    """AI 卡片分类器。

    将卡片分类到树形结构中的适当父节点下。
    """

    def __init__(self, card_store: CardStore, ai_provider: AIProvider):
        self.store = card_store
        self.ai = ai_provider

    async def _maybe_await(self, value):
        """兼容同步/异步 store 方法：如果 value 是 awaitable 则 await。"""
        if inspect.isawaitable(value):
            return await value
        return value

    async def _ask_ai(self, prompt: str) -> str:
        """调用 AI（兼容 ask/query 方法名）。"""
        if hasattr(self.ai, "ask"):
            return await self.ai.ask(prompt)
        if hasattr(self.ai, "query"):
            return await self.ai.query(prompt)
        raise AttributeError("AI provider must implement 'ask' or 'query' method.")

    async def suggest_parent(self, card: Card) -> ClassificationSuggestion:
        """建议卡片的最佳父节点（基于 AI 分类）。"""
        candidates: List[Card] = []
        if hasattr(self.store, "get_root_cards"):
            candidates = await self._maybe_await(self.store.get_root_cards())

        # Fallback if no candidates available
        candidate_summaries = [
            {"id": c.id, "title": getattr(c, "title", getattr(c, "name", str(c)))}
            for c in candidates
        ]

        prompt = CLASSIFY_PROMPT.format(
            card_id=getattr(card, "id", ""),
            title=getattr(card, "title", ""),
            content=getattr(card, "content", ""),
            metadata=getattr(card, "metadata", {}),
            candidates=candidate_summaries,
        )

        raw = await self._ask_ai(prompt)
        try:
            data = json.loads(raw)
        except Exception:
            data = {
                "suggested_parent_id": None,
                "confidence": 0.0,
                "reasoning": "AI did not return valid JSON.",
                "alternative_parents": [],
            }

        # Normalize keys
        suggested_parent_id = data.get("suggested_parent_id")
        confidence = float(data.get("confidence", 0.0))
        reasoning = data.get("reasoning", "")
        alternative_parents = data.get("alternative_parents", []) or []

        return ClassificationSuggestion(
            suggested_parent_id=suggested_parent_id,
            confidence=confidence,
            reasoning=reasoning,
            alternative_parents=alternative_parents,
        )

    async def classify_card(
        self, card: Card, auto_apply: bool = False
    ) -> ClassificationSuggestion:
        """分类卡片，可选自动应用分类结果。"""
        suggestion = await self.suggest_parent(card)
        if auto_apply and suggestion.suggested_parent_id:
            # Attempt to apply the suggestion to the store
            if hasattr(self.store, "update_parent"):
                await self._maybe_await(self.store.update_parent(card.id, suggestion.suggested_parent_id))
        return suggestion

    async def reclassify(
        self,
        card_id: str,
        new_parent_id: Optional[str],
    ) -> Card:
        """手动重分类：将卡片移动到新的父节点下。"""
        if hasattr(self.store, "update_parent"):
            await self._maybe_await(self.store.update_parent(card_id, new_parent_id))
            return await self._maybe_await(self.store.get_card(card_id))
        raise AttributeError("CardStore does not support update_parent")

    async def get_tree(self, root_id: Optional[str] = None) -> dict:
        """获取分类树（从根节点递归构建）。"""
        async def _build_node(card: Card) -> dict:
            children = []
            if hasattr(self.store, "get_children"):
                children = await self._maybe_await(self.store.get_children(card.id))
            return {
                "id": getattr(card, "id", ""),
                "title": getattr(card, "title", ""),
                "children": [await _build_node(ch) for ch in children],
            }

        if root_id:
            root = await self._maybe_await(self.store.get_card(root_id))
            return await _build_node(root)

        roots: List[Card] = await self._maybe_await(self.store.get_root_cards())
        if len(roots) == 0:
            return {}
        if len(roots) == 1:
            return await _build_node(roots[0])
        return {
            "id": "root",
            "title": "Root",
            "children": [await _build_node(r) for r in roots],
        }

    async def get_children(self, card_id: str) -> List[Card]:
        """获取指定卡片的直接子卡片。"""
        return await self._maybe_await(self.store.get_children(card_id))

    async def get_descendants(self, card_id: str) -> List[Card]:
        """递归获取指定卡片的所有后代卡片。"""
        result: List[Card] = []

        async def _walk(current_id: str):
            children = await self._maybe_await(self.store.get_children(current_id))
            for c in children:
                result.append(c)
                await _walk(c.id)

        await _walk(card_id)
        return result

    async def get_ancestors(self, card_id: str) -> List[Card]:
        """获取指定卡片的所有祖先卡片（自底向上）。"""
        ancestors: List[Card] = []
        current = await self._maybe_await(self.store.get_card(card_id))
        while getattr(current, "parent_id", None):
            parent_id = current.parent_id
            parent = await self._maybe_await(self.store.get_card(parent_id))
            ancestors.append(parent)
            current = parent
        return ancestors

    async def get_root_cards(self) -> List[Card]:
        """获取所有根节点卡片。"""
        return await self._maybe_await(self.store.get_root_cards())

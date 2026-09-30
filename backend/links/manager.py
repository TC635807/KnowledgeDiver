"""
卡片双向链接管理器模块。

维护卡片间的 links（出链）/ backlinks（入链）对称性，
提供链接创建、移除、循环检测和链接验证功能。
"""

import asyncio
import inspect
from typing import List, Tuple
from pydantic import BaseModel

from backend.storage.base import BaseCardStore
from backend.models import Card


class LinkWarning(BaseModel):
    """链接警告信息。"""
    type: str       # 警告类型："cycle_detected"（循环引用）, "broken_link"（断链）
    message: str    # 警告描述
    card_ids: List[str]  # 涉及的卡片 ID 列表


class LinkManager:
    """卡片链接管理器。

    维护卡片间的双向链接（links + backlinks），
    提供循环检测、断链验证和对称清理功能。
    """

    def __init__(self, card_store: BaseCardStore):
        self.store = card_store

    async def _maybe_await(self, value):
        """兼容同步/异步 store 方法：如果 value 是 awaitable 则 await。"""
        if inspect.isawaitable(value):
            return await value
        return value

    async def _get_card(self, card_id: str):
        """获取卡片（兼容同步/异步 store）。"""
        card = await self._maybe_await(self.store.get_card(card_id))
        return card

    async def _save_card(self, card: Card):
        """保存卡片（兼容同步/异步 store）。"""
        await self._maybe_await(self.store.update_card(card))

    async def create_link(
        self, 
        from_card_id: str, 
        to_card_id: str,
        link_type: str = "reference"  # "reference"（引用）或 "parent"（父子，兼容保留）
    ) -> Tuple[bool, List[LinkWarning]]:
        """创建两个卡片之间的无向链接。

        链接是无向的（Obsidian 风格）：a↔b 时双方 links/backlinks 对称维护，
        create_link(a, b) 与 create_link(b, a) 效果相同。幂等：已存在则无副作用。
        树形父子方向由 Card.parent_id 显式承载，不再从链接推断。

        Args:
            from_card_id: 卡片 A 的 ID
            to_card_id: 卡片 B 的 ID
            link_type: 链接类型（兼容保留，不影响存储）

        Returns:
            (成功标志, 警告列表)
        """
        warnings: List[LinkWarning] = []

        from_card = await self._get_card(from_card_id)
        to_card = await self._get_card(to_card_id)

        if not from_card or not to_card:
            warn = LinkWarning(
                type="broken_link",
                message="One or both cards not found",
                card_ids=[from_card_id, to_card_id],
            )
            return False, [warn]

        # 初始化列表（如果为空）
        if getattr(from_card, 'links', None) is None:
            from_card.links = []  # type: ignore
        if getattr(to_card, 'links', None) is None:
            to_card.links = []  # type: ignore
        if getattr(from_card, 'backlinks', None) is None:
            from_card.backlinks = []  # type: ignore
        if getattr(to_card, 'backlinks', None) is None:
            to_card.backlinks = []  # type: ignore

        changed = False
        for card, other_id in ((from_card, to_card_id), (to_card, from_card_id)):
            if other_id not in card.links:  # type: ignore
                card.links.append(other_id)  # type: ignore
                changed = True
            if other_id not in card.backlinks:  # type: ignore
                card.backlinks.append(other_id)  # type: ignore
                changed = True

        if changed:
            await self._save_card(from_card)
            await self._save_card(to_card)

        return True, warnings

    async def remove_link(
        self,
        from_card_id: str,
        to_card_id: str,
        link_type: str = "reference"
    ) -> bool:
        """移除两个卡片之间的无向链接（对称清理双方 links/backlinks）。"""
        from_card = await self._get_card(from_card_id)
        to_card = await self._get_card(to_card_id)
        if not from_card or not to_card:
            return False

        changed = False
        for card, other_id in ((from_card, to_card_id), (to_card, from_card_id)):
            for field in ("links", "backlinks"):
                values = getattr(card, field, None) or []
                if other_id in values:
                    try:
                        values.remove(other_id)  # type: ignore
                        setattr(card, field, values)
                        changed = True
                    except ValueError:
                        pass

        if changed:
            await self._save_card(from_card)
            await self._save_card(to_card)
            return True
        return False

    async def get_outgoing_links(self, card_id: str) -> list[str]:
        """获取指定卡片的出链 ID 列表。"""
        card = await self._get_card(card_id)
        return list(getattr(card, 'links', []) or [])

    async def get_incoming_links(self, card_id: str) -> list[str]:
        """获取指定卡片的入链 ID 列表。"""
        card = await self._get_card(card_id)
        return list(getattr(card, 'backlinks', []) or [])

    async def detect_cycles(self, card_id: str) -> List[List[str]]:
        """DFS 检测从指定卡片出发的所有循环引用路径。

        Args:
            card_id: 起始卡片 ID

        Returns:
            所有循环路径的列表
        """
        cycles: List[List[str]] = []
        start_card = await self._get_card(card_id)
        if not start_card:
            return cycles

        async def dfs(curr_id: str, path: List[str], visited: set[str]):
            if curr_id in visited:
                return
            visited.add(curr_id)
            curr_card = await self._get_card(curr_id)
            if not curr_card:
                return
            for nxt in getattr(curr_card, 'links', []) or []:
                new_path = path + [nxt]
                if nxt == card_id:
                    cycles.append(new_path + [card_id])
                else:
                    await dfs(nxt, new_path, visited.copy())

        for neighbor in getattr(start_card, 'links', []) or []:
            await dfs(neighbor, [card_id, neighbor], {card_id})
        return cycles

    async def validate_links(self, card_id: str) -> List[LinkWarning]:
        """验证卡片的所有链接目标是否存在，返回断链警告列表。"""
        warnings: List[LinkWarning] = []
        card = await self._get_card(card_id)
        if not card:
            return warnings
        for linked_id in getattr(card, 'links', []) or []:
            linked_card = await self._get_card(linked_id)
            if not linked_card:
                warnings.append(LinkWarning(
                    type="broken_link",
                    message=f"Linked card {linked_id} does not exist",
                    card_ids=[card_id, linked_id],
                ))
        return warnings

    async def _path_exists(self, start_id: str, target_id: str) -> bool:
        return await self._path_exists_impl(start_id, target_id, set())

    async def _path_exists_impl(self, start_id: str, target_id: str, visited: set[str]) -> bool:
        if start_id == target_id:
            return True
        if start_id in visited:
            return False
        visited.add(start_id)
        start_card = await self._get_card(start_id)
        if not start_card:
            return False
        for nxt in getattr(start_card, 'links', []) or []:
            if await self._path_exists_impl(nxt, target_id, visited.copy()):
                return True
        return False

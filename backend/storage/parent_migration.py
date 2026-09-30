"""存量会话 parent_id 回填迁移。

旧版树结构从"links 有向边 + created_at 启发式"推断，无法区分
"Agent re-root（后建父卡挂载先建子卡）"与"旧 keyword-merge 反向污染边"。
本模块把推断结果**固化为显式 parent_id**，之后树方向不再依赖启发式：

规则（对每张卡 C，遍历其 backlinks 候选 P）：
- 边存在性：C.id ∈ P.links（P→C 有边）
- 非互指（C.links ∌ P）：links 方向权威 → C.parent_id = P
- 互指（双向边）：创建更晚者为子（更早的保持根，兼容旧污染数据）
- 无候选 → 保持根（parent_id 为空）

幂等：已设置 parent_id 的卡跳过（二次运行无副作用）。
"""

from __future__ import annotations

import logging
from typing import List, Optional

from backend.models.card import Card

logger = logging.getLogger(__name__)


def infer_parent_ids(cards: List[Card]) -> dict[str, str]:
    """从旧式有向链接推断每张卡的 parent_id。

    Args:
        cards: 会话内全部卡片（含 links/backlinks/created_at）。

    Returns:
        {card_id: parent_id} 映射（只含需要设置 parent_id 的卡）。
    """
    card_map = {c.id: c for c in cards}
    result: dict[str, str] = {}

    for card in cards:
        if card.parent_id:  # 幂等：已有父卡则跳过
            continue
        candidates: List[str] = []
        mutual_candidates: List[str] = []
        for p_id in (card.backlinks or []):
            parent = card_map.get(p_id)
            if parent is None or card.id not in (parent.links or []):
                continue  # 边 P→C 不存在
            if p_id in (card.links or []):
                # 互指：创建更晚者为子（更早的保持根，兼容旧污染）
                if card.created_at > parent.created_at:
                    mutual_candidates.append(p_id)
            else:
                # 非互指：links 方向权威
                candidates.append(p_id)
        if candidates:
            result[card.id] = candidates[0]
        elif mutual_candidates:
            result[card.id] = mutual_candidates[0]
    return result


def migrate_store(store, dry_run: bool = False) -> int:
    """对一个卡牌存储执行 parent_id 回填。

    Args:
        store: 实现了 list_cards/update_card 的存储（SqliteCardStore / InMemoryCardStore）。
        dry_run: 只统计不写入。

    Returns:
        更新的卡片数量。
    """
    cards = store.list_cards()
    mapping = infer_parent_ids(cards)
    updated = 0
    for card in cards:
        parent_id = mapping.get(card.id)
        if parent_id is None:
            continue
        if dry_run:
            updated += 1
            continue
        card.parent_id = parent_id
        store.update_card(card)
        updated += 1
    if updated:
        logger.info("parent_id 迁移完成: %d 张卡片 (%s)", updated, "dry-run" if dry_run else "已写入")
    return updated


def migrate_session(username: str, session_id: str, dry_run: bool = False) -> int:
    """对一个会话的 SQLite 库执行迁移（跳过向量自动索引，避免与关闭竞态）。"""
    from backend.storage.sqlite_card_store import SqliteCardStore

    store = SqliteCardStore(username=username, session_id=session_id, auto_index=False)
    try:
        return migrate_store(store, dry_run=dry_run)
    finally:
        store.close()

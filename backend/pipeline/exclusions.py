"""提取关键词时随 prompt 下发的「已有卡片标题」上下文。

背景：`extract_related_topics` 原先只知道源卡自己的标题，模型会把**库里已有**的主题
当成新主题提出来；随后这些主题被标题预检（find_card_by_normalized_title）和关键词
向量合并逐条丢掉——等于白花一轮「搜索 + 抓取 + 摘要 + 生成」，而且真正的新主题因为
名额被占用而变少（实测 5 个主题里 1~3 个是已有的）。把已有标题直接写进 prompt 后：
  1. 模型不再重复提取已有主题；
  2. max_topics 个名额留给真正的新主题。

总量大时只送最相关的一批（EXTRACT_EXCLUDE_TITLES_MAX，默认 80），选择顺序：
  1. 源卡的直接子卡（parent_id == 源卡）：expand 场景下最可能撞车的对象；
  2. 与源卡内容向量最近的卡片（需要 embedder + 向量索引，取不到就跳过）；
  3. 其余按入库顺序补齐。
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from backend.config import EXTRACT_EXCLUDE_TITLES_MAX

logger = logging.getLogger(__name__)


def _normalized_titles(cards) -> List[str]:
    """按归一化标题去重后返回标题列表（顺序保留）。"""
    from backend.utils.titles import normalize_title

    seen: set = set()
    titles: List[str] = []
    for c in cards:
        title = (getattr(c, "title", "") or "").strip()
        if not title:
            continue
        key = normalize_title(title)
        if key in seen:
            continue
        seen.add(key)
        titles.append(title)
    return titles


async def collect_existing_titles(
    card_store: Any,
    *,
    focus_text: Optional[str] = None,
    exclude_id: Optional[str] = None,
    embedder: Any = None,
    cap: Optional[int] = None,
) -> List[str]:
    """收集「已有卡片标题」，供提取关键词的 prompt 防重复使用。

    Args:
        card_store: SqliteCardStore（需有 list_cards / read_card / 可选 vector_search）
        focus_text: 源卡正文（用于挑最相关的已有卡）
        exclude_id: 源卡自身 id（永不进入清单）
        embedder: Embedder（可选；有向量索引时可挑出最相关的标题）
        cap: 覆盖默认上限（0 表示不下发）

    Returns:
        标题列表；任何异常都退化为 []（绝不打断流水线）。
    """
    limit = EXTRACT_EXCLUDE_TITLES_MAX if cap is None else int(cap)
    if card_store is None or limit <= 0:
        return []

    try:
        cards = card_store.list_cards()
    except Exception as e:  # noqa: BLE001
        logger.warning("[Exclusions] 读取卡片列表失败，跳过已有标题下发: %s", e)
        return []

    cards = [
        c for c in cards
        if getattr(c, "id", None) != exclude_id and (getattr(c, "title", "") or "").strip()
    ]
    if not cards:
        return []
    if len(cards) <= limit:
        return _normalized_titles(cards)

    by_id = {getattr(c, "id", None): c for c in cards}
    picked: List[Any] = []
    seen: set = set()

    def take(card) -> None:
        cid = getattr(card, "id", None)
        if cid is None or cid in seen:
            return
        seen.add(cid)
        picked.append(card)

    # 1) 源卡的直接子卡（最可能语义撞车）
    for c in cards:
        if getattr(c, "parent_id", None) and getattr(c, "parent_id", None) == exclude_id:
            take(c)

    # 2) 向量近邻（取不到索引就静默跳过）
    if focus_text and embedder is not None and len(picked) < limit:
        search = getattr(card_store, "vector_search", None)
        encode_one = getattr(embedder, "encode_one", None)
        if search and encode_one:
            try:
                emb = await encode_one(str(focus_text)[:500])
                for sid, _dist in search(emb, limit=limit * 2, threshold=1.0):
                    card = by_id.get(sid)
                    if card is not None:
                        take(card)
            except Exception as e:  # noqa: BLE001
                logger.warning("[Exclusions] 向量近邻挑选失败，回退按入库顺序: %s", e)

    # 3) 其余按入库顺序补齐
    for c in cards:
        if len(picked) >= limit:
            break
        take(c)

    return _normalized_titles(picked[:limit])

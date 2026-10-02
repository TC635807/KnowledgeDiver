"""把 Hub 公开详情写进**本地**工作区（05-实施契约 §2.6 / 设计 §4.3）。

关键：绝不调用远端 /api/hub/{u}/{s}/import —— 那是把内容复制进**云端账号**的工作区，
本地目录下什么都不会发生（03 文档 §7 的架构级坑）。这里的做法是：

    GET /api/hub/{creator}/{session_name}（远端公开接口，免鉴权）
      -> 本地 SessionStore 建会话 + SqliteCardStore 写卡片

重名自动追加「 (来自 Hub)」或序号；卡片 id 冲突时重新分配；links/backlinks 统一重建。
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Any, Optional

from backend.models.card import Card
from backend.storage.session_store import SessionStore
from backend.storage.sqlite_card_store import SqliteCardStore

logger = logging.getLogger(__name__)

# 重名后缀（设计 §4.3「自动追加后缀 (来自 Hub) 或序号」）
NAME_SUFFIX = " (来自 Hub)"
FALLBACK_SESSION_NAME = "未命名会话"
FALLBACK_CARD_TITLE = "未命名卡片"
MAX_NAME_LENGTH = 100


def sanitize_session_name(raw: Any, fallback: str = FALLBACK_SESSION_NAME) -> str:
    """清洗会话名：折叠空白、去掉路径分隔符与 ..、限长（SessionStore 不做这些校验）。"""
    name = re.sub(r"\s+", " ", str(raw or "").strip())
    name = name.replace("/", "-").replace("\\", "-")
    while ".." in name:
        name = name.replace("..", ".")
    name = name[:MAX_NAME_LENGTH].strip()
    return name or fallback


def unique_session_name(base: str, existing: set[str]) -> str:
    """重名时追加后缀：base -> base (来自 Hub) -> base (来自 Hub) 2 -> ..."""
    if base not in existing:
        return base
    candidate = base + NAME_SUFFIX
    if candidate not in existing:
        return candidate
    index = 2
    while candidate + " " + str(index) in existing:
        index += 1
    return candidate + " " + str(index)


def _valid_uuid(value: Any) -> Optional[str]:
    """Card.id 必须是合法 UUID；非法/缺失视为「需要新 id」。"""
    if value is None or value == "":
        return None
    try:
        return str(uuid.UUID(str(value)))
    except Exception:
        return None


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item)]


def _strip_leading_title(content: str, title: str) -> str:
    """Hub 的 .md 正文常带一行「# 标题」，落地时去掉（与远端 import_session 一致）。"""
    if not content:
        return ""
    stripped = content.lstrip()
    leading = "# " + title
    if stripped.startswith(leading):
        first_line, sep, rest = stripped.partition("\n")
        if sep:
            return rest.lstrip("\n")
        return ""
    return content


def _build_card(payload: dict, new_id: str, now: datetime, source: str) -> Card:
    meta = payload.get("metadata")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta["imported_from"] = source

    title = str(payload.get("title") or "").strip() or FALLBACK_CARD_TITLE
    content = _strip_leading_title(str(payload.get("content") or ""), title)

    try:
        confidence = float(payload.get("confidence") or 1.0)
    except (TypeError, ValueError):
        confidence = 1.0

    parent_id = payload.get("parent_id")
    return Card(
        id=new_id,
        title=title,
        content=content,
        metadata=meta,
        links=[],        # 写完统一重建
        backlinks=[],
        parent_id=str(parent_id) if isinstance(parent_id, str) and parent_id else None,
        created_at=now,
        updated_at=now,
        sources=_str_list(payload.get("sources")),
        confidence=confidence,
        tags=_str_list(payload.get("tags")),
    )


def import_hub_detail(detail: dict, username: str, source: str) -> dict:
    """把 {manifest, cards[]} 写进 username 的本地工作区，返回落盘摘要。

    返回：{session_id, session_name, card_count, renamed}
    """
    manifest = detail.get("manifest") if isinstance(detail.get("manifest"), dict) else {}
    raw_cards = detail.get("cards")
    raw_cards = [c for c in raw_cards if isinstance(c, dict)] if isinstance(raw_cards, list) else []

    store = SessionStore(username=username)
    base_name = sanitize_session_name(manifest.get("name"))
    final_name = unique_session_name(base_name, {s.name for s in store.list_sessions()})
    session = store.create_session(final_name)

    now = datetime.utcnow()

    # 1) 逐张分配 id（重复/非法则新生成）；id_map 只记「旧 id 第一次出现」的映射，供链接解析
    new_ids: list[str] = []
    id_map: dict[str, str] = {}
    used: set[str] = set()
    for payload in raw_cards:
        old_id = _valid_uuid(payload.get("id"))
        new_id = old_id if (old_id and old_id not in used) else str(uuid.uuid4())
        if old_id:
            id_map.setdefault(old_id, new_id)
        used.add(new_id)
        new_ids.append(new_id)

    # 2) 写卡片（先不写 links/backlinks）
    card_store = SqliteCardStore(username=username, session_id=session.id)
    written = 0
    for payload, new_id in zip(raw_cards, new_ids):
        card_store.create_card(_build_card(payload, new_id, now, source))
        written += 1

    # 3) 重建 links/backlinks：邻接集 = 源卡片 links ∪ backlinks（按 id_map 重映射，
    #    只保留本次导入内存在的卡），两侧写入同一集合 —— 与 LinkManager 的无向不变式一致
    neighbors: dict[str, set[str]] = {new_id: set() for new_id in new_ids}
    for payload, new_id in zip(raw_cards, new_ids):
        for key in ("links", "backlinks"):
            for ref in _str_list(payload.get(key)):
                ref_old = _valid_uuid(ref)
                target = id_map.get(ref_old) if ref_old else None
                if target and target != new_id:
                    neighbors.setdefault(new_id, set()).add(target)
                    neighbors.setdefault(target, set()).add(new_id)

    for new_id, refs in neighbors.items():
        card = card_store.get_card(new_id)
        if card is None:
            continue
        ordered = sorted(refs)
        card.links = ordered
        card.backlinks = ordered
        parent_old = _valid_uuid(card.parent_id) if card.parent_id else None
        if parent_old:
            card.parent_id = id_map.get(parent_old)
        card_store.update_card(card)

    store.set_card_count(session.id, written)
    logger.info("[HubImport] %s -> 本地会话 %s（%d 张卡）", source, session.id, written)

    return {
        "session_id": session.id,
        "session_name": final_name,
        "card_count": written,
        "renamed": final_name != base_name,
    }


__all__ = ["import_hub_detail", "sanitize_session_name", "unique_session_name", "NAME_SUFFIX"]

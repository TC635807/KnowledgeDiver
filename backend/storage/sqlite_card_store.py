"""
SQLite 实现的 CardStore。

实现 BaseCardStore 全部 8 个方法 + 3 个 Classifier 兼容方法，
使用 SessionDatabaseManager 操作 per-session .db 文件。
"""
from __future__ import annotations

import json
import logging
import os
import struct
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
from typing import Iterable, List, Optional

from backend.config import DEFAULT_SESSION_ID
from backend.models.card import Card
from backend.storage.base import BaseCardStore
from backend.storage.session_database import SessionDatabaseManager
from backend.storage.session_store import SessionStore


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CARDS_DIR = PROJECT_ROOT / "cards"
_INDEXED_SESSIONS: set[tuple[str, str]] = set()
_logger = logging.getLogger(__name__)


def _encode_blob(embedding: list[float]) -> bytes:
    return struct.pack(f'{len(embedding)}f', *embedding)


class SqliteCardStore(BaseCardStore):
    def __init__(
        self,
        base_dir: Optional[str] = None,
        username: Optional[str] = None,
        session_id: Optional[str] = None,
        auto_index: bool = True,
    ) -> None:
        self.username = username
        self.session_id = session_id or DEFAULT_SESSION_ID
        uname = username or "unknown"
        self.db = SessionDatabaseManager(
            username=uname,
            session_id=self.session_id,
            base_dir=base_dir,
        )
        if base_dir:
            self.base_dir = Path(base_dir)
        elif username:
            self.base_dir = CARDS_DIR / username / self.session_id
        else:
            self.base_dir = CARDS_DIR

        self._vec_lock = threading.Lock()

        if auto_index:
            self._auto_build_vectors(uname)

    def close(self) -> None:
        self.db.close()

    def _auto_build_vectors(self, username: str) -> None:
        key = (username, self.session_id)
        if key in _INDEXED_SESSIONS:
            return
        _INDEXED_SESSIONS.add(key)

        try:
            self.db.conn.execute("SELECT 1 FROM cards_vec LIMIT 0")
        except Exception:
            _logger.debug("Vector extension not available, skipping auto-index")
            return

        map_row = self.db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM card_vec_map"
        ).fetchone()
        card_row = self.db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM cards"
        ).fetchone()
        if not card_row or card_row["cnt"] == 0:
            return
        if map_row and map_row["cnt"] == card_row["cnt"]:
            return

        _logger.info(
            "检测到 %d/%d 张卡未索引 session=%s/%s, 开始增量索引...",
            card_row["cnt"] - (map_row["cnt"] if map_row else 0),
            card_row["cnt"], username, self.session_id,
        )
        t = threading.Thread(target=self._build_vectors_sync, daemon=True)
        t.start()

    def _build_vectors_sync(self) -> None:
        try:
            cards = self.list_cards()
            if not cards:
                return
            mapped = {r[0] for r in self.db.conn.execute(
                "SELECT card_id FROM card_vec_map"
            ).fetchall()}
            missing = [c for c in cards if c.id not in mapped]
            if not missing:
                return

            from backend.ai.embedder import Embedder
            import asyncio

            _logger.info(
                "增量索引 %d 张未索引卡片 %s/%s", len(missing), self.username, self.session_id,
            )
            embedder = Embedder.get()
            texts = [f"{c.title}\n{c.content}" for c in missing]
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            embeddings = loop.run_until_complete(embedder.encode(texts))
            self.index_vectors([c.id for c in missing], embeddings)
            _logger.info("增量索引完成: %d 张卡片", len(missing))
        except Exception as e:
            _logger.warning("自动索引失败: %s", e)

    def _update_session_card_count(self) -> None:
        if not self.username:
            return
        count = self.db.card_count
        session_store = SessionStore(username=self.username)
        session_store.set_card_count(self.session_id, count)

    @staticmethod
    def _serialize_list(value: Optional[list]) -> str:
        if value is None:
            return "[]"
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _serialize_dict(value: Optional[dict]) -> str:
        if value is None:
            return "{}"
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _deserialize_list(raw: Optional[str]) -> list:
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _deserialize_dict(raw: Optional[str]) -> dict:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _row_to_card(row) -> Optional[Card]:
        if row is None:
            return None
        d = dict(row)

        def _parse_dt(dt: Optional[str]) -> datetime:
            if dt is None:
                return datetime.utcnow()
            try:
                return datetime.fromisoformat(dt)
            except (ValueError, TypeError):
                return datetime.utcnow()

        return Card(
            id=d["id"],
            title=d["title"] or "",
            content=d["content"] or "",
            metadata=SqliteCardStore._deserialize_dict(d["metadata"]),
            links=SqliteCardStore._deserialize_list(d["links"]),
            backlinks=SqliteCardStore._deserialize_list(d["backlinks"]),
            parent_id=(d.get("parent_id") or None) if "parent_id" in d else None,
            created_at=_parse_dt(d["created_at"]),
            updated_at=_parse_dt(d["updated_at"]),
            sources=SqliteCardStore._deserialize_list(d["sources"]),
            confidence=float(d["confidence"]) if d["confidence"] is not None else 0.0,
            tags=SqliteCardStore._deserialize_list(d["tags"]),
        )

    def create_card(self, card: Card) -> Card:
        if self.card_exists(card.id):
            raise FileExistsError(f"Card {card.id} already exists")

        self.db.execute(
            """INSERT INTO cards (id, title, content,
               links, backlinks, parent_id, created_at, updated_at, sources, confidence, tags, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                card.id,
                card.title,
                card.content,
                self._serialize_list(card.links),
                self._serialize_list(card.backlinks),
                card.parent_id or "",
                (card.created_at.isoformat() if isinstance(card.created_at, datetime)
                 else card.created_at),
                (card.updated_at.isoformat() if isinstance(card.updated_at, datetime)
                 else card.updated_at),
                self._serialize_list(card.sources),
                card.confidence,
                self._serialize_list(card.tags),
                self._serialize_dict(card.metadata),
            ),
        )
        self.db.commit()
        self._update_session_card_count()
        return card

    def read_card(self, card_id: str) -> Optional[Card]:
        row = self.db.execute(
            "SELECT * FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        return self._row_to_card(row)

    def get_card(self, card_id: str) -> Optional[Card]:
        return self.read_card(card_id)

    def update_card(self, card: Card) -> Card:
        if not self.card_exists(card.id):
            raise FileNotFoundError(f"Card {card.id} does not exist")

        self.db.execute(
            """UPDATE cards SET
               title = ?, content = ?, links = ?, backlinks = ?, parent_id = ?,
               updated_at = ?, sources = ?, confidence = ?, tags = ?, metadata = ?
               WHERE id = ?""",
            (
                card.title,
                card.content,
                self._serialize_list(card.links),
                self._serialize_list(card.backlinks),
                card.parent_id or "",
                (card.updated_at.isoformat() if isinstance(card.updated_at, datetime)
                 else card.updated_at),
                self._serialize_list(card.sources),
                card.confidence,
                self._serialize_list(card.tags),
                self._serialize_dict(card.metadata),
                card.id,
            ),
        )
        self.db.commit()
        return card

    def delete_card(self, card_id: str) -> bool:
        if not self.card_exists(card_id):
            return False
        self.delete_vector(card_id)
        self.db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        self.db.commit()
        self._update_session_card_count()
        return True

    def list_cards(self) -> List[Card]:
        rows = self.db.execute("SELECT * FROM cards").fetchall()
        cards = [self._row_to_card(row) for row in rows]
        return [c for c in cards if c is not None]

    def card_exists(self, card_id: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM cards WHERE id = ?", (card_id,)
        ).fetchone()
        return row is not None

    def save_cards(self, cards: List[Card]) -> List[Card]:
        saved: List[Card] = []
        for card in cards:
            exists = self.card_exists(card.id)
            if exists:
                self.update_card(card)
            else:
                self.create_card(card)
            saved.append(card)
        return saved

    def move_card_ids_to(self, target_store: "SqliteCardStore", card_ids: list[str]) -> int:
        moved = 0
        for card_id in card_ids:
            card = self.read_card(card_id)
            if card is None:
                continue
            if target_store.card_exists(card_id):
                continue
            try:
                target_store.create_card(card)
                self.delete_card(card_id)
                moved += 1
            except Exception as e:
                logger.warning("Card move skipped: %s", e)
                continue
        return moved

    def update_parent(self, card_id: str, new_parent_id: Optional[str]) -> None:
        """设置卡片的树形父卡（显式方向）。

        树方向由 parent_id 显式承载，不再依赖 links 方向或创建时间推断；
        links/backlinks 是无向引用，挂载到新父卡时确保与父卡之间存在无向链接。

        防环：new_parent 的祖先链中不得出现 card_id——树是单向结构，环会让
        get_root_cards() 返回空、整棵树消失（e2e 实证：两张卡互相为父）。
        """
        card = self.read_card(card_id)
        if card is None:
            raise FileNotFoundError(f"Card {card_id} not found")
        if new_parent_id and new_parent_id == card_id:
            raise ValueError("Card cannot be its own parent")
        if new_parent_id:
            new_parent = self.read_card(new_parent_id)
            if new_parent is None:
                raise ValueError(f"Parent card {new_parent_id} not found")
            # 祖先链上溯：new_parent 的父链中出现 card_id 即成环（含间接环）
            cur = new_parent
            while cur:
                if cur.id == card_id:
                    raise ValueError(
                        f"循环父卡: {card_id} 已是 {new_parent_id} 的祖先，"
                        f"拒绝 {card_id} -> {new_parent_id}"
                    )
                cur = self.read_card(cur.parent_id) if cur.parent_id else None
        card.parent_id = new_parent_id or None
        self.update_card(card)
        if new_parent_id:
            # 与父卡建立/保持无向引用链接（对称维护）
            self._ensure_undirected_link(card_id, new_parent_id)

    def _ensure_undirected_link(self, a_id: str, b_id: str) -> None:
        """确保 a↔b 之间存在对称的无向链接（幂等）。"""
        a = self.read_card(a_id)
        b = self.read_card(b_id)
        if a is None or b is None:
            return
        changed = False
        for card, other_id in ((a, b_id), (b, a_id)):
            if other_id not in (card.links or []):
                card.links = (card.links or []) + [other_id]
                changed = True
            if other_id not in (card.backlinks or []):
                card.backlinks = (card.backlinks or []) + [other_id]
                changed = True
        if changed:
            self.update_card(a)
            self.update_card(b)

    def get_children(self, card_id: str) -> List[Card]:
        """直接子卡片：所有 parent_id == card_id 的卡片。

        方向来自显式 parent_id，无创建时间启发式——后建的父卡挂载先建的子卡
        （Agent re-root 场景）也能完整显示，不再丢弃任何已挂载的卡片。
        """
        rows = self.db.execute(
            "SELECT * FROM cards WHERE parent_id = ?", (card_id,)
        ).fetchall()
        children: List[Card] = []
        for row in rows:
            child = self._row_to_card(row)
            if child is not None:
                children.append(child)
        return children

    def get_root_cards(self) -> List[Card]:
        """根卡片：parent_id 为空的卡片（树方向显式，无需链接推断）。"""
        rows = self.db.execute(
            "SELECT * FROM cards WHERE parent_id IS NULL OR parent_id = ''"
        ).fetchall()
        return [c for c in (self._row_to_card(row) for row in rows) if c is not None]

    @staticmethod
    def _is_root(card: Card, card_map: dict) -> bool:
        """根判定（兼容旧调用）：parent_id 为空即为根。"""
        return not (card.parent_id or "")

    # ── 去重 ────────────────────────────────────────────────────────

    def find_by_title(self, title: str) -> Optional[Card]:
        """精确标题匹配——用于保存前查重。"""
        row = self.db.execute(
            "SELECT * FROM cards WHERE title = ?", (title,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_card(row)

    def find_similar(
        self, embedding: list[float], threshold: float = 0.85, limit: int = 3
    ) -> list[tuple[str, float]]:
        """语义相似度查重——复用现有 vector_search，阈值更高（去重比搜索严格）。"""
        return self.vector_search(embedding, limit=limit, threshold=threshold)

    def _vec_ready(self) -> bool:
        try:
            self.db.conn.execute("SELECT 1 FROM cards_vec LIMIT 0")
            return True
        except Exception:
            return False

    def index_vectors(self, card_ids: list[str], embeddings: list[list[float]]):
        if not card_ids or not self._vec_ready():
            return
        with self._vec_lock:
            for cid, emb in zip(card_ids, embeddings):
                old = self.db.conn.execute(
                    "SELECT vec_rowid FROM card_vec_map WHERE card_id = ?", (cid,)
                ).fetchone()
                if old:
                    self.db.conn.execute("DELETE FROM cards_vec WHERE rowid = ?", (old[0],))
                    self.db.conn.execute("DELETE FROM card_vec_map WHERE card_id = ?", (cid,))
                self.db.conn.execute(
                    "INSERT INTO cards_vec(embedding) VALUES (?)",
                    [_encode_blob(emb)],
                )
                new_row = self.db.conn.execute("SELECT last_insert_rowid()").fetchone()
                if new_row and new_row[0]:
                    self.db.conn.execute(
                        "INSERT INTO card_vec_map(card_id, vec_rowid) VALUES (?, ?)",
                        (cid, new_row[0]),
                    )
            self.db.commit()
        _logger.info("已索引 %d 个向量 %s/%s", len(card_ids), self.username, self.session_id)

    def vector_search(self, embedding: list[float], limit: int = 10, threshold: float = 0.7) -> list[tuple[str, float]]:
        if not self._vec_ready():
            _logger.debug("向量搜索跳过: vec表未就绪 %s/%s", self.username, self.session_id)
            return []
        try:
            with self._vec_lock:
                blob = _encode_blob(embedding)
                rows = self.db.conn.execute(
                    "SELECT m.card_id, v.distance "
                    "FROM card_vec_map m JOIN cards_vec v ON m.vec_rowid = v.rowid "
                    "WHERE v.embedding MATCH ? AND k = ? AND v.distance < ? "
                    "ORDER BY v.distance",
                    [blob, limit, threshold],
                ).fetchall()
        except Exception as e:
            _logger.warning("向量搜索失败 %s/%s: %s", self.username, self.session_id, e)
            return []
        _logger.info("向量搜索返回 %d 条结果 %s/%s", len(rows), self.username, self.session_id)
        return [(r[0], r[1]) for r in rows]

    def reindex_all(self) -> int:
        from backend.ai.embedder import Embedder
        import asyncio

        cards = self.list_cards()
        if not cards:
            _logger.info("重建索引跳过: 无卡片 %s/%s", self.username, self.session_id)
            return 0

        _logger.info("重建索引开始: %d 张卡片 %s/%s", len(cards), self.username, self.session_id)
        embedder = Embedder.get()
        texts = [f"{c.title}\n{c.content}" for c in cards]
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        embeddings = loop.run_until_complete(embedder.encode(texts))
        self.index_vectors([c.id for c in cards], embeddings)
        _logger.info("重建索引完成: %d 张卡片 %s/%s", len(cards), self.username, self.session_id)
        return len(cards)

    def delete_vector(self, card_id: str):
        with self._vec_lock:
            row = self.db.conn.execute(
                "SELECT vec_rowid FROM card_vec_map WHERE card_id = ?", (card_id,)
            ).fetchone()
            if row:
                self.db.conn.execute("DELETE FROM cards_vec WHERE rowid = ?", (row[0],))
                self.db.conn.execute("DELETE FROM card_vec_map WHERE card_id = ?", (card_id,))
                self.db.commit()

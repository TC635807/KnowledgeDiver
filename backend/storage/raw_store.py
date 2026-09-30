"""
Raw page store — 保存爬取的原始网页内容到 session 数据库。

每 session 一个 SQLite DB (session.db)，raw_pages 表存储键值：
  url → {title, content, method, fetched_at, metadata}

用法::

    store = RawPageStore(username="alice", session_id="abc123")
    await store.save(url="https://...", title="...", content="...")
    page = store.get(url)
    all_urls = store.list_urls()
    store.close()
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from backend.storage.session_database import SessionDatabaseManager

logger = logging.getLogger(__name__)


class RawPageStore:
    """原始网页内容存储，基于 per-session SQLite。

    每个 URL 一条记录，同 URL 重复保存会覆盖（INSERT OR REPLACE）。
    """

    def __init__(
        self,
        username: str,
        session_id: str,
        base_dir: Optional[str] = None,
    ):
        self.db = SessionDatabaseManager(
            username=username,
            session_id=session_id,
            base_dir=base_dir,
        )

    # ── 写 ────────────────────────────────────────────────

    def save(
        self,
        url: str,
        title: str,
        content: str,
        method: str = "",
        metadata: Optional[dict] = None,
    ) -> None:
        """保存或覆盖一条原始网页记录。"""
        self.db.execute(
            """INSERT OR REPLACE INTO raw_pages (url, title, content, method, fetched_at, metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                url,
                title,
                content,
                method,
                datetime.utcnow().isoformat(),
                json.dumps(metadata or {}, ensure_ascii=False),
            ),
        )
        self.db.commit()
        logger.debug("[RawStore] Saved %s chars for %s", len(content), url[:60])

    # ── 读 ────────────────────────────────────────────────

    def get(self, url: str) -> Optional[dict]:
        """按 URL 获取原始网页内容。"""
        row = self.db.execute(
            "SELECT * FROM raw_pages WHERE url = ?", (url,)
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("metadata"):
            try:
                d["metadata"] = json.loads(d["metadata"])
            except (json.JSONDecodeError, TypeError):
                d["metadata"] = {}
        return d

    def exists(self, url: str) -> bool:
        """检查 URL 是否已抓取过。"""
        row = self.db.execute(
            "SELECT 1 FROM raw_pages WHERE url = ?", (url,)
        ).fetchone()
        return row is not None

    def list_urls(self) -> list[dict]:
        """返回所有已保存的 URL 摘要列表（不含 content）。"""
        rows = self.db.execute(
            "SELECT url, title, method, fetched_at, length(content) AS chars FROM raw_pages ORDER BY fetched_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        """返回已保存的原始网页数量。"""
        row = self.db.execute("SELECT COUNT(*) AS cnt FROM raw_pages").fetchone()
        return row["cnt"] if row else 0

    # ── 删 ────────────────────────────────────────────────

    def delete(self, url: str) -> bool:
        """删除指定 URL 的记录。"""
        self.db.execute("DELETE FROM raw_pages WHERE url = ?", (url,))
        self.db.commit()
        return True

    def clear(self) -> None:
        """清空所有原始网页记录。"""
        self.db.execute("DELETE FROM raw_pages")
        self.db.commit()

    # ── 生命周期 ───────────────────────────────────────────

    def close(self) -> None:
        self.db.close()

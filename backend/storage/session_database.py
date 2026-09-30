"""
Per-session SQLite database manager.

Each session gets its own .db file at cards/{username}/{session_id}/session.db.
This isolates session data and avoids cross-session queries. No username or
session_id columns needed — the file path itself provides the identity.

Unlike DatabaseManager (singleton for global data), each SessionDatabaseManager
instance manages exactly one session's database, with its own connection.
"""

from __future__ import annotations

import logging
import os
import sqlite3

logger = logging.getLogger(__name__)
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


SESSION_CARDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL DEFAULT '',
    links       TEXT DEFAULT '[]',
    backlinks   TEXT DEFAULT '[]',
    parent_id   TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    sources     TEXT DEFAULT '[]',
    confidence  REAL DEFAULT 0.0,
    tags        TEXT DEFAULT '[]',
    metadata    TEXT DEFAULT '{}'
);
"""


SESSION_RAW_SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_pages (
    url         TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    content     TEXT NOT NULL,
    method      TEXT DEFAULT '',
    fetched_at  TEXT NOT NULL,
    metadata    TEXT DEFAULT '{}'
);
"""

SESSION_VECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_vec_map (
    card_id TEXT PRIMARY KEY,
    vec_rowid INTEGER UNIQUE
);
"""


class SessionDatabaseManager:
    """Manages a single session's SQLite database.

    Each instance opens its own connection to a per-session .db file.
    Thread-safe via WAL mode (supports concurrent reads/writes).

    Usage::

        sdb = SessionDatabaseManager(username="alice", session_id="abc123")
        sdb.execute("SELECT * FROM cards WHERE id = ?", ("card1",))
        sdb.commit()
        sdb.close()
    """

    _instances: dict[tuple[str, str], list["SessionDatabaseManager"]] = {}

    @classmethod
    def close_for_session(cls, username: str, session_id: str) -> None:
        """关闭并清理指定会话的所有数据库连接。"""
        key = (username, session_id)
        instances = cls._instances.pop(key, [])
        for inst in instances:
            inst._close_internal()

    def __init__(
        self,
        username: str,
        session_id: str,
        base_dir: Optional[str] = None,
    ) -> None:
        self.username = username
        self.session_id = session_id

        if base_dir:
            root = Path(base_dir)
        else:
            root = PROJECT_ROOT

        self.db_dir = root / "cards" / username / session_id
        self.db_dir.mkdir(parents=True, exist_ok=True)

        self.db_path = self.db_dir / "session.db"
        try:
            self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        except sqlite3.OperationalError as e:
            # 常见原因：目录由 root 创建（上次以 sudo 启动），当前用户无写权限
            st = self.db_dir.stat()
            import pwd
            owner = pwd.getpwuid(st.st_uid).pw_name if st.st_uid != os.getuid() else "current user"
            raise RuntimeError(
                f"无法创建会话数据库: {self.db_path}\n"
                f"  目录归属: {owner} (uid={st.st_uid}), 当前 uid={os.getuid()}\n"
                f"  目录权限: {oct(st.st_mode)[-3:]}\n"
                f"  解决方法: chown -R $(whoami) {self.db_dir}\n"
                f"  原始错误: {e}"
            ) from e
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

        SessionDatabaseManager._instances.setdefault((username, session_id), []).append(self)

    def _init_schema(self) -> None:
        self.conn.executescript(SESSION_CARDS_SCHEMA)
        self.conn.executescript(SESSION_RAW_SCHEMA)
        self.conn.executescript(SESSION_VECTOR_SCHEMA)
        self.conn.commit()
        self._migrate_columns()
        self._init_vector_table()

    def _migrate_columns(self) -> None:
        """为存量库幂等补齐新列（parent_id）。"""
        try:
            self.conn.execute("ALTER TABLE cards ADD COLUMN parent_id TEXT DEFAULT ''")
            self.conn.commit()
            logger.info("Session DB migrated: added parent_id column (%s/%s)", self.username, self.session_id)
        except sqlite3.OperationalError:
            # 列已存在（新库或已迁移过）
            pass

    def _init_vector_table(self) -> None:
        try:
            import sqlite_vec
            self.conn.enable_load_extension(True)
            sqlite_vec.load(self.conn)
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS cards_vec USING vec0("
                "  embedding FLOAT[512] distance_metric=cosine"
                ")"
            )
            self.conn.enable_load_extension(False)
            self.conn.commit()
        except Exception as e:
            logger.warning("Vector extension load failed: %s", e)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq: list) -> sqlite3.Cursor:
        return self.conn.executemany(sql, seq)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        """关闭连接并从实例注册表中移除自身。"""
        instances = SessionDatabaseManager._instances.get((self.username, self.session_id))
        if instances and self in instances:
            instances.remove(self)
        self._close_internal()

    def _close_internal(self) -> None:
        """仅关闭数据库连接，不操作实例注册表。"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __del__(self) -> None:
        self.close()

    @property
    def card_count(self) -> int:
        """Return the number of cards in this session's database."""
        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM cards").fetchone()
        return row["cnt"] if row else 0

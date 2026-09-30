"""
SQLite 数据库连接管理器。

提供线程安全的单例 DatabaseManager，管理全局数据（用户、Hub、分享令牌）。
卡片数据已移至 per-session .db 文件（参见 SessionDatabaseManager）。
WAL 模式支持并发读写。
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Optional

from backend.config import DATABASE_PATH


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    username            TEXT PRIMARY KEY,
    hashed_password     TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    avatar_url          TEXT
);

CREATE TABLE IF NOT EXISTS hub_sessions (
    creator     TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    card_count  INTEGER DEFAULT 0,
    topics      TEXT DEFAULT '[]',
    likes       INTEGER DEFAULT 0,
    dislikes    INTEGER DEFAULT 0,
    liked_by    TEXT DEFAULT '[]',
    disliked_by TEXT DEFAULT '[]',
    comments    TEXT DEFAULT '[]',
    graph       TEXT DEFAULT '{}',
    PRIMARY KEY (creator, name)
);

CREATE TABLE IF NOT EXISTS share_tokens (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS domain_quality (
    domain              TEXT PRIMARY KEY,
    fetch_count         INTEGER NOT NULL DEFAULT 0,
    success_count       INTEGER NOT NULL DEFAULT 0,
    fail_count          INTEGER NOT NULL DEFAULT 0,
    total_content_len   INTEGER NOT NULL DEFAULT 0,
    consecutive_fails   INTEGER NOT NULL DEFAULT 0,
    blocked_until       REAL    NOT NULL DEFAULT 0,
    first_seen          REAL    NOT NULL DEFAULT 0,
    last_updated        REAL    NOT NULL DEFAULT 0,
    last_method         TEXT    NOT NULL DEFAULT ''
);
"""


class DatabaseManager:
    """线程安全单例，管理 SQLite 连接。

    Usage::

        db = DatabaseManager()
        db.execute("SELECT * FROM users WHERE username = ?", ("tc",))
        db.commit()

    所有 Sqlite*Store 共享同一个实例。
    """

    _instance: Optional["DatabaseManager"] = None
    _lock = threading.Lock()

    def __new__(cls, db_path: Optional[str] = None) -> "DatabaseManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize(db_path or DATABASE_PATH)
        return cls._instance

    def _initialize(self, db_path: str) -> None:
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL 模式：读不阻塞写，写不阻塞读，高并发友好
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, seq: list) -> sqlite3.Cursor:
        return self.conn.executemany(sql, seq)

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
        DatabaseManager._instance = None

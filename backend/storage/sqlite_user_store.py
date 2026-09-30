"""
SQLite 实现的 UserStore。

实现 BaseUserStore 全部 6 个方法，使用 DatabaseManager 单例操作 users 表。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from backend.auth.password import get_password_hash, verify_password
from backend.models.user import User
from backend.storage.base import BaseUserStore
from backend.storage.database import DatabaseManager


class SqliteUserStore(BaseUserStore):
    """SQLite 实现的用户存储。

    线程安全（SQLite 行级锁）。
    """

    def __init__(self) -> None:
        self.db = DatabaseManager()

    @staticmethod
    def _row_to_user(row: Optional[dict]) -> Optional[User]:
        if row is None:
            return None
        return User(
            username=row["username"],
            hashed_password=row["hashed_password"],
            created_at=row["created_at"],
            avatar_url=row["avatar_url"],
        )

    def user_exists(self, username: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        return row is not None

    def create_user(self, username: str, password: str) -> User:
        if self.user_exists(username):
            raise ValueError(f"用户名「{username}」已存在")

        hashed_password = get_password_hash(password)
        now = datetime.utcnow().isoformat()

        self.db.execute(
            "INSERT INTO users (username, hashed_password, created_at) VALUES (?, ?, ?)",
            (username, hashed_password, now),
        )
        self.db.commit()

        return User(username=username, hashed_password=hashed_password, created_at=datetime.utcnow())

    def get_user(self, username: str) -> Optional[User]:
        row = self.db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        return self._row_to_user(row)

    def verify_user(self, username: str, password: str) -> Optional[User]:
        user = self.get_user(username)
        if user and verify_password(password, user.hashed_password):
            return user
        return None

    def update_user(self, username: str, **kwargs) -> None:
        if not kwargs:
            return
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        values = list(kwargs.values()) + [username]
        self.db.execute(f"UPDATE users SET {sets} WHERE username = ?", values)
        self.db.commit()

    def get_user_cards_dir(self, username: str) -> str:
        cards_dir = os.path.join(os.getcwd(), "cards", username)
        os.makedirs(cards_dir, exist_ok=True)
        return cards_dir

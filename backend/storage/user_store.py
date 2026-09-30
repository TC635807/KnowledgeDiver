"""
用户存储模块。

管理用户数据的文件系统存储，使用 data/users.json 存储用户信息，
支持用户的 CRUD、密码验证和原地修改。
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from backend.models.user import User
from backend.auth.password import get_password_hash, verify_password
from backend.storage.base import BaseUserStore


class UserStore(BaseUserStore):
    """用户数据存储管理类。

    使用 JSON 文件存储用户信息，提供创建、查询、验证、更新等操作。
    """

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or os.path.join(os.getcwd(), "data")
        self.users_file = os.path.join(self.base_dir, "users.json")
        os.makedirs(self.base_dir, exist_ok=True)
        self._ensure_users_file()

    def _ensure_users_file(self):
        """确保 users.json 文件存在。"""
        if not os.path.exists(self.users_file):
            with open(self.users_file, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def _load_users(self) -> dict:
        if not os.path.exists(self.users_file):
            return {}
        try:
            with open(self.users_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_users(self, users: dict):
        """将所有用户数据写入 JSON 文件。"""
        with open(self.users_file, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2, default=str)

    def user_exists(self, username: str) -> bool:
        """检查用户名是否已存在。"""
        users = self._load_users()
        return username in users

    def create_user(self, username: str, password: str) -> User:
        """创建新用户，密码自动哈希后存储。"""
        if self.user_exists(username):
            raise ValueError(f"用户名「{username}」已存在")

        hashed_password = get_password_hash(password)
        user = User(
            username=username,
            hashed_password=hashed_password,
            created_at=datetime.utcnow()
        )

        users = self._load_users()
        users[username] = user.dict()
        self._save_users(users)

        return user

    def get_user(self, username: str) -> Optional[User]:
        """获取 User 模型实例。"""
        users = self._load_users()
        user_data = users.get(username)
        if user_data:
            return User(**user_data)
        return None

    def verify_user(self, username: str, password: str) -> Optional[User]:
        """验证用户密码，成功返回 User 实例。"""
        user = self.get_user(username)
        if user and verify_password(password, user.hashed_password):
            return user
        return None

    def update_user(self, username: str, **kwargs):
        """更新用户的部分字段（如 avatar_url）。"""
        users = self._load_users()
        if username not in users:
            raise ValueError(f"用户「{username}」不存在")
        users[username].update(kwargs)
        self._save_users(users)

    def get_user_cards_dir(self, username: str) -> str:
        """获取用户的卡片存储目录路径。"""
        cards_dir = os.path.join(os.getcwd(), "cards", username)
        os.makedirs(cards_dir, exist_ok=True)
        return cards_dir

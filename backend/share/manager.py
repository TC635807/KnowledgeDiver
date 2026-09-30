"""
分享令牌管理模块。

使用本地 JSON 文件存储分享令牌（token），
提供创建、验证和吊销令牌的功能。
"""

import json
import uuid
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

SHARE_DIR = Path("backend/share")      # 令牌存储目录
TOKENS_FILE = SHARE_DIR / "tokens.json"  # 令牌文件


class ShareManager:
    """分享令牌管理器。

    基于 JSON 文件的令牌存储，
    支持创建、验证和按用户+会话吊销。
    """

    def __init__(self):
        SHARE_DIR.mkdir(parents=True, exist_ok=True)
        self._ensure_tokens_file()

    def _ensure_tokens_file(self):
        """确保令牌文件存在（不存在则创建空文件）。"""
        if not TOKENS_FILE.exists():
            self._save_tokens({})

    def _load_tokens(self) -> dict:
        """从磁盘加载所有令牌。"""
        if not TOKENS_FILE.exists():
            return {}
        try:
            with open(TOKENS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_tokens(self, tokens: dict):
        """保存所有令牌到磁盘。"""
        with open(TOKENS_FILE, "w", encoding="utf-8") as f:
            json.dump(tokens, f, ensure_ascii=False, indent=2)

    def create_token(self, username: str, session_id: str) -> str:
        """创建分享令牌并保存。

        Args:
            username: 用户名
            session_id: 会话 ID

        Returns:
            生成的 UUID 令牌
        """
        tokens = self._load_tokens()
        token = str(uuid.uuid4())
        tokens[token] = {
            "username": username,
            "session_id": session_id,
            "created_at": datetime.utcnow().isoformat(),
        }
        self._save_tokens(tokens)
        return token

    def validate_token(self, token: str) -> Optional[dict]:
        """验证令牌是否有效，返回令牌信息或 None。"""
        tokens = self._load_tokens()
        return tokens.get(token)

    def revoke_token(self, username: str, session_id: str) -> bool:
        """吊销指定用户和会话的所有令牌。"""
        tokens = self._load_tokens()
        to_remove = []
        for t, info in tokens.items():
            if info["username"] == username and info["session_id"] == session_id:
                to_remove.append(t)
        for t in to_remove:
            del tokens[t]
        if to_remove:
            self._save_tokens(tokens)
        return len(to_remove) > 0

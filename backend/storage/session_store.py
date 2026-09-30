"""
会话存储模块。

管理会话（Session）的文件系统存储，
使用 cards/{username}/sessions.json 存储会话元数据，
每个会话对应 cards/{username}/{session_id}/ 目录存放卡片文件。
"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
import shutil

logger = logging.getLogger(__name__)

from backend.config import DEFAULT_SESSION_ID
from backend.models.session import Session, SessionCreate, SessionUpdate
from backend.storage.base import BaseSessionStore
from backend.storage.session_database import SessionDatabaseManager


class SessionStore(BaseSessionStore):
    """会话存储管理类。

    负责会话的 CRUD 操作、卡片移动和卡片计数维护。
    自动创建默认会话，支持会话名重名检测。
    """

    def __init__(self, username: str):
        self.username = username
        self.cards_dir = Path("cards") / username
        self.sessions_file = self.cards_dir / "sessions.json"
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        self._ensure_default_session()

    def _ensure_default_session(self):
        """确保默认会话和 sessions.json 已初始化。"""
        sessions = self._load_sessions()
        if DEFAULT_SESSION_ID not in sessions:
            default_session = Session(
                id="default",
                name="Default",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
                card_count=0
            )
            sessions[DEFAULT_SESSION_ID] = default_session.model_dump(mode="json")
            self._save_sessions(sessions)

            # 创建默认会话目录
            default_dir = self.cards_dir / "default"
            default_dir.mkdir(exist_ok=True)

    def _load_sessions(self) -> Dict[str, Any]:
        """从 JSON 文件加载所有会话，将字符串日期反序列化为 datetime 对象。"""
        if not self.sessions_file.exists():
            return {}
        try:
            with open(self.sessions_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                for session_id, session_data in data.items():
                    if "created_at" in session_data and isinstance(session_data["created_at"], str):
                        session_data["created_at"] = datetime.fromisoformat(session_data["created_at"])
                    if "updated_at" in session_data and isinstance(session_data["updated_at"], str):
                        session_data["updated_at"] = datetime.fromisoformat(session_data["updated_at"])
                return data
        except Exception as e:
            logger.warning("Session load failed: %s", e)
            return {}

    def _save_sessions(self, sessions: Dict[str, Any]):
        """将会话字典保存到 JSON 文件，自动处理 datetime 序列化。"""
        serializable = {}
        for session_id, session_data in sessions.items():
            if hasattr(session_data, "model_dump"):
                session_data = session_data.model_dump(mode="json")
            elif isinstance(session_data, dict):
                session_data = session_data.copy()
            else:
                continue

            if "created_at" in session_data and isinstance(session_data["created_at"], datetime):
                session_data["created_at"] = session_data["created_at"].isoformat()
            if "updated_at" in session_data and isinstance(session_data["updated_at"], datetime):
                session_data["updated_at"] = session_data["updated_at"].isoformat()

            serializable[session_id] = session_data

        with open(self.sessions_file, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

    def _update_session_card_count(self, session_id: str):
        try:
            sdb = SessionDatabaseManager(username=self.username, session_id=session_id)
            count = sdb.card_count
            sdb.close()
        except Exception as e:
            logger.warning("Card count failed: %s", e)
            count = 0
        self.set_card_count(session_id, count)

    def set_card_count(self, session_id: str, count: int):
        sessions = self._load_sessions()
        if session_id in sessions:
            session_data = sessions[session_id]
            if isinstance(session_data, dict):
                session_data["card_count"] = count
            elif hasattr(session_data, "card_count"):
                session_data.card_count = count
            self._save_sessions(sessions)

    def list_sessions(self) -> List[Session]:
        sessions_dict = self._load_sessions()
        result = []
        for session_id, session_data in sessions_dict.items():
            if isinstance(session_data, dict):
                result.append(Session(**session_data))
            else:
                result.append(session_data)
        return result

    def get_session(self, session_id: str) -> Optional[Session]:
        """根据 ID 获取特定会话。"""
        sessions = self._load_sessions()
        session_data = sessions.get(session_id)
        if session_data is None:
            return None
        if isinstance(session_data, dict):
            return Session(**session_data)
        return session_data

    def create_session(self, name: str) -> Session:
        """创建新会话（自动生成 UUID，检测重名）。"""
        session_id = str(uuid.uuid4())

        # 检查重名
        sessions = self.list_sessions()
        for session in sessions:
            if session.name == name:
                raise ValueError(f"Session with name '{name}' already exists")

        session = Session(
            id=session_id,
            name=name,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            card_count=0
        )

        sessions_dict = self._load_sessions()
        sessions_dict[session_id] = session.model_dump(mode="json")
        self._save_sessions(sessions_dict)

        # 创建会话卡片目录
        session_dir = self.cards_dir / session_id
        session_dir.mkdir(exist_ok=True)

        return session

    def update_session(self, session_id: str, name: str) -> Optional[Session]:
        """更新会话名称（检测重名）。"""
        sessions_dict = self._load_sessions()
        if session_id not in sessions_dict:
            return None

        # 检查其他会话是否已使用该名称
        sessions = self.list_sessions()
        for session in sessions:
            if session.id != session_id and session.name == name:
                raise ValueError(f"Session with name '{name}' already exists")

        session_data = sessions_dict[session_id]
        if isinstance(session_data, dict):
            session_data["name"] = name
            session_data["updated_at"] = datetime.utcnow()
            session = Session(**session_data)
            sessions_dict[session_id] = session.model_dump(mode="json")
        else:
            session_data.name = name
            session_data.updated_at = datetime.utcnow()
            session = session_data
            sessions_dict[session_id] = session.model_dump(mode="json")

        self._save_sessions(sessions_dict)
        return session

    def delete_session(self, session_id: str) -> bool:
        if session_id == DEFAULT_SESSION_ID:
            raise ValueError("default 是系统保留会话，不可删除")

        sessions_dict = self._load_sessions()
        if session_id not in sessions_dict:
            return False

        SessionDatabaseManager.close_for_session(self.username, session_id)

        session_dir = self.cards_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)

        del sessions_dict[session_id]
        self._save_sessions(sessions_dict)

        return True

    def get_session_dir(self, session_id: str) -> Path:
        """获取会话对应的卡片目录路径。"""
        return self.cards_dir / session_id

    def move_cards_to_session(self, source_session_id: str, target_session_id: str, card_ids: List[str]) -> int:
        if source_session_id == target_session_id:
            return 0

        from backend.storage.sqlite_card_store import SqliteCardStore

        source_store = SqliteCardStore(username=self.username, session_id=source_session_id)
        target_store = SqliteCardStore(username=self.username, session_id=target_session_id)
        moved = source_store.move_card_ids_to(target_store, card_ids)
        self._update_session_card_count(source_session_id)
        self._update_session_card_count(target_session_id)
        return moved
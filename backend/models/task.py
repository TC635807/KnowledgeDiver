"""
任务管理器模型。

定义异步任务（Task）的数据结构和 TaskManager 单例，
管理所有后台 running 任务的创建、状态跟踪和取消，
支持 SSE 事件流和断线重连回放。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set

from pydantic import BaseModel

from backend.config import DEFAULT_SESSION_ID
from backend.models import Card

logger = logging.getLogger(__name__)


class TaskType(str, Enum):
    """任务类型枚举。"""
    COLLECT = "collect"          # 关键词收集
    EXPAND = "expand"            # 延申搜索
    REFRESH = "refresh"          # 刷新已有卡片
    DOCUMENT = "document"        # 文档分析
    GAP_DRIVEN = "gap_driven"    # Gap-Driven 探索


class TaskStatus(str, Enum):
    """任务状态枚举。"""
    PENDING = "pending"       # 等待中
    RUNNING = "running"       # 运行中
    COMPLETED = "completed"   # 已完成
    ERROR = "error"           # 出错
    CANCELLED = "cancelled"   # 已取消


class TaskProgress(BaseModel):
    """任务进度数据模型，用于 SSE 推送。"""
    stage: str                        # 当前阶段：searching/scraping/summarizing/generating
    message: str                      # 人类可读的进度描述
    progress: float                   # 进度百分比（0.0~1.0）
    timestamp: str = ""               # 时间戳
    current_item: Optional[str] = None  # 当前处理的项目名称（用于 per-topic 流式面板）
    ai_output: Optional[str] = None   # AI 流式输出内容


class TaskEvent(BaseModel):
    """SSE 事件数据模型。"""
    type: str             # 事件类型：progress/card/error/complete/status
    data: Any             # 事件数据
    timestamp: str = ""   # ISO 8601 时间戳


class Task:
    """单个异步任务。

    维护事件队列、进度状态和已生成卡片列表，
    支持后台 asyncio.Task 绑定和取消。
    """

    def __init__(self, task_id: str, task_type: TaskType, username: str, keyword: str,
                 session_id: str = DEFAULT_SESSION_ID, params: Optional[Dict] = None):
        self.task_id = task_id
        self.task_type = task_type
        self.username = username
        self.keyword = keyword
        self.session_id = session_id
        self.params = params or {}
        self.agent_owned: bool = False  # Agent 触发的任务：前端断开不等于用户离开，不做 idle-cancel
        self.status = TaskStatus.PENDING
        self.progress: Optional[TaskProgress] = None
        self.generated_cards: List[Card] = []
        self.error: Optional[str] = None
        self.created_at = datetime.utcnow()
        self.updated_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None
        self._event_queue: asyncio.Queue[TaskEvent] = asyncio.Queue()
        self._async_task: Optional[asyncio.Task] = None
        self._subscriber_count: int = 0
        self._idle_cancel_timer: Optional[asyncio.Task] = None

    def to_dict(self) -> dict:
        """序列化为字典（供 API 返回）。"""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "username": self.username,
            "keyword": self.keyword,
            "session_id": self.session_id,
            "params": self.params,
            "status": self.status.value,
            "progress": self.progress.model_dump() if self.progress else None,
            "created_at": self.created_at.isoformat(),
            "card_count": len(self.generated_cards),
            "error": self.error,
        }

    async def emit(self, event_type: str, data: Any):
        event = TaskEvent(
            type=event_type,
            data=data.model_dump(mode="json") if hasattr(data, "model_dump") else data,
            timestamp=datetime.utcnow().isoformat(),
        )
        await self._event_queue.put(event)
        self.updated_at = datetime.utcnow()

        if event_type == "progress" and isinstance(data, TaskProgress):
            self.progress = data
        elif event_type == "card" and hasattr(data, "id"):
            self.generated_cards.append(data)
        elif event_type == "error":
            self.status = TaskStatus.ERROR
            self.error = str(data) if isinstance(data, str) else data.get("message", "Unknown error")
        elif event_type == "complete":
            self.status = TaskStatus.COMPLETED
            self.completed_at = datetime.utcnow()

    async def events(self) -> AsyncIterator[TaskEvent]:
        # Cancel any pending idle timer when a client connects
        if self._idle_cancel_timer and not self._idle_cancel_timer.done():
            self._idle_cancel_timer.cancel()
            self._idle_cancel_timer = None

        self._subscriber_count += 1
        try:
            while True:
                # Drain any pending events non-blockingly
                drained = False
                while not self._event_queue.empty():
                    try:
                        event = self._event_queue.get_nowait()
                        drained = True
                        yield event
                    except asyncio.QueueEmpty:
                        break

                if self.status in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED):
                    break

                if drained:
                    continue  # got events, retry immediately

                # No events ready — wait briefly then re-check status + queue
                try:
                    event = await asyncio.wait_for(
                        self._event_queue.get(), timeout=1.0
                    )
                    yield event
                except asyncio.TimeoutError:
                    # Emit heartbeat to keep SSE connection alive during long pipeline ops
                    yield TaskEvent(type="_heartbeat", data={})
        finally:
            self._subscriber_count -= 1
            if (self._subscriber_count <= 0 and self.status == TaskStatus.RUNNING
                    and not self.agent_owned):
                self._schedule_idle_cancel()

    def start(self, async_task: asyncio.Task):
        self._async_task = async_task
        self.status = TaskStatus.RUNNING
        self.updated_at = datetime.utcnow()

    def _schedule_idle_cancel(self):
        """Cancel task if no client reconnects within 30 seconds."""

        async def _idle_timer():
            await asyncio.sleep(30)
            if self._subscriber_count <= 0 and self.status == TaskStatus.RUNNING:
                logger.info(f"[Task {self.task_id}] No clients for 30s, auto-cancelling")
                self.cancel()

        self._idle_cancel_timer = asyncio.create_task(_idle_timer())

    def cancel(self):
        if self._idle_cancel_timer and not self._idle_cancel_timer.done():
            self._idle_cancel_timer.cancel()
            self._idle_cancel_timer = None
        if self._async_task and not self._async_task.done():
            self._async_task.cancel()
        self.status = TaskStatus.CANCELLED
        self.completed_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()


class TaskManager:
    _instance: Optional[TaskManager] = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tasks: Dict[str, Task] = {}
            cls._instance._user_tasks: Dict[str, List[str]] = {}
        return cls._instance

    @classmethod
    def get_instance(cls) -> TaskManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def create_task(
        self,
        task_type: TaskType,
        username: str,
        session_id: str,
        keyword: str,
        params: Dict[str, Any] = None,
    ) -> Task:
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task = Task(
            task_id=task_id,
            task_type=task_type,
            username=username,
            session_id=session_id,
            keyword=keyword,
            params=params,
        )
        self._tasks[task_id] = task

        if username not in self._user_tasks:
            self._user_tasks[username] = []
        self._user_tasks[username].append(task_id)

        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        return self._tasks.get(task_id)

    def get_user_tasks(self, username: str, session_id: Optional[str] = None) -> List[Task]:
        task_ids = self._user_tasks.get(username, [])
        tasks = [self._tasks[tid] for tid in task_ids if tid in self._tasks]

        if session_id:
            tasks = [t for t in tasks if t.session_id == session_id]

        return tasks

    def get_running_tasks(self, username: str) -> List[Task]:
        tasks = self.get_user_tasks(username)
        return [t for t in tasks if t.status == TaskStatus.RUNNING]

    def remove_task(self, task_id: str):
        if task_id in self._tasks:
            task = self._tasks[task_id]
            if task.username in self._user_tasks:
                try:
                    self._user_tasks[task.username].remove(task_id)
                except ValueError:
                    pass
            del self._tasks[task_id]

    def cleanup_completed_tasks(self, max_age_hours: int = 24):
        now = datetime.utcnow()
        to_remove = []
        for task_id, task in self._tasks.items():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED):
                if task.completed_at:
                    age_hours = (now - task.completed_at).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        to_remove.append(task_id)

        for task_id in to_remove:
            self.remove_task(task_id)

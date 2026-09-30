"""
统一任务服务层。

所有任务创建、状态查询、事件流均通过此单例，
替代散落在 routes/ 和 pipeline/api.py 中的 TaskManager 直接调用。

Usage:
    svc = TaskService.get_instance()
    task = svc.create(TaskType.COLLECT, username, session_id, keyword)
    events = svc.launch(task, run_in_background())
    return StreamingResponse(events, media_type="text/event-stream")
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, List, Optional

from backend.models.task import Task, TaskManager, TaskType

logger = logging.getLogger(__name__)


class TaskService:
    """统一任务服务（单例）。

    封装 TaskManager 的创建/启动/查询/控制 + SSE 事件流生成，
    确保所有调用方使用一致的接口。
    """

    _instance: Optional["TaskService"] = None

    @classmethod
    def get_instance(cls) -> "TaskService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self._manager = TaskManager.get_instance()

    # ── 创建 ──────────────────────────────────────────────────────

    def create(
        self,
        task_type: TaskType,
        username: str,
        session_id: str,
        keyword: str,
        params: dict | None = None,
    ) -> Task:
        """创建并注册任务（不启动）。

        用于 pipeline/api.py 的 *_with_task 方法等需要手动控制生命周期的场景。
        """
        return self._manager.create_task(
            task_type=task_type,
            username=username,
            session_id=session_id,
            keyword=keyword,
            params=params,
        )

    def start(self, task: Task, background_coro) -> None:
        """仅启动任务（不返回 SSE 流）。

        用于 documents.py 等不需要 SSE 流式返回的场景。
        """
        bg = asyncio.create_task(background_coro)
        task.start(bg)

    def launch(self, task: Task, background_coro) -> AsyncIterator[str]:
        """启动任务并返回 SSE 事件生成器。

        用于 routes/ 层：创建任务后立即绑定后台协程、设置 RUNNING 状态、
        返回可直接传给 StreamingResponse 的异步事件流。

        Args:
            task: 已创建的任务对象
            background_coro: 后台协程（通常包裹 pipeline.run() 等）

        Returns:
            SSE 格式的异步事件流
        """
        self.start(task, background_coro)
        return self.stream_events(task)

    async def stream_events(self, task: Task) -> AsyncIterator[str]:
        """生成 SSE 事件流（公开接口）。"""
        logger.info("[TaskService] Starting event stream for task %s", task.task_id)

        yield f"data: {json.dumps({'type': 'status', 'data': {'task_id': task.task_id, 'status': task.status.value}}, ensure_ascii=False)}\n\n"

        event_count = 0
        try:
            async for event in task.events():
                if event.type == "_heartbeat":
                    yield ": heartbeat\n\n"
                    continue
                event_count += 1
                payload = {"type": event.type, "data": event.data}
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
        finally:
            logger.info("[TaskService] Stream complete for task %s, total events: %d", task.task_id, event_count)

    # ── 查询 ──────────────────────────────────────────────────────

    def get(self, task_id: str) -> Optional[Task]:
        """查询单个任务。"""
        return self._manager.get_task(task_id)

    def list_user_tasks(self, username: str, session_id: str | None = None) -> List[Task]:
        """查询用户任务（可按 session 过滤）。"""
        return self._manager.get_user_tasks(username, session_id)

    def list_running(self, username: str) -> List[Task]:
        """查询用户当前运行中的任务。"""
        return self._manager.get_running_tasks(username)

    def find_existing(self, username: str, session_id: str, task_type: TaskType, keyword: str) -> Optional[Task]:
        """查找同类型同关键词的已运行任务（用于 SSE 重连）。"""
        for t in self.list_running(username):
            if (t.keyword == keyword
                    and t.session_id == session_id
                    and t.task_type == task_type):
                return t
        return None

    # ── 控制 ──────────────────────────────────────────────────────

    def cancel(self, task_id: str) -> bool:
        """取消运行中的任务。"""
        task = self.get(task_id)
        if task is None:
            return False
        task.cancel()
        return True

    def remove(self, task_id: str) -> bool:
        """移除任务记录（如果运行中则先取消）。"""
        task = self.get(task_id)
        if task is None:
            return False
        if task.status.value == "running":
            task.cancel()
        self._manager.remove_task(task_id)
        return True

    # ── SSE 重连 ──────────────────────────────────────────────────

    async def replay_events(self, task: Task) -> AsyncIterator[str]:
        """回放已完成任务的历史事件 + 进行中任务的延续流。"""
        if task.status.value in ("completed", "error", "cancelled"):
            yield f"data: {json.dumps({'type': 'status', 'data': {'status': task.status.value, 'message': 'Task already completed'}}, ensure_ascii=False)}\n\n"
            if task.progress:
                yield f"data: {json.dumps({'type': 'progress', 'data': task.progress.model_dump()}, ensure_ascii=False)}\n\n"
            for card in task.generated_cards:
                yield f"data: {json.dumps({'type': 'card', 'data': card.model_dump(mode='json')}, ensure_ascii=False)}\n\n"
            if task.error:
                yield f"data: {json.dumps({'type': 'error', 'data': {'message': task.error}})}\n\n"
            yield f"data: {json.dumps({'type': 'complete', 'data': {'card_count': len(task.generated_cards)}})}\n\n"
        else:
            # Running task: replay state + continue live
            yield f"data: {json.dumps({'type': 'status', 'data': {'task_id': task.task_id, 'status': task.status.value}}, ensure_ascii=False)}\n\n"
            if task.progress:
                yield f"data: {json.dumps({'type': 'progress', 'data': task.progress.model_dump()}, ensure_ascii=False)}\n\n"
            for card in task.generated_cards:
                yield f"data: {json.dumps({'type': 'card', 'data': card.model_dump(mode='json')}, ensure_ascii=False)}\n\n"
            async for event in task.events():
                if event.type == "_heartbeat":
                    yield ": heartbeat\n\n"
                    continue
                payload = {"type": event.type, "data": event.data}
                yield f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

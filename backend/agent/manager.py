"""Agent 循环后台管理 — 循环与 SSE 连接解耦。

问题：run_agent_loop 是 async generator，直接被 StreamingResponse 消费时，
前端刷新/断开连接会取消整个循环（含正在执行的工具和后台流水线任务）。

方案：循环由独立 asyncio.Task 后台运行，事件写入单一队列；
SSE 只是队列订阅者，断开只结束订阅、不取消循环。
刷新后重新发消息 → 注入 bridge + 订阅同一队列 → 继续看到进度（积压回放）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncIterator, Optional

logger = logging.getLogger(__name__)


def _sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, "data": data}
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class AgentLoopManager:
    """管理 per-key 的后台 agent 循环（单例）。"""

    _instance: Optional["AgentLoopManager"] = None

    def __new__(cls):
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._tasks = {}
            inst._queues = {}
            cls._instance = inst
        return cls._instance

    @classmethod
    def get_instance(cls) -> "AgentLoopManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def is_running(self, key: str) -> bool:
        task = self._tasks.get(key)
        return task is not None and not task.done()

    def start(self, key: str, generator) -> None:
        """启动后台循环。同一 key 已有运行循环则忽略。"""
        if self.is_running(key):
            return
        q: asyncio.Queue = asyncio.Queue()
        self._queues[key] = q

        async def _consume() -> None:
            try:
                async for event in generator:
                    await q.put(event)
            except asyncio.CancelledError:
                await q.put(_sse("error", {"message": "Agent 任务被取消"}))
            except Exception as e:
                logger.error("[AgentLoop] Background loop failed: %s", e)
                await q.put(_sse("error", {"message": str(e)}))
            finally:
                await q.put(_sse("complete", {"turns": -1, "tools_used": [], "interrupted": True}))
                await asyncio.sleep(0.2)
                self._queues.pop(key, None)
                self._tasks.pop(key, None)

        self._tasks[key] = asyncio.create_task(_consume())

    async def subscribe(self, key: str) -> AsyncIterator[str]:
        """订阅后台循环的事件流。断开只结束订阅，不取消循环。"""
        q = self._queues.get(key)
        if q is None or not self.is_running(key):
            yield _sse("error", {"message": "当前没有正在运行的 Agent 任务"})
            return
        try:
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield event
                    if '"complete"' in event:
                        return
                except asyncio.TimeoutError:
                    if not self.is_running(key):
                        return
                    yield ": heartbeat\n\n"
        finally:
            pass

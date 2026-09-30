"""LoopBridge — route ↔ loop 之间非阻塞消息通道。

单一职责: 接收外部消息、交付给 loop。
不关心消息内容、不持久化、不做业务逻辑。
"""

from __future__ import annotations

import asyncio


class _LoopBridge:
    _queues: dict[str, asyncio.Queue] = {}

    def register(self, key: str) -> None:
        self._queues[key] = asyncio.Queue()

    def unregister(self, key: str) -> None:
        self._queues.pop(key, None)

    def is_active(self, key: str) -> bool:
        return key in self._queues

    def send(self, key: str, message: str) -> None:
        q = self._queues.get(key)
        if q is None:
            return
        q.put_nowait(message)

    def receive(self, key: str) -> str | None:
        q = self._queues.get(key)
        if q is None or q.empty():
            return None
        try:
            return q.get_nowait()
        except asyncio.QueueEmpty:
            return None


loop_bridge = _LoopBridge()

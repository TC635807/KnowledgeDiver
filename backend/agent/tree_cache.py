"""树摘要增量缓存 — 写层工具成功后失效，读层轮次零 DB 查询。

对齐 DeepSeek Harness 的 session-projection 思路：树摘要是"卡片集合"的投影，
只有写操作改变集合。缓存 key 为 (username, session_id)：

- run_agent_loop 每轮 decide 通过 TreeSummaryCache.instance().get() 取摘要：读层轮次直接命中；
- 写层工具（search_by_keyword / expand_from_card / refresh_card / link_card）成功、
  以及新一轮用户消息进入时调用 invalidate()，下一次 get 重建。

单例 + 线程锁：loop 协程与工具线程都可能访问。
"""

from __future__ import annotations

import threading
from typing import Callable

_MAX_ENTRIES = 128


class TreeSummaryCache:
    _instance: "TreeSummaryCache | None" = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, str] = {}

    @classmethod
    def instance(cls) -> "TreeSummaryCache":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @staticmethod
    def _key(username: str, session_id: str) -> str:
        return f"{username}:{session_id}"

    def invalidate(self, username: str, session_id: str) -> None:
        """写操作后调用：丢弃该会话的缓存，下一次 get 重建。"""
        with self._lock:
            self._store.pop(self._key(username, session_id), None)

    def get(self, username: str, session_id: str, builder: Callable[[], str]) -> str:
        """返回缓存的树摘要；未命中时用 builder 重建并缓存。

        builder 只应在缓存未命中时执行（省掉每轮 O(卡片数) 的全树查询与遍历）。
        """
        key = self._key(username, session_id)
        with self._lock:
            cached = self._store.get(key)
        if cached is not None:
            return cached
        text = builder()
        with self._lock:
            if len(self._store) >= _MAX_ENTRIES:
                # 超出上限时淘汰最旧条目（dict 保序为插入序）
                oldest = next(iter(self._store))
                del self._store[oldest]
            self._store[key] = text
        return text

    def clear(self) -> None:
        """测试辅助：清空全部缓存。"""
        with self._lock:
            self._store.clear()

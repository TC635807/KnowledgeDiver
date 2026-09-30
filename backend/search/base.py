"""搜索客户端共享基类。

   封装三个搜索源（Bocha/Baidu/Exa）共有的模式：
  - 并发控制（Semaphore）
  - 速率限制（最小请求间隔）
  - 指数退避重试


子类只需实现 _do_search()，专注 API 调用 + 响应解析。
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from backend.pipeline.stages import SearchResult

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """子类遇到 429 限速时抛出，基类会以指数退避重试。"""


class FatalSearchError(Exception):
    """致命搜索错误（认证失败/额度不足/服务不可用）。

    基类不重试、不吞掉，直接上抛给调用方（如自动回退包装器），
    以便切换到备用搜索源。子类在 _do_search 中抛出。
    """


class BaseSearchClient(ABC):
    """搜索客户端基类：封装重试、限流的共享模式。

    设计原则：
      - _do_search 抛异常 -> 基类重试（瞬态错误：超时、网络、429）
      - _do_search 返回 [] -> 基类不重试（非瞬态：无结果、认证失败）
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 5,
        rate_per_sec: float = 2.0,
        max_retries: int = 2,
        base_delay: float = 1.0,
    ):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._lock = asyncio.Lock()
        self._last_ts: float = 0.0
        self._min_interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._name = self.__class__.__name__

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """带限流、重试的搜索入口。"""
        if not query:
            return []

        async with self._semaphore:
            return await self._search_with_retry(query, max_results)

    @abstractmethod
    async def _do_search(self, query: str, max_results: int) -> list:
        """执行单步搜索。抛异常->基类重试；返回[]->基类不重试。"""
        ...

    async def close(self) -> None:
        """子类可覆盖以释放资源。"""
        pass

    async def _search_with_retry(self, query: str, max_results: int) -> list:
        """指数退避重试。RateLimitError 与其他异常走同一退避逻辑；
        FatalSearchError 不重试、直接上抛（由回退包装器处理）。"""
        for attempt in range(self.max_retries + 1):
            try:
                await self._rate_limit()
                return await self._do_search(query, max_results)
            except FatalSearchError:
                raise
            except Exception as e:
                if attempt >= self.max_retries:
                    logger.warning(
                        "[%s] 搜索 %r 失败，已重试 %d 次: %s",
                        self._name, query, self.max_retries, e,
                    )
                    return []
                delay = self.base_delay * (2 ** attempt)
                logger.warning(
                    "[%s] 搜索 %r 第 %d/%d 次失败，%.1fs 后重试: %s",
                    self._name, query, attempt + 1, self.max_retries + 1, delay, e,
                )
                await asyncio.sleep(delay)
        return []

    async def _rate_limit(self) -> None:
        """令请求间至少间隔 _min_interval 秒，避免触发限速。"""
        if self._min_interval <= 0:
            return
        async with self._lock:
            elapsed = time.monotonic() - self._last_ts
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_ts = time.monotonic()


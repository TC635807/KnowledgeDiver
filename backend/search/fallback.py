"""搜索自动回退：博查主用，百度反代兜底（熔断式）。

触发条件（FatalSearchError）：
  - 博查 401/403 认证失效
  - 博查 402 / 额度、余额类业务错误（额度不足）
  - 显式抛出的服务不可用
连续 FALLBACK_THRESHOLD 次触发后熔断（circuit open），后续搜索全部直走百度，
避免每查询都先打一次博查白等；熔断状态可通过 reset() 手动复位。

对调用方完全透明：仍继承 BaseSearchClient，返回 List[SearchResult]。
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

from backend.search.base import BaseSearchClient, FatalSearchError, RateLimitError

logger = logging.getLogger(__name__)


class AutoFallbackSearchClient(BaseSearchClient):
    """主源 + 备用源自动回退，带熔断器。"""

    def __init__(
        self,
        primary: BaseSearchClient,
        fallback: BaseSearchClient,
        fallback_threshold: int = 3,
        circuit_reset_seconds: float = 1800.0,
        name: str = "AutoFallback",
    ):
        super().__init__(max_concurrent=max(primary._semaphore._value, 1),
                         rate_per_sec=0.0, max_retries=0)
        self._primary = primary
        self._fallback = fallback
        self._threshold = fallback_threshold
        self._reset_seconds = circuit_reset_seconds
        self._name = name
        self._fatal_count = 0
        self._circuit_open = False
        self._circuit_opened_at: Optional[float] = None
        self._fallback_hits = 0
        self._last_trigger: Optional[str] = None

    @property
    def active_provider(self) -> str:
        """当前实际生效的搜索源（供日志/监控）。"""
        return "baidu(fallback)" if self._circuit_open else "bocha"

    def reset(self) -> None:
        """手动复位熔断（用于确认主源已恢复后）。"""
        self._circuit_open = False
        self._fatal_count = 0
        self._circuit_opened_at = None
        logger.info("[%s] 熔断已手动复位，恢复主源", self._name)

    def stats(self) -> dict:
        return {
            "circuit_open": self._circuit_open,
            "fatal_count": self._fatal_count,
            "fallback_hits": self._fallback_hits,
            "active_provider": self.active_provider,
            "last_trigger": self._last_trigger,
        }

    def _maybe_reopen(self) -> None:
        """熔断后经过足够时间自动试探复位（半开）。"""
        if (self._circuit_open and self._circuit_opened_at
                and time.monotonic() - self._circuit_opened_at > self._reset_seconds):
            self._circuit_open = False
            self._fatal_count = 0
            logger.info("[%s] 熔断时间到，自动复位试探主源", self._name)

    def _trip(self, reason: str) -> None:
        self._fatal_count += 1
        self._last_trigger = reason
        if not self._circuit_open and self._fatal_count >= self._threshold:
            self._circuit_open = True
            self._circuit_opened_at = time.monotonic()
            logger.error(
                "[%s] 主源连续 %d 次失败（最近: %s），熔断切换为备用源（百度反代）",
                self._name, self._threshold, reason,
            )

    async def _do_search(self, query: str, max_results: int) -> list:
        self._maybe_reopen()

        if self._circuit_open:
            try:
                results = await self._fallback.search(query, max_results)
                self._fallback_hits += 1
                return results
            except Exception as e:
                logger.error("[%s] 备用源(百度)也失败: %s", self._name, e)
                return []

        # 主源路径：FatalSearchError 触发回退；其他异常由主源自身重试
        try:
            return await self._primary.search(query, max_results)
        except FatalSearchError as e:
            self._trip(str(e))
            logger.warning("[%s] 主源失败，回退百度反代: %s", self._name, e)
            try:
                results = await self._fallback.search(query, max_results)
                self._fallback_hits += 1
                return results
            except Exception as e2:
                logger.error("[%s] 备用源(百度)也失败: %s", self._name, e2)
                return []
        except Exception as e:
            # 主源的非致命异常（重试耗尽后按主源语义返回空）——不触发回退
            logger.warning("[%s] 主源搜索异常: %s", self._name, e)
            return []

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()
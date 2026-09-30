"""百度搜索客户端模块。

基于 baidu_serp_api 库，继承 BaseSearchClient。
只保留 API 调用 + 响应解析，重试/限流/缓存由基类处理。
"""

from __future__ import annotations

import asyncio
import logging
from typing import List

from backend.search.base import BaseSearchClient

logger = logging.getLogger(__name__)


class BaiduSearchClient(BaseSearchClient):
    """百度搜索客户端。

    使用 baidu_serp_api.BaiduPc 执行同步搜索，通过 asyncio.to_thread 转为异步。
    """

    def __init__(
        self,
        max_concurrent: int = 3,
        rate_limit: float = 1.0,
        retry_limit: int = 2,
    ) -> None:
        super().__init__(
            max_concurrent=max_concurrent,
            rate_per_sec=rate_limit,
            max_retries=retry_limit,
            base_delay=2.0,
        )
        self._client = None

    def _get_client(self):
        if self._client is None:
            from baidu_serp_api import BaiduPc
            self._client = BaiduPc()
        return self._client

    async def _do_search(self, query: str, max_results: int) -> list:
        from backend.pipeline.stages import SearchResult

        client = self._get_client()
        raw = await asyncio.to_thread(client.search, query)

        if not isinstance(raw, dict):
            logger.warning(f"[Baidu] {query!r} 返回异常类型: {type(raw).__name__}")
            return []

        code = raw.get("code", "")
        msg = raw.get("msg", "")
        if code != 200:
            logger.warning(f"[Baidu] {query!r} 错误: code={code}, msg={msg}")
            if code == 501:
                logger.warning("[Baidu] 安全验证触发")
            elif code == 429:
                logger.warning("[Baidu] 限速 429")
            return []

        data = raw.get("data", {})
        items = data.get("results", []) if isinstance(data, dict) else []

        results = []
        # 全量返回候选，不做客户端截断——截断职责在 select_top（五维加权挑 topN）。
        # 历史 bug：items[:max_results] 把候选池截小，select_top 无选择余地。
        for r in items:
            if not isinstance(r, dict):
                continue
            url = r.get("url", "")
            if not url:
                continue
            results.append(SearchResult(
                url=url,
                title=r.get("title", ""),
                snippet=r.get("description", ""),
            ))

        logger.info(f"[Baidu] {query!r} 返回 {len(results)} 条")
        return results

"""博查 (Bocha) 搜索客户端模块。

基于博查 AI 开放平台的 Web Search API，继承 BaseSearchClient。
只保留 API 调用 + 响应解析，重试/限流/缓存由基类处理。
"""

from __future__ import annotations

import logging
import time
from typing import List, Optional

import httpx

from backend.config import BOCHA_API_URL, BOCHA_API_KEY, BOCHA_MAX_CONCURRENT, BOCHA_TIMEOUT
from backend.search.base import BaseSearchClient, RateLimitError, FatalSearchError

logger = logging.getLogger(__name__)


# 黑名单 exclude 列表内存缓存（避免每次搜索都打 SQLite，60s 刷新）
_blocked_cache: list[str] = []
_blocked_cache_ts: float = 0.0
_BLOCKED_CACHE_TTL = 60.0


def _get_excluded_domains() -> list[str]:
    """读取 DomainQualityCache 当前封禁域名列表，60s 内存缓存。Bocha exclude 上限 100 个。"""
    global _blocked_cache, _blocked_cache_ts
    now = time.time()
    if _blocked_cache and now - _blocked_cache_ts < _BLOCKED_CACHE_TTL:
        return _blocked_cache
    try:
        from backend.scraper.domain_quality import get_domain_quality
        _blocked_cache = get_domain_quality().list_blocked(limit=100)
        _blocked_cache_ts = now
    except Exception as e:
        logger.debug("[Bocha] Failed to load blocked domains: %s", e)
        _blocked_cache = []
        _blocked_cache_ts = now
    return _blocked_cache


class BochaSearchClient(BaseSearchClient):
    """博查搜索客户端。"""

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_concurrent: int = BOCHA_MAX_CONCURRENT,
        freshness: str = "noLimit",
        summary: bool = True,
    ):
        super().__init__(
            max_concurrent=max_concurrent,
            rate_per_sec=0.0,
            max_retries=2,
            base_delay=2.0,
        )
        self.api_key = api_key or BOCHA_API_KEY
        self.api_url = api_url or BOCHA_API_URL
        self._http_client = http_client
        self._owns_client = False
        self._freshness = freshness
        self._summary = summary

    async def _do_search(self, query: str, max_results: int) -> list:
        from backend.pipeline.stages import SearchResult

        if not self.api_key:
            logger.error("[Bocha] BOCHA_API_KEY 未设置")
            return []

        client = await self._get_client()

        payload = {
            "query": query,
            "freshness": self._freshness,
            "summary": self._summary,
            # count 固定 60：候选池必须远大于目标来源数（max_sources=5），
            # select_top 才有选择余地——搜索结果低质 URL 居多的根因就是候选池
            # 太小（max_sources=5 时 API 只给 3-5 条，无米下锅）。
            # 博查按次计费，返回条数不额外计费；超上限时由服务端钳制。
            "count": 60,
        }

        excluded = _get_excluded_domains()
        if excluded:
            payload["exclude"] = "|".join(excluded)
            logger.debug("[Bocha] Excluded %d blocked domains", len(excluded))

        response = await client.post(self.api_url, json=payload)

        if response.status_code == 429:
            raise RateLimitError(f"[Bocha] 限速 429 for {query!r}")
        if response.status_code in (401, 403):
            # 认证失效：非瞬态，上抛让回退包装器切换备用源
            raise FatalSearchError(f"[Bocha] 认证失败 (HTTP {response.status_code})")
        if response.status_code == 402:
            # 欠费/额度不足
            raise FatalSearchError(f"[Bocha] 余额/额度不足 (HTTP 402) for {query!r}")
        if not response.is_success:
            raise RuntimeError(f"[Bocha] HTTP {response.status_code} for {query!r}")

        data = response.json()
        return self._parse_response(data, max_results, SearchResult)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client:
            return self._http_client
        self._http_client = httpx.AsyncClient(
            timeout=BOCHA_TIMEOUT,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        self._owns_client = True
        return self._http_client

    def _parse_response(self, data: dict, max_results: int, SearchResult) -> list:
        code = data.get("code")
        if code != 200:
            msg = str(data.get("msg", ""))
            logger.warning(f"[Bocha] API 错误: code={code}, msg={msg}")
            # 额度/余额类业务错误按致命处理，触发自动回退
            if any(k in msg for k in ("额度", "余额", "欠费", "quota", "balance",
                                      "insufficient", "credit", "充值")):
                raise FatalSearchError(f"[Bocha] 额度/余额错误: code={code}, msg={msg}")
            return []

        results_data = data.get("data", {})
        if not isinstance(results_data, dict):
            return []

        web_pages = results_data.get("webPages", {})
        if not isinstance(web_pages, dict):
            return []

        total = web_pages.get("totalEstimatedMatches", 0)
        items = web_pages.get("value", [])
        if not isinstance(items, list):
            return []

        results = []
        # 全量返回候选（count=60），不做客户端截断——截断职责在 select_top
        # （按域名质量+白名单+结构五维加权挑 top5）。历史 bug：items[:max_results]
        # 把候选池截成 5 条，select_top 无选择余地，低质 URL 直通抓取。
        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("url", "")
            if not url:
                continue
            results.append(SearchResult(
                url=url,
                title=item.get("name", ""),
                snippet=item.get("snippet", ""),
            ))

        logger.info(f"[Bocha] 返回 {len(results)} 条 (总计约 {total} 条)")
        return results

    async def close(self) -> None:
        if self._owns_client and self._http_client:
            await self._http_client.aclose()
            self._http_client = None

"""
默认 Pipeline 阶段实现，包裹现有的搜索、抓取、AI 卡片生成逻辑。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from typing import Any, AsyncGenerator, AsyncIterator, List

from backend.ai.openai_provider import _extract_json_from_response
from backend.models.card import Card
from backend.pipeline.stages import (
    CardBuilder,
    CardPersister,
    ContentProcessor,
    ExploreTask,
    Explorer,
    FetchResult,
    PipelineProgress,
    ProcessedContent,
    SearchResult,
    SourceProvider,
    TextFetcher,
)
from backend.pipeline.quality_gate import gate_content
from backend.search import SearchClient, SearchResult as ExaSearchResult, BochaSearchClient
from backend.scraper import WebFetcher, ScrapedContent

logger = logging.getLogger(__name__)


def _dq():
    """延迟获取 DomainQualityCache 单例（避免循环导入）。"""
    from backend.scraper.domain_quality import get_domain_quality
    return get_domain_quality()


class ExaSourceProvider(SourceProvider):
    def __init__(self, search_client: SearchClient):
        self.search_client = search_client

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        raw = await self.search_client.search(query, max_results)
        results = []
        for r in raw:
            if isinstance(r, SearchResult):
                if r.url and not _dq().is_blocked(r.url):
                    results.append(r)
            elif hasattr(r, "url"):
                url = getattr(r, "url", "")
                if url and not _dq().is_blocked(url):
                    results.append(
                        SearchResult(
                            url=url,
                            title=getattr(r, "title", ""),
                            snippet=getattr(r, "snippet", getattr(r, "description", "")),
                        )
                    )
        return results

    async def close(self) -> None:
        if hasattr(self.search_client, "close"):
            await self.search_client.close()


class BaiduSourceProvider(SourceProvider):
    def __init__(self, search_client: Any):
        self.search_client = search_client

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        raw = await self.search_client.search(query, max_results)
        results: List[SearchResult] = []
        for r in raw:
            if isinstance(r, SearchResult):
                if r.url and not _dq().is_blocked(r.url):
                    results.append(r)
            elif isinstance(r, dict):
                url = r.get("url", "")
                if not url or _dq().is_blocked(url):
                    continue
                results.append(SearchResult(
                    url=url,
                    title=str(r.get("title", "")),
                    snippet=str(r.get("description", "")),
                ))
        return results

    async def close(self) -> None:
        if hasattr(self.search_client, "close"):
            await self.search_client.close()


class BochaSourceProvider(SourceProvider):
    def __init__(self, search_client: BochaSearchClient):
        self.search_client = search_client

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        raw = await self.search_client.search(query, max_results)
        results: List[SearchResult] = []
        for r in raw:
            if isinstance(r, SearchResult):
                if r.url and not _dq().is_blocked(r.url):
                    results.append(r)
            elif isinstance(r, dict):
                url = r.get("url", "")
                if not url or _dq().is_blocked(url):
                    continue
                results.append(SearchResult(
                    url=url,
                    title=str(r.get("title", "")),
                    snippet=str(r.get("snippet", "")),
                ))
        return results

    async def close(self) -> None:
        if hasattr(self.search_client, "close"):
            await self.search_client.close()


class FreeSourceProvider(SourceProvider):
    """免费多引擎搜索源（provider="free"，backend/search/free.py）。

    对搜索客户端返回类型做鸭子类型兼容：pipeline.SearchResult / dict /
    任意带 url/title/snippet 的对象（如 pydantic SearchResult）都能接住。
    """

    def __init__(self, search_client: Any):
        self.search_client = search_client

    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        raw = await self.search_client.search(query, max_results)
        results: List[SearchResult] = []
        for r in raw:
            if isinstance(r, SearchResult):
                if r.url and not _dq().is_blocked(r.url):
                    results.append(r)
                continue
            if isinstance(r, dict):
                url = r.get("url", "")
                title = r.get("title", "")
                snippet = r.get("snippet", r.get("description", ""))
            else:
                url = getattr(r, "url", "")
                title = getattr(r, "title", "")
                snippet = getattr(r, "snippet", getattr(r, "description", ""))
            if not url or _dq().is_blocked(url):
                continue
            results.append(SearchResult(url=url, title=str(title), snippet=str(snippet)))
        return results

    async def close(self) -> None:
        if hasattr(self.search_client, "close"):
            await self.search_client.close()


class WebPageTextFetcher(TextFetcher):
    def __init__(self, web_fetcher: WebFetcher, raw_store: Any = None):
        self.web_fetcher = web_fetcher
        self.raw_store = raw_store  # Optional[RawPageStore]

    async def fetch(self, source: SearchResult) -> FetchResult | None:
        # 短时间窗去重：并发探索分支常搜到相同 URL，10 分钟内已抓过则跳过
        if self.raw_store is not None:
            try:
                from datetime import datetime, timezone
                row = self.raw_store.get(source.url)
                if row and row.get("fetched_at"):
                    ts = datetime.fromisoformat(row["fetched_at"])
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if (datetime.now(timezone.utc) - ts).total_seconds() < 600:
                        logger.info("[Fetcher] Skipped (recently fetched): %s", source.url)
                        return None
            except Exception:
                pass
        try:
            content = await self.web_fetcher.fetch(source.url)
        except Exception as e:
            logger.warning("[Fetcher] Failed to fetch %s: %s", source.url, e)
            from backend.scraper.domain_quality import get_domain_quality
            get_domain_quality().record_failure(source.url)
            return None
        if len(content.content) < 1000:
            logger.info("[Fetcher] Content too short (%d chars) for %s", len(content.content), source.url)
            from backend.scraper.domain_quality import get_domain_quality
            get_domain_quality().record_failure(source.url)
            return None

        from backend.scraper.domain_quality import get_domain_quality
        get_domain_quality().record_success(source.url, len(content.content))

        # 保存原始网页到 session 数据库
        if self.raw_store is not None:
            try:
                self.raw_store.save(
                    url=content.url,
                    title=content.title,
                    content=content.content,
                    method="trafilatura/crawl4ai",
                    metadata=content.metadata,
                )
            except Exception as e:
                logger.warning("[Fetcher] RawStore save failed: %s", e)

        return FetchResult(
            text=content.content,
            source_url=content.url,
            source_title=content.title,
            raw_metadata=content.metadata,
        )

    async def close(self) -> None:
        if hasattr(self.web_fetcher, "close"):
            await self.web_fetcher.close()


class DocumentChunkFetcher(TextFetcher):
    def __init__(self):
        pass

    async def fetch(self, source: SearchResult) -> FetchResult | None:
        if len(source.snippet) < 30:
            return None
        return FetchResult(
            text=source.snippet,
            source_url=source.url or "document",
            source_title=source.title,
        )


class ScrapeAndSummarizeProcessor(ContentProcessor):
    def __init__(
        self,
        fetcher: TextFetcher,
        max_concurrent: int = 5,  # 与 fetcher._crawler_sem=5 对齐，避免 crawl4ai 槽位外空等
        early_stop: int = 0,      # 合格来源达到此数即提前终止剩余抓取（0=不启用）
                                  # 搜索场景 factory 传 2：候选池扩大后低质站居多，
                                  # 抓 2 个合格来源即够生成卡片，避免逐个抓完浪费时间
    ):
        self.fetcher = fetcher
        self.max_concurrent = max_concurrent
        self.early_stop = early_stop

    async def process(self, results: List[SearchResult]) -> List[ProcessedContent]:
        sem = asyncio.Semaphore(self.max_concurrent)
        early_stop = self.early_stop
        stop = asyncio.Event()
        accepted = 0

        async def process_one(r: SearchResult) -> ProcessedContent | None:
            nonlocal accepted
            async with sem:
                try:
                    # 不包外层 wait_for：fetch 内部各阶段（crawl4ai/trafilatura/light）
                    # 已有独立超时兜底；外层总超时会把 crawl4ai 槽位排队时间也算进去，
                    # 导致并发时排队中的 URL 被误杀（实测 3-7s 的 URL 并发下 40s 超时）。
                    fetched = await self.fetcher.fetch(r)
                    if fetched is None:
                        logger.info("[Processor] Fetch skipped (short/unavailable): %s", r.url)
                        return None
                    ok, reason = gate_content(getattr(fetched, "text", "") or "")
                    if not ok:
                        logger.info("[Processor] Quality gate rejected %s: %s", r.url, reason)
                        _dq().record_failure(r.url)
                        return None
                    accepted += 1
                    if early_stop > 0 and accepted >= early_stop:
                        stop.set()
                    # 卡片生成直接用原文（text=raw_text）；摘要层已无消费方，不再调用 LLM
                    return ProcessedContent(
                        source=SearchResult(url=fetched.source_url, title=fetched.source_title),
                        text=fetched.text,
                        raw_text=fetched.text,
                        metadata={
                            "source_url": fetched.source_url,
                            "source_title": fetched.source_title,
                        },
                    )
                except asyncio.TimeoutError:
                    logger.info("[Processor] Timeout: %s", r.url)
                    return None
                except Exception as e:
                    logger.info("[Processor] Failed: %s — %s", r.url, e)
                    return None

        tasks = [asyncio.create_task(process_one(r)) for r in results]
        if early_stop > 0 and len(tasks) > early_stop:
            # 等待 stop 信号或全部完成；stop 后取消剩余抓取任务。
            # 排队中（未拿到 semaphore）的任务安全取消；已 fetch 中的协程取消后
            # crawl4ai to_thread 线程在后台跑完即弃，不影响正确性。
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            while pending and not stop.is_set():
                done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if stop.is_set() and pending:
                logger.info("[Processor] Early stop: %d qualified sources reached, cancelled %d pending fetches",
                            accepted, len(pending))
                for t in pending:
                    t.cancel()
        raw = await asyncio.gather(*tasks, return_exceptions=True)
        filtered = [p for p in raw if isinstance(p, ProcessedContent)]
        logger.info("[Processor] %d/%d sources processed successfully", len(filtered), len(results))
        return filtered

    async def close(self) -> None:
        await self.fetcher.close()


class LLMCardBuilder(CardBuilder):
    def __init__(self, ai_provider: Any):
        self.ai_provider = ai_provider

    async def build(
        self, content: List[ProcessedContent], context: str
    ) -> AsyncGenerator[PipelineProgress | Card, None]:
        if not content:
            logger.warning("[LLMCardBuilder] No processed content to build from")
            return

        sources_for_ai = [
            {
                "title": p.metadata.get("source_title", p.source.title),
                # 卡片生成直接用抓取原文（raw_text），摘要只作 fallback：
                # 避免「摘要的摘要」二次压缩导致信息丢失
                "content": p.raw_text or p.text,
                "url": p.source.url,
            }
            for p in content
        ]

        logger.info(
            f"[LLMCardBuilder] Building card from {len(sources_for_ai)} sources, "
            f"total text length: {sum(len(s['content']) for s in sources_for_ai)}"
        )

        ai_output = ""
        async for chunk in self.ai_provider.generate_cards_from_sources_stream(
            sources_for_ai, context
        ):
            ai_output += chunk
            yield PipelineProgress(
                stage="generating",
                message="AI 正在生成...",
                progress=0.35,
                current_item=context,
                ai_output=ai_output,
            )

        logger.info(f"[LLMCardBuilder] AI output received: {len(ai_output)} chars")
        for card in self._parse(ai_output, content, context):
            yield card

    def _parse(
        self, ai_output: str, content: List[ProcessedContent], context: str
    ) -> List[Card]:
        try:
            cleaned = _extract_json_from_response(ai_output)
            data = json.loads(cleaned)
            if isinstance(data, dict):
                data = [data]
            if not isinstance(data, list) or not data:
                logger.warning(
                    "[LLMCardBuilder] Parsed JSON is not a valid list: %s",
                    str(data)[:200] if data else "empty",
                )
                return []
            cards: List[Card] = []
            for item in data:
                if not isinstance(item, dict) or not ("title" in item and "content" in item):
                    logger.warning(
                        "[LLMCardBuilder] Skipping card missing title/content keys: %s",
                        list(item.keys()) if isinstance(item, dict) else type(item).__name__,
                    )
                    continue

                source_indices = item.get("source_indices", [])
                source_indices = [int(i) for i in source_indices if 0 <= int(i) < len(content)]

                raw_conf = item.get("confidence")
                confidence = 0.5
                if raw_conf is not None:
                    try:
                        confidence = max(0.0, min(1.0, float(raw_conf)))
                    except (ValueError, TypeError):
                        pass

                card = Card(
                    id=str(uuid.uuid4()),
                    title=str(item["title"]),
                    content=self._append_citations(str(item["content"]), content, source_indices),
                    metadata={},
                    sources=[content[i].source.url for i in source_indices]
                    if source_indices
                    else [p.source.url for p in content],
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    confidence=confidence,
                    tags=[str(t) for t in item.get("tags", []) if t],
                    links=[],
                    backlinks=[],
                )
                if len(card.content.strip()) < 30:
                    logger.warning(
                        "[LLMCardBuilder] Card content too short (%d chars), discarding. title=%s",
                        len(card.content.strip()), card.title,
                    )
                    continue
                cards.append(card)
            if cards:
                logger.info(
                    "[LLMCardBuilder] Generated %d cards: %s",
                    len(cards), [c.title for c in cards],
                )
                return cards
            logger.warning("[LLMCardBuilder] No valid cards parsed from AI output")
            return []
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning(
                "[LLMCardBuilder] JSON parse failed: %s, ai_output[:200]: %s",
                e, ai_output[:200],
            )
            fallback_content = ai_output[:2000] if ai_output else "生成失败"
            if len(fallback_content.strip()) < 30:
                logger.warning(
                    "[LLMCardBuilder] Fallback content also too short (%d chars), discarding",
                    len(fallback_content.strip()),
                )
                return []
            raw_conf = data.get("confidence") if isinstance(data, dict) else None
            fb_conf = 0.3
            if raw_conf is not None:
                try:
                    fb_conf = max(0.0, min(1.0, float(raw_conf)))
                except (ValueError, TypeError):
                    pass
            return [
                Card(
                    id=str(uuid.uuid4()),
                    title=context,
                    content=self._append_citations(ai_output[:2000] if ai_output else "生成失败", content, []),
                    metadata={},
                    sources=[p.source.url for p in content],
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                    confidence=fb_conf,
                )
            ]

    def _append_citations(
        self, text: str, content: List[ProcessedContent], source_indices: List[int]
    ) -> str:
        indices = source_indices if source_indices else list(range(len(content)))
        if not indices:
            return text
        lines = ["\n\n---\n\n### 来源"]
        for n, i in enumerate(indices):
            if i >= len(content):
                continue
            src = content[i]
            title = src.metadata.get("source_title", src.source.title) or src.source.url
            url = src.source.url
            lines.append(f"{n}. [{title}]({url})")
        return text + "\n".join(lines)

    async def close(self) -> None:
        pass


class CardStorePersister(CardPersister):
    def __init__(self, card_store: Any):
        self.card_store = card_store
        self.last_skipped: int = 0

    def save(self, cards: List[Card]) -> List[Card]:
        if not cards:
            self.last_skipped = 0
            return []

        unique: list[Card] = []
        skipped = 0

        for card in cards:
            existing = self.card_store.find_by_title(card.title)
            if existing is not None:
                logger.info(
                    "[Persister] 标题重复，跳过: %s (已有 id=%s)",
                    card.title, existing.id,
                )
                skipped += 1
                continue
            unique.append(card)

        self.last_skipped = skipped
        saved = self.card_store.save_cards(unique)
        return saved if saved else unique


class RelatedTopicExplorer(Explorer):
    def __init__(self, ai_provider: Any, max_topics: int = 7, search_level: str = "default"):
        self.ai_provider = ai_provider
        self.max_topics = max_topics
        self.search_level = search_level

    async def explore(self, cards: List[Card]) -> AsyncIterator[ExploreTask]:
        for card in cards:
            if not card.content:
                continue
            try:
                topics = await self.ai_provider.extract_related_topics(
                    card.content, self.max_topics, self.search_level,
                    exclude_title=card.title, source_title=card.title,
                )
                for t in topics:
                    yield ExploreTask(query=str(t), parent_card_id=card.id)
            except Exception:
                continue

    async def extract_topics(
        self, card_content: str, max_count: int = 7, level: str = "default",
        exclude_title: str | None = None, source_title: str | None = None,
    ) -> List[str]:
        try:
            return await self.ai_provider.extract_related_topics(
                card_content, max_count, level,
                exclude_title=exclude_title, source_title=source_title,
            )
        except Exception:
            return []

    async def close(self) -> None:
        pass

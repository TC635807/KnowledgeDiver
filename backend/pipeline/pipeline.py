"""
Pipeline 编排器。

将 5 个阶段接口串联为可配置的执行流水线，
支持递归探索（Explorer 产出的 ExploreTask 自动重新进入流水线），
并保留与现有 SSE 事件系统的兼容性（yield PipelineProgress + Card）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, List, Optional
from urllib.parse import urlparse

from backend.links.manager import LinkManager
from backend.models.card import Card
from backend.config import PIPELINE_TIMEOUT_QUEUE
from backend.utils.titles import find_card_by_normalized_title
from backend.pipeline.stages import (
    CardBuilder,
    CardPersister,
    PipelineProgress,
    ContentProcessor,
    ExploreTask,
    Explorer,
    SearchResult,
    SourceProvider,
)

logger = logging.getLogger(__name__)


# 向量搜索合并阈值——语义距离小于此值则合并到已有卡片
MERGE_DISTANCE_THRESHOLD = 0.40

# 软锚定（方案 C）：短词才拼接源卡标题——长词/带括号限定词已自包含
ANCHOR_MAX_LEN = 4


def _needs_anchor(topic: str) -> bool:
    """判定搜索词是否需要软锚定：无括号的短词（跨领域多义词高危区）。

    e2e 实证：agent 建库 6/13 卡漂移——「Zero」→LayerZero 区块链、
    「毒蛇」→无畏契约、「F-35」→作战识别AI系统；expand 实测「瀑布模型」
    →粒子系统模拟。长词（如「软件生命周期」）或带括号限定（如
    「猎人（杀戮尖塔2）」）自包含，不拼接。
    """
    t = topic.strip()
    if not t or len(t) > ANCHOR_MAX_LEN:
        return False
    return "（" not in t and "(" not in t


def _extract_domain(url: str) -> str:
    """从 URL 中提取域名 (netloc)，失败返回空字符串。"""
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def _dq():
    """延迟获取 DomainQualityCache 单例。"""
    from backend.scraper.domain_quality import get_domain_quality
    return get_domain_quality()


class Pipeline:
    def __init__(
        self,
        source: SourceProvider,
        processor: ContentProcessor,
        builder: CardBuilder,
        persister: CardPersister,
        explorer: Explorer,
        max_explore_depth: int = 1,
        explore_concurrency: int = 7,
        card_store: Any = None,
        embedder: Any = None,
    ):
        self.source = source
        self.processor = processor
        self.builder = builder
        self.persister = persister
        self.explorer = explorer
        self.max_explore_depth = max_explore_depth
        self.explore_concurrency = explore_concurrency
        self.card_store = card_store
        self.embedder = embedder
        self._depth = 0
        self._re_search_count = 0

    async def run(
        self,
        query: str,
        max_sources: int = 2,
        source_card_id: Optional[str] = None,
        persist: bool = True,
    ) -> AsyncIterator[PipelineProgress | Card]:
        async for event in self._run_query(query, max_sources, source_card_id, persist=persist):
            yield event

    async def run_document(
        self,
        text: str,
        filename: str,
        sections: list[dict],
    ) -> AsyncIterator[PipelineProgress | Card]:
        """文档分析流水线入口。

        sections 由 AI 预先分析好的文档结构列表，格式：
        [{"title": "概述", "summary": "...", "key_points": [{"title": "...", "summary": "..."}]}, ...]
        第一条为根结构，后续为章节。"""

        yield PipelineProgress(
            stage="analyzing", message=f"正在分析文档: {filename}", progress=0.1,
        )

        if not sections:
            yield PipelineProgress(stage="error", message="文档结构分析失败，未找到章节", progress=1.0)
            return

        root_title = sections[0].get("title", filename) if sections else filename
        root_key_points = sections[0].get("key_points", [])
        root_text = sections[0].get("summary", "")

        root_source = SearchResult(url="document://root", title=root_title, snippet=root_text)
        root_processed = await self.processor.process([root_source])

        if root_processed:
            cards: list[Card] = []
            async for item in self.builder.build(root_processed, filename):
                if isinstance(item, PipelineProgress):
                    yield item
                else:
                    cards.append(item)
                    yield item
            self.persister.save(cards)
            await self._index_cards(cards)
            root_card = cards[0] if cards else None
        else:
            root_card = None

        detail_cards: list[Card] = []
        section_card_ids: dict[int, str] = {}
        section_sources = [
            SearchResult(
                url=f"document://section/{i}",
                title=s.get("title", f"section_{i}"),
                snippet=s.get("summary", ""),
            )
            for i, s in enumerate(sections[1:], 1) if s.get("summary")
        ]

        if section_sources:
            yield PipelineProgress(
                stage="analyzing",
                message=f"并行处理 {len(section_sources)} 个章节",
                progress=0.3,
            )
            # 并行处理所有章节源（processor.process 内部会并行抓取和总结）
            all_processed = await self.processor.process(section_sources)
            yield PipelineProgress(
                stage="analyzing",
                message=f"已处理 {len(all_processed)}/{len(section_sources)} 个章节",
                progress=0.4,
            )
            # 并行生成所有章节卡片
            sem = asyncio.Semaphore(5)
            section_event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
            section_completed = 0
            section_total = len(all_processed)

            async def build_section_card(idx: int, processed: ProcessedContent):
                nonlocal section_completed
                async with sem:
                    try:
                        cards: list[Card] = []
                        async for item in self.builder.build([processed], processed.source.title):
                            await section_event_queue.put((idx, item))
                            if isinstance(item, Card):
                                cards.append(item)
                        self.persister.save(cards)
                        await self._index_cards(cards)
                    except Exception as e:
                        logger.warning(f"[Pipeline] Section {idx} build failed: {e}")
                    finally:
                        section_completed += 1
                        await section_event_queue.put((idx, None))

            for i, processed in enumerate(all_processed):
                asyncio.create_task(build_section_card(i, processed))

            pending_sections = set(range(section_total))
            while pending_sections:
                try:
                    idx, event = await asyncio.wait_for(section_event_queue.get(), timeout=PIPELINE_TIMEOUT_QUEUE)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    pending_sections.discard(idx)
                    progress = 0.4 + 0.1 * (1 - len(pending_sections) / section_total)
                    yield PipelineProgress(
                        stage="analyzing",
                        message=f"章节卡片生成进度: {section_total - len(pending_sections)}/{section_total}",
                        progress=min(progress, 0.49),
                    )
                    continue
                if isinstance(event, PipelineProgress):
                    yield event
                elif isinstance(event, Card):
                    if root_card:
                        try:
                            await LinkManager(self.card_store).create_link(root_card.id, event.id, "expand")
                            if not event.parent_id:
                                event.parent_id = root_card.id
                                self.persister.save([event])
                        except Exception as e:
                            logger.warning(f"[Pipeline] Link failed {root_card.id} -> {event.id}: {e}")
                        section_card_ids[idx + 1] = event.id
                    yield event

        kp_tasks = []
        kp_parent_ids = []
        for section_idx, section in enumerate(sections):
            parent_card_id = section_card_ids.get(section_idx)
            for kp in section.get("key_points", []):
                if not isinstance(kp, dict) or not kp.get("summary"):
                    continue
                kp_tasks.append(kp)
                kp_parent_ids.append(parent_card_id)

        if kp_tasks:
            yield PipelineProgress(
                stage="analyzing",
                message=f"并行处理 {len(kp_tasks)} 个知识点",
                progress=0.5,
            )
            # 构建知识点源列表并并行处理
            kp_sources = [
                SearchResult(
                    url=f"document://keypoint/{kp.get('title', '')}",
                    title=kp.get("title", ""),
                    snippet=kp.get("summary", ""),
                )
                for kp in kp_tasks
            ]
            all_kp_processed = await self.processor.process(kp_sources)
            yield PipelineProgress(
                stage="analyzing",
                message=f"已处理 {len(all_kp_processed)}/{len(kp_tasks)} 个知识点",
                progress=0.6,
            )
            # 并行生成所有知识点卡片
            sem = asyncio.Semaphore(5)
            kp_event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
            kp_completed = 0
            kp_total = len(all_kp_processed)

            async def build_kp_card(kp_idx: int, processed: ProcessedContent, parent_card_id: str | None):
                nonlocal kp_completed
                async with sem:
                    try:
                        cards: list[Card] = []
                        async for item in self.builder.build([processed], processed.source.title):
                            await kp_event_queue.put((kp_idx, parent_card_id, item))
                            if isinstance(item, Card):
                                cards.append(item)
                        self.persister.save(cards)
                        await self._index_cards(cards)
                    except Exception as e:
                        logger.warning(f"[Pipeline] Keypoint {kp_idx} build failed: {e}")
                    finally:
                        kp_completed += 1
                        await kp_event_queue.put((kp_idx, parent_card_id, None))

            for i, processed in enumerate(all_kp_processed):
                parent_id = kp_parent_ids[i] if i < len(kp_parent_ids) else None
                asyncio.create_task(build_kp_card(i, processed, parent_id))

            pending_kps = set(range(kp_total))
            while pending_kps:
                try:
                    kp_idx, parent_card_id, event = await asyncio.wait_for(kp_event_queue.get(), timeout=PIPELINE_TIMEOUT_QUEUE)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    pending_kps.discard(kp_idx)
                    progress = 0.6 + 0.25 * (1 - len(pending_kps) / kp_total)
                    yield PipelineProgress(
                        stage="analyzing",
                        message=f"知识点卡片生成进度: {kp_total - len(pending_kps)}/{kp_total}",
                        progress=min(progress, 0.84),
                    )
                    continue
                if isinstance(event, PipelineProgress):
                    yield event
                elif isinstance(event, Card):
                    detail_cards.append(event)
                    if parent_card_id:
                        try:
                            await LinkManager(self.card_store).create_link(parent_card_id, event.id, "expand")
                            if not event.parent_id:
                                event.parent_id = parent_card_id
                                self.persister.save([event])
                        except Exception as e:
                            logger.warning(f"[Pipeline] Link failed {parent_card_id} -> {event.id}: {e}")
                    yield event

        # 细节卡牌扩展：对每张细节卡牌调 expand 搜索互联网补充内容
        if detail_cards:
            yield PipelineProgress(
                stage="expanding",
                message=f"为 {len(detail_cards)} 张细节卡牌并行搜索扩展内容",
                progress=0.85,
            )
            sem = asyncio.Semaphore(3)
            event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
            completed = 0
            total = len(detail_cards)

            async def expand_detail_card(i: int, detail_card: Card):
                nonlocal completed
                async with sem:
                    try:
                        async for event in self.run_expand(
                            card_content=detail_card.content,
                            source_card_id=detail_card.id,
                            max_sources=2,
                            max_topics=3,
                            search_level="default",
                        ):
                            await event_queue.put(event)
                    except Exception as e:
                        logger.warning(f"[Pipeline] Expand failed for {detail_card.title}: {e}")
                    finally:
                        completed += 1
                        await event_queue.put(None)

            for i, detail_card in enumerate(detail_cards):
                asyncio.create_task(expand_detail_card(i, detail_card))

            while completed < total:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=PIPELINE_TIMEOUT_QUEUE)
                except asyncio.TimeoutError:
                    continue
                if event is None:
                    progress = 0.85 + 0.15 * (completed / total)
                    yield PipelineProgress(
                        stage="expanding",
                        message=f"已完成 {completed}/{total} 张卡牌扩展",
                        progress=min(progress, 0.99),
                    )
                    continue
                yield event

        yield PipelineProgress(
            stage="complete",
            message=f"文档分析完成: {filename}",
            progress=1.0,
        )

    async def run_expand(
        self,
        card_content: str,
        source_card_id: str,
        max_sources: int = 2,
        max_topics: int = 7,
        search_level: str = "default",
    ) -> AsyncIterator[PipelineProgress | Card]:
        yield PipelineProgress(
            stage="expanding", message="正在从卡片内容提取关键词...", progress=0.1
        )
        try:
            parent_title = None
            if source_card_id and self.card_store:
                parent_card = self.card_store.read_card(source_card_id)
                if parent_card:
                    parent_title = parent_card.title
            topics = await self.explorer.extract_topics(
                card_content, max_topics, search_level,
                exclude_title=parent_title, source_title=parent_title,
            )
        except Exception as e:
            logger.warning(f"[Pipeline] Failed to extract topics: {e}")
            yield PipelineProgress(stage="error", message=f"提取关键词失败: {e}")
            return

        logger.info("[Pipeline] run_expand extracted topics: %s", topics)
        if not topics:
            yield PipelineProgress(
                stage="complete", message="未找到相关关键词", progress=1.0
            )
            return

        merge_threshold = MERGE_DISTANCE_THRESHOLD
        merged_count = 0
        title_skipped = 0
        sem = asyncio.Semaphore(self.explore_concurrency)
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        completed = 0
        pending_topics: list[str] = []

        async def try_keyword_merge(topic: str) -> bool:
            """用 topic 关键词做向量搜索，匹配已有卡片则跳过（内容已覆盖，不建新卡）。

            注意：命中时**不再建链**——旧实现 create_link(source_card_id, existing.id)
            会把匹配到的已有卡（常是上位概念/根卡，如「免疫系统」）写成源卡的子卡，
            产生反向边污染 links/backlinks，导致 get_root_cards() 返回空、树构建全空
            （实测：expand「免疫器官」时「中枢免疫器官」~「免疫系统」dist=0.352，
            create_link(免疫器官, 免疫系统) 后 27 张卡全部带 backlinks，根卡消失）。
            merge 的职责是去重跳过，不是建链。
            """
            if not self.card_store or not self.embedder:
                return False
            try:
                emb = await self.embedder.encode_one(topic)
            except Exception:
                return False
            similar = self.card_store.vector_search(emb, limit=3, threshold=merge_threshold)
            for sid, d in similar:
                if sid == source_card_id:
                    continue
                existing = self.card_store.read_card(sid)
                if not existing:
                    continue
                logger.info(
                    "[Pipeline] Keyword merge: '%s' 已被已有卡片「%s」覆盖 (dist=%.3f)，跳过建卡",
                    topic, existing.title, d,
                )
                return True
            return False

        for topic in topics:
            # 标题预检（第一道闸，先于向量合并）：归一化精确匹配已有卡标题 → 跳过。
            # 零 API 成本（纯内存比对）；向量合并需要 encode + vector_search，命中时连
            # embedding 都省。实测锚定长词稀释 bge 相似度使 0.40 向量合并漏过 13 个重复
            # 主题（白做 ~300s 搜索+生成，最后才被 Persister 标题查重拦下）——精确预检
            # 把拦截点提前到搜索前。
            if self.card_store and find_card_by_normalized_title(
                self.card_store.list_cards(), topic, exclude_id=source_card_id,
            ):
                title_skipped += 1
                logger.info("[Pipeline] 标题预检: '%s' 已有同名卡片，跳过搜索", topic)
                continue
            if await try_keyword_merge(topic):
                merged_count += 1
                continue
            pending_topics.append(topic)

        total = len(pending_topics)
        if merged_count > 0 or title_skipped > 0:
            yield PipelineProgress(
                stage="expanding",
                message=f"去重: {title_skipped} 个标题已存在、{merged_count} 个语义已覆盖，{total} 个待探索",
                progress=0.15,
            )

        logger.info(
            "[Pipeline] run_expand topics=%d title_skipped=%d merged=%d pending=%s",
            len(topics), title_skipped, merged_count, pending_topics,
        )
        if not pending_topics:
            yield PipelineProgress(
                stage="complete",
                message=f"延申搜索完成，{merged_count} 个已合并",
                progress=1.0,
            )
            return

        yield PipelineProgress(
            stage="expanding",
            message=f"探索 {total} 个关键词: {', '.join(pending_topics[:5])}",
            progress=0.15,
        )

        async def explore_topic(topic: str):
            nonlocal completed
            async with sem:
                # 方案 B：不再拼接源卡标题做搜索锚定（query == topic 原样直通）。
                # 领域锚定职责前移至 extract_topics 提取阶段——主题自带领域限定
                # （如「猎人（杀戮尖塔2）」），输出即锚定；标题预检因此零剥离。
                sub_pipeline = Pipeline(
                    source=self.source,
                    processor=self.processor,
                    builder=self.builder,
                    persister=self.persister,
                    explorer=self.explorer,
                    max_explore_depth=0,
                    explore_concurrency=self.explore_concurrency,
                    card_store=self.card_store,
                )
                sub_pipeline._depth = 1
                try:
                    async for event in sub_pipeline._run_query(topic, max_sources, source_card_id, display_query=topic):
                        await event_queue.put(event)
                except Exception as e:
                    logger.warning(f"[Pipeline] Topic '{topic}' failed: {e}")
                finally:
                    await sub_pipeline.close()
                    await event_queue.put(None)

        for topic in pending_topics:
            asyncio.create_task(explore_topic(topic))

        while completed < total:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=PIPELINE_TIMEOUT_QUEUE)
            except asyncio.TimeoutError:
                continue

            if event is None:
                completed += 1
                progress = 0.15 + (completed / total) * 0.80
                yield PipelineProgress(
                    stage="expanding",
                    message=f"已完成 {completed}/{total} 个关键词",
                    progress=min(progress, 0.99),
                )
                continue

            if isinstance(event, PipelineProgress):
                yield event
            elif isinstance(event, Card) or hasattr(event, "id"):
                yield event

        yield PipelineProgress(
            stage="complete",
            message=f"延申搜索完成，探索了 {total} 个关键词",
            progress=1.0,
        )
    def _resolve_anchor(self, source_card_id: Optional[str]) -> Optional[str]:
        """搜索词的领域锚标题。

        优先级：源卡标题（expand/挂载上下文，最精确）→ 会话根卡标题
        （知识库主题即领域——agent 无源卡裸短词搜索场景，如「Zero」在
        MGSV 库里应锚定到根卡「合金装备5：幻痛」）。无可用锚返回 None。
        """
        if source_card_id and self.card_store:
            src = self.card_store.read_card(source_card_id)
            if src and src.title:
                return src.title
        if self.card_store:
            roots = self.card_store.get_root_cards()
            if roots and roots[0].title:
                return roots[0].title
        return None

    async def _run_query(
        self,
        query: str,
        max_sources: int = 2,
        source_card_id: Optional[str] = None,
        display_query: Optional[str] = None,
        persist: bool = True,
    ) -> AsyncIterator[PipelineProgress | Card]:
        step = 0.0
        # 预检/展示用原词（display_query 优先，方案 C 锚定后 query 会变长）
        precheck_title = display_query or query

        # 标题预检（方案 B）：搜索前归一化精确查重，命中直接跳过——兜住所有
        # _run_query 子路径（run_explore_task / agent 搜索 / run_expand 子流水线）。
        # persist=False 豁免：refresh_card 场景显式重新收集同名主题必须放行。
        if persist and self.card_store and find_card_by_normalized_title(
            self.card_store.list_cards(), precheck_title, exclude_id=source_card_id,
        ):
            logger.info("[Pipeline] 标题预检: '%s' 已有同名卡片，跳过搜索", precheck_title)
            yield PipelineProgress(
                stage="searching",
                message=f"「{precheck_title}」已存在同名卡片，跳过搜索",
                progress=0.3,
            )
            return

        # 领域锚定（方案 C 强化）：短词 + 领域锚 → 搜索词自带领域，让搜索
        # 结果本身就是领域内信息（用户方向：重点不是事后拦截漂移卡，而是
        # 搜索时就不要出现无关信息——#97 实证「猎人（Silent） 杀戮尖塔2」
        # top5 全领域内）。
        # 领域锚来源：源卡标题（expand/挂载上下文）→ 会话根卡标题
        # （知识库主题即领域，覆盖 agent 无源卡裸短词搜索场景）。
        # persist=False 豁免：refresh 搜的正是原卡标题本身，拼接反而搜偏。
        if persist and self.card_store and _needs_anchor(query):
            anchor = self._resolve_anchor(source_card_id)
            if anchor and anchor not in query:
                logger.info(
                    "[Pipeline] 领域锚定: '%s' -> '%s %s'", query, query, anchor,
                )
                query = f"{query} {anchor}"

        results = await self.source.search(query, max_sources)
        step += 0.15

        # 域名质量驱动的 URL 优先级筛选
        from backend.scraper.url_prioritizer import select_top
        results = select_top(results, query)
        step += 0.05

        if results:
            yield PipelineProgress(
                stage="searching",
                message=f"Found {len(results)} sources",
                progress=step,
            )

        processed = await self.processor.process(results)
        step += 0.25

        # ── 域名黑名单：抓取失败由 WebPageTextFetcher 自己调 record_failure()，DomainQualityCache 连续 3 次失败自动封 30 天 ──

        # ── 重搜熔断：最多 2 次，跨尝试去重 + 黑名单过滤 ──
        # 目标有效来源数按实际请求数自适应，允许 1 个失败：
        # 3 来源 → 需 2；5 来源 → 需 3；1 来源 → 需 1。
        # 搜索场景 processor 配置了 early_stop（合格来源达到即提前终止抓取），
        # 重搜目标必须与其一致——否则提前终止于 2 个后仍会触发无谓重搜。
        # 旧逻辑硬编码 3：3 来源时零容错，任何网络噪音都触发重搜。
        seen_urls = {r.url for r in results}
        re_search_attempt = 0
        max_re_search = 2
        early_stop = getattr(self.processor, "early_stop", 0) or 3
        target_sources = min(early_stop, max(1, len(results) - 1))

        while len(processed) < target_sources and re_search_attempt < max_re_search:
            re_search_attempt += 1
            self._re_search_count += 1
            logger.info("[Pipeline] Only %d effective sources, re-searching (attempt %d/%d): %s",
                        len(processed), re_search_attempt, max_re_search, query)
            yield PipelineProgress(
                stage="searching",
                message=f"有效链接仅 {len(processed)} 个，重新搜索补充 ({re_search_attempt}/{max_re_search})",
                progress=step,
            )
            # 重搜量随 max_sources 走，不硬编码：否则失败后滚雪球到 8+ 来源，卡片生成输入过长
            more_results = await self.source.search(query, max_results=max_sources * 2 + re_search_attempt * 2)
            fresh = [
                r for r in more_results
                if r.url not in seen_urls
                and not _dq().is_blocked(r.url)
            ]
            seen_urls.update(r.url for r in more_results)
            if fresh:
                logger.info("[Pipeline] Re-search found %d new URLs", len(fresh))
                more_processed = await self.processor.process(fresh)
                processed.extend(more_processed)
            else:
                logger.info("[Pipeline] Re-search returned no new URLs (all seen or blocked)")
                yield PipelineProgress(
                    stage="searching",
                    message=f"重新搜索未找到新链接 ({len(more_results)} 条全覆盖或被屏蔽)",
                    progress=step,
                )

        if processed:
            yield PipelineProgress(
                stage="scraping",
                message=f"Scraped and summarized {len(processed)} sources",
                progress=step,
            )
        else:
            yield PipelineProgress(
                stage="error",
                message=f"所有 {len(results)} 个来源均无法获取有效内容，尝试更换关键词",
                progress=step,
            )
            return

        cards: list[Card] = []
        logger.info("[Pipeline] Starting card generation from %d processed sources", len(processed))
        async for item in self.builder.build(processed, display_query or query):
            if isinstance(item, PipelineProgress):
                yield item
            else:
                cards.append(item)
                yield item
        step += 0.35

        if not cards:
            yield PipelineProgress(
                stage="scraping",
                message=f"AI 未能从 {len(processed)} 个来源中生成卡片",
                progress=step,
            )

        saved = self.persister.save(cards) if persist else []
        step += 0.05

        skipped = getattr(self.persister, "last_skipped", 0)
        if skipped > 0 and persist:
            logger.info("[Pipeline] Dedup: %d cards skipped (title match), %d saved", skipped, len(saved))
            yield PipelineProgress(
                stage="scraping",
                message=f"去重: 跳过 {skipped} 张重复卡片，保存 {len(saved)} 张",
                progress=step,
            )

        if self.embedder and self.card_store and saved:
            try:
                texts = [f"{c.title}\n{c.content}" for c in saved]
                embeddings = await self.embedder.encode(texts)
                self.card_store.index_vectors([c.id for c in saved], embeddings)
                logger.info("流水线已索引 %d 张卡片向量", len(saved))

                await self._score_new_cards(saved, embeddings)

            except Exception as e:
                logger.warning("流水线向量索引失败: %s", e)

        if source_card_id and saved and self.card_store:
            link_manager = LinkManager(self.card_store)
            for card in saved:
                try:
                    await link_manager.create_link(source_card_id, card.id, "expand")
                    # 显式树方向：新卡挂到源卡下（无向链接 + parent_id）
                    if not card.parent_id:
                        # 不能用 persister.save([card]) 更新——CardStorePersister.save 的
                        # 标题查重会把已入库的卡自己当重复跳过，parent_id 更新被吞
                        # （e2e 实证：links 对称正常但新卡 parent_id 全空）。
                        self.card_store.update_parent(card.id, source_card_id)
                except Exception as e:
                    logger.warning(f"[Pipeline] Link failed {source_card_id} -> {card.id}: {e}")

        if self._depth >= self.max_explore_depth:
            yield PipelineProgress(
                stage="complete",
                message=f"Pipeline complete, {len(saved)} cards generated",
                progress=1.0,
            )
            return

        explore_tasks: List[ExploreTask] = []
        async for task in self.explorer.explore(saved):
            task.depth = self._depth + 1
            explore_tasks.append(task)

        if not explore_tasks:
            yield PipelineProgress(
                stage="complete",
                message=f"Pipeline complete, {len(saved)} cards generated",
                progress=1.0,
            )
            return

        yield PipelineProgress(
            stage="expanding",
            message=f"Exploring {len(explore_tasks)} related topics",
            progress=step + 0.05,
        )

        sem = asyncio.Semaphore(self.explore_concurrency)
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        completed = 0
        total = len(explore_tasks)

        async def run_explore_task(task: ExploreTask):
            nonlocal completed
            async with sem:
                # 方案 B：不拼接父卡标题——task.query 来自 extract_related_topics
                # （主题自带领域限定，输出即锚定），原样直通；标题预检由 _run_query 兜底。
                sub_pipeline = Pipeline(
                    source=self.source,
                    processor=self.processor,
                    builder=self.builder,
                    persister=self.persister,
                    explorer=self.explorer,
                    max_explore_depth=self.max_explore_depth,
                    explore_concurrency=self.explore_concurrency,
                    card_store=self.card_store,
                    embedder=self.embedder,
                )
                sub_pipeline._depth = task.depth
                try:
                    async for event in sub_pipeline._run_query(
                        task.query, max_sources, task.parent_card_id,
                        display_query=task.query
                    ):
                        await event_queue.put(event)
                except Exception as e:
                    logger.warning(f"[Pipeline] Explore task failed for '{task.query}': {e}")
                finally:
                    await sub_pipeline.close()
                    await event_queue.put(None)

        for task in explore_tasks:
            asyncio.create_task(run_explore_task(task))

        last_progress = step + 0.05
        while completed < total:
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=PIPELINE_TIMEOUT_QUEUE)
            except asyncio.TimeoutError:
                continue

            if event is None:
                completed += 1
                progress = step + 0.05 + (completed / total) * 0.35
                if progress - last_progress >= 0.05 or completed == total:
                    last_progress = progress
                    yield PipelineProgress(
                        stage="expanding",
                        message=f"Explored {completed}/{total} topics",
                        progress=min(progress, 0.99),
                    )
                continue

            if isinstance(event, PipelineProgress):
                yield PipelineProgress(
                    stage=event.stage,
                    message=event.message,
                    progress=min(event.progress, 0.99),
                    current_item=event.current_item,
                    ai_output=event.ai_output,
                )
            elif isinstance(event, Card) or hasattr(event, "id"):
                yield event

        yield PipelineProgress(
            stage="complete",
            message=f"Pipeline complete, {len(saved) + (total)} topics explored",
            progress=1.0,
        )

    async def _index_cards(self, cards: list[Card]) -> None:
        if not self.embedder or not self.card_store or not cards:
            return
        try:
            texts = [f"{c.title}\n{c.content}" for c in cards]
            embeddings = await self.embedder.encode(texts)
            self.card_store.index_vectors([c.id for c in cards], embeddings)
            logger.info("文档流水线已索引 %d 张卡片向量", len(cards))
        except Exception as e:
            logger.warning("文档流水线向量索引失败: %s", e)

    async def _score_new_cards(self, new_cards: list[Card], new_embeddings: list[list[float]]) -> None:
        """仅作新卡批次的临时诊断日志，不是 canonical 四维质量分。

        正式评分统一走 backend/quality/scorer.score_all_cards（结构+图论+语义+置信度），
        此处因新卡尚未挂载/链接，无法得到最终图论信号，因此只记录结构+语义批内信号。
        """
        try:
            from backend.quality.scorer import compute_structure_score, compute_semantic_score, content_completeness, source_richness, link_density
        except ImportError:
            logger.debug("backend.quality.scorer not available, skipping quality scoring")
            return

        batch_embeddings = {c.id: e for c, e in zip(new_cards, new_embeddings)}

        for card, emb in zip(new_cards, new_embeddings):
            structure = compute_structure_score(card)
            semantic = compute_semantic_score(emb, batch_embeddings, card.id) if len(new_cards) > 1 else 0.5

            dims = {
                "content": content_completeness(card),
                "sources": source_richness(card),
                "links": link_density(card),
            }
            batch_signal = 0.55 * structure + 0.45 * semantic
            batch_gap = round(1.0 - batch_signal, 4)

            logger.info(
                "[Pipeline] New-card batch signal (not final quality): %s gap=%.2f struct=%.2f sem=%.2f dims=%s",
                card.title, batch_gap, structure, semantic,
                ",".join(f"{k}={v:.2f}" for k, v in dims.items()),
            )

    async def _load_existing_embeddings(self) -> dict[str, list[float]]:
        emb_map: dict[str, list[float]] = {}
        if not self.card_store or not self.embedder:
            return emb_map
        try:
            all_cards = self.card_store.list_cards()
            if not all_cards:
                return emb_map

            texts = [f"{c.title}\n{c.content}" for c in all_cards]
            embeddings = await self.embedder.encode(texts)
            for c, emb in zip(all_cards, embeddings):
                emb_map[c.id] = emb
        except Exception as e:
            logger.warning("加载已有向量失败: %s", e)
        return emb_map

    async def close(self):
        for stage in [self.source, self.processor, self.builder, self.explorer]:
            try:
                await stage.close()
            except Exception as e:
                logger.warning("Pipeline stage close failed: %s", e)

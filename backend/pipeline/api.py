"""
Pipeline API — 面向 AI Agent 的程序化调用接口。

将 SSE 流式 Pipeline 封装为同步/异步方法，返回具体数据类型。
为未来引入 AI Agent 提供干净的 Python API，不依赖 HTTP/SSE。

Usage:
    api = PipelineAPI(username="user", session_id=DEFAULT_SESSION_ID)
    cards = await api.search_by_keyword("机器学习", max_sources=2)
    card = api.get_card_info(card_id)
    similar = await api.search_similar_cards("神经网络", limit=10)

设计原则：
- 所有搜索/扩展方法为 async（涉及网络+AI）
- 所有 CRUD 方法为 sync（纯 SQLite 操作）
- 返回具体数据类型，不使用 generator
- 内部处理异常并记录日志，不向调用方抛出未预期异常
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from datetime import datetime
from typing import Dict, List, Optional
import uuid

from backend.config import DEFAULT_SESSION_ID, PIPELINE_TIMEOUT_SYNC
from backend.models.card import Card
from backend.storage.raw_store import RawPageStore
from backend.storage.sqlite_card_store import SqliteCardStore
from backend.links.manager import LinkManager
from backend.ai.embedder import Embedder
from backend.pipeline.factory import create_pipeline
from backend.pipeline.stages import PipelineProgress
from backend.task import TaskService
from backend.search import DEFAULT_SEARCH_PROVIDER

logger = logging.getLogger(__name__)


class PipelineAPI:
    """面向 Agent 的流水线封装。

    提供关键词搜索、卡牌扩展、卡牌 CRUD、语义搜索、树形结构等方法。
    所有方法均可从 Python 代码直接调用，无需 HTTP 请求。

    Attributes:
        username: 当前用户名
        session_id: 当前会话 ID
        card_store: 卡牌存储实例
    """

    def __init__(self, username: str, session_id: str = DEFAULT_SESSION_ID):
        self.username = username
        self.session_id = session_id
        self.card_store = SqliteCardStore(username=username, session_id=session_id)
        self.raw_store = RawPageStore(username=username, session_id=session_id)

    @staticmethod
    def _run_async(coro):
        """在同步上下文中安全运行异步协程。

        委托给 backend.utils.async_bridge.run_async_in_thread。
        仅用于纯 I/O 操作（数据库读写），不涉及 PyTorch 推理。
        嵌入编码请使用 Embedder.encode_sync() + ThreadPoolExecutor。

        Args:
            coro: 协程对象

        Returns:
            协程的返回值，超时或失败返回 None
        """
        from backend.utils.async_bridge import run_async_in_thread
        return run_async_in_thread(coro)

    # ── 2.1 关键词搜索 ────────────────────────────────────────────

    async def search_by_keyword(
        self,
        keyword: str,
        max_sources: int = 2,
        search_level: str = "default",
        search_provider: str = DEFAULT_SEARCH_PROVIDER,
        source_card_id: str | None = None,
    ) -> List[Card]:
        """按关键词搜索并收集知识卡片。

        启动完整的搜索流水线（搜索→抓取→摘要→生成卡片），
        收集所有生成的 Card 对象并以列表形式返回。

        Args:
            keyword: 搜索关键词
            max_sources: 最大搜索来源数（默认 5）
            search_level: 搜索级别（default/downstream/peer/upstream，即 默认/下级/平级/上级）；
                仅影响「提取哪些主题」，不影响搜索、抓取与合并
            search_provider: 搜索服务商（free/bocha/baidu/exa）

        Returns:
            生成的 Card 对象列表，失败时返回空列表
        """
        cards: List[Card] = []
        pipeline = None
        try:
            pipeline = create_pipeline(
                self.username,
                self.session_id,
                search_level=search_level,
                search_provider=search_provider,
                # max_explore_depth=0：纯搜索只生成目标卡片，不自动探索联想主题
                # （gap_driven/refresh/improver 等调用方均期望"搜完即停"，探索由
                #   expand_from_card 显式触发；与 collect 路由和 Agent 路径行为对齐）
                max_explore_depth=0,
            )
            async for item in pipeline.run(keyword, max_sources=max_sources):
                if isinstance(item, Card):
                    cards.append(item)
            if source_card_id and cards:
                self._attach_to_parent(source_card_id, cards)
            logger.info(
                "PipelineAPI.search_by_keyword: keyword=%r → %d cards",
                keyword, len(cards),
            )
        except Exception as e:
            logger.error("PipelineAPI.search_by_keyword failed: %s", e)
        finally:
            if pipeline:
                await pipeline.close()
        return cards

    async def search_by_keyword_with_task(
        self,
        keyword: str,
        max_sources: int = 2,
        search_level: str = "default",
        search_provider: str = DEFAULT_SEARCH_PROVIDER,
        source_card_id: str | None = None,
        external_task = None,
        persist: bool = True,
        explore: bool = False,
    ) -> tuple[List[Card], str | None]:
        """按关键词搜索，同时创建 Task 供收集器页面订阅 SSE 进度。

        persist=False 时生成候选卡片但不写入知识库（refresh_card 场景：只取内容更新原卡）。
        explore=True 时自动扩展相关主题（需显式开启——默认关闭，
        与设计文档"搜索只生成目标卡片"一致，扩展由 expand_from_card 显式控制）。
        """
        from backend.models.task import TaskType, TaskStatus

        cards: List[Card] = []
        task_id: str | None = None
        task = external_task
        pipeline = None
        try:
            if task is None:
                task = TaskService.get_instance().create(
                    task_type=TaskType.COLLECT,
                    username=self.username,
                    session_id=self.session_id,
                    keyword=keyword,
                    params={"max_sources": max_sources, "search_level": search_level},
                )
            task_id = task.task_id
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.updated_at = datetime.utcnow()

            pipeline = create_pipeline(
                self.username, self.session_id,
                search_level=search_level, search_provider=search_provider,
                max_explore_depth=1 if explore else 0,
            )
            async for item in pipeline.run(keyword, max_sources=max_sources, persist=persist):
                if isinstance(item, Card):
                    cards.append(item)
                    await task.emit("card", item)
                elif hasattr(item, "stage"):
                    from backend.models.task import TaskProgress
                    progress = TaskProgress(
                        stage=getattr(item, "stage", ""),
                        message=getattr(item, "message", ""),
                        progress=getattr(item, "progress", 0.0),
                        current_item=getattr(item, "current_item", None),
                        ai_output=getattr(item, "ai_output", None),
                    )
                    await task.emit("progress", progress)

            if source_card_id and cards:
                self._attach_to_parent(source_card_id, cards)
            await task.emit("complete", {"message": f"生成 {len(cards)} 张卡片"})
            logger.info(
                "PipelineAPI.search_by_keyword_with_task: keyword=%r → %d cards, task=%s",
                keyword, len(cards), task_id,
            )
        except asyncio.CancelledError:
            logger.info("PipelineAPI.search_by_keyword_with_task cancelled")
            if task:
                task.status = TaskStatus.CANCELLED
                task.error = "任务被取消"
        except Exception as e:
            logger.error("PipelineAPI.search_by_keyword_with_task failed: %s", e)
            if task:
                task.error = str(e)
                await task.emit("error", {"message": str(e)})
        finally:
            if pipeline:
                await pipeline.close()
        return cards, task_id

    # ── 2.2 卡牌联想扩展 ──────────────────────────────────────────

    async def expand_from_card(
        self,
        card_id: str,
        max_topics: int = 7,
        search_level: str = "default",
        search_provider: str = DEFAULT_SEARCH_PROVIDER,
    ) -> List[Card]:
        """从指定卡牌内容出发，联想扩展生成新卡牌。

        读取卡牌内容 → AI 提取关键词 → 每个关键词搜索生成新卡牌。
        支持四级搜索模式（default/lower/sibling/parent）。

        Args:
            card_id: 源卡牌 ID
            max_topics: 最大联想关键词数（默认 7）
            search_level: 搜索级别
            search_provider: 搜索服务商

        Returns:
            新生成的 Card 对象列表，卡牌不存在或失败时返回空列表
        """
        card = self.card_store.read_card(card_id)
        if card is None:
            logger.warning("PipelineAPI.expand_from_card: card not found id=%s", card_id)
            return []

        cards: List[Card] = []
        pipeline = None
        try:
            pipeline = create_pipeline(
                self.username,
                self.session_id,
                search_level=search_level,
                max_topics=max_topics,
                search_provider=search_provider,
            )
            async for item in pipeline.run_expand(
                card_content=card.content,
                source_card_id=card_id,
                max_sources=2,
                max_topics=max_topics,
                search_level=search_level,
            ):
                if isinstance(item, Card):
                    cards.append(item)
            logger.info(
                "PipelineAPI.expand_from_card: card=%r → %d new cards",
                card.title, len(cards),
            )
        except Exception as e:
            logger.error("PipelineAPI.expand_from_card failed: %s", e)
        finally:
            if pipeline:
                await pipeline.close()
        return cards

    async def expand_from_card_with_task(
        self,
        card_id: str,
        max_topics: int = 7,
        search_level: str = "default",
        search_provider: str = DEFAULT_SEARCH_PROVIDER,
        external_task = None,
    ) -> tuple[List[Card], str | None]:
        card = self.card_store.read_card(card_id)
        if card is None:
            return [], None

        from backend.models.task import TaskType, TaskStatus

        cards: List[Card] = []
        task_id: str | None = None
        task = external_task
        pipeline = None
        try:
            if task is None:
                task = TaskService.get_instance().create(
                    task_type=TaskType.EXPAND,
                    username=self.username,
                    session_id=self.session_id,
                    keyword=f"延申: {card.title}",
                    params={"source_card_id": card_id, "max_topics": max_topics},
                )
            task_id = task.task_id
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.updated_at = datetime.utcnow()

            pipeline = create_pipeline(
                self.username, self.session_id,
                search_level=search_level, max_topics=max_topics,
                search_provider=search_provider,
            )
            async for item in pipeline.run_expand(
                card_content=card.content, source_card_id=card_id,
                max_sources=2, max_topics=max_topics, search_level=search_level,
            ):
                if isinstance(item, Card):
                    cards.append(item)
                    await task.emit("card", item)
                elif hasattr(item, "stage"):
                    from backend.models.task import TaskProgress
                    progress = TaskProgress(
                        stage=getattr(item, "stage", ""),
                        message=getattr(item, "message", ""),
                        progress=getattr(item, "progress", 0.0),
                        current_item=getattr(item, "current_item", None),
                        ai_output=getattr(item, "ai_output", None),
                    )
                    await task.emit("progress", progress)

            await task.emit("complete", {"message": f"生成 {len(cards)} 张卡片"})
            logger.info(
                "PipelineAPI.expand_from_card_with_task: card=%r → %d cards, task=%s",
                card.title, len(cards), task_id,
            )
        except asyncio.CancelledError:
            logger.info("PipelineAPI.expand_from_card_with_task cancelled")
            if task:
                task.status = TaskStatus.CANCELLED
                task.error = "任务被取消"
        except Exception as e:
            logger.error("PipelineAPI.expand_from_card_with_task failed: %s", e)
            if task:
                task.error = str(e)
                await task.emit("error", {"message": str(e)})
        finally:
            if pipeline:
                await pipeline.close()
        return cards, task_id

    # ── 2.3 读取卡牌信息 ─────────────────────────────────────────

    def get_card_info(self, card_id: str) -> Optional[Card]:
        """读取单张卡牌的完整信息。

        Args:
            card_id: 卡牌 ID

        Returns:
            Card 对象，不存在时返回 None
        """
        return self.card_store.read_card(card_id)

    # ── 2.4 获取关联卡牌 ──────────────────────────────────────────

    def get_linked_cards(self, card_id: str) -> List[str]:
        """获取与指定卡牌关联的所有卡牌 ID（双向）。

        包含出链（links）和入链（backlinks），去重后返回。

        Args:
            card_id: 卡牌 ID

        Returns:
            关联卡牌 ID 列表（去重），卡牌不存在时返回空列表
        """
        card = self.card_store.read_card(card_id)
        if card is None:
            logger.warning("PipelineAPI.get_linked_cards: card not found id=%s", card_id)
            return []
        linked = list(set(
            (card.links or []) + (card.backlinks or [])
        ))
        return linked

    def _attach_to_parent(self, source_card_id: str, cards: List[Card]) -> None:
        """新卡挂载到源卡下：建立无向链接 + 显式设置 parent_id（树方向）。

        链接无向（create_link 对称）；树方向由 parent_id 显式承载，
        之后 get_children/get_root_cards 不再依赖创建时间启发式。
        """
        if not source_card_id or not cards:
            return
        link_mgr = LinkManager(self.card_store)
        for card in cards:
            self._run_async(link_mgr.create_link(source_card_id, card.id, "expand"))
            if not card.parent_id:
                card.parent_id = source_card_id
                try:
                    self.card_store.update_card(card)
                except Exception as e:
                    logger.warning("PipelineAPI._attach_to_parent: update failed: %s", e)

    def link_cards(self, card_id_a: str, card_id_b: str, parent: str | None = None) -> bool:
        """在两个卡牌之间建立无向链接。

        链接是无向的（Obsidian 风格）：link_cards(a, b) 与 link_cards(b, a) 效果相同，
        双方 links/backlinks 对称维护；已存在则幂等无副作用。

        Args:
            card_id_a: 卡牌 A 的 ID
            card_id_b: 卡牌 B 的 ID
            parent: 可选树方向提示——'a' 表示把 card_id_a 设为 card_id_b 的父卡，
                'b' 反之；省略则只建立无向引用链接，不改变树结构。

        Returns:
            True 表示链接创建成功，False 表示失败（如卡片不存在）
        """
        mgr = LinkManager(self.card_store)
        ok, warnings = self._run_async(mgr.create_link(card_id_a, card_id_b))
        if warnings:
            for w in warnings:
                logger.warning("PipelineAPI.link_cards: %s", w.message)
        if ok and parent == "a":
            self.card_store.update_parent(card_id_b, card_id_a)
        elif ok and parent == "b":
            self.card_store.update_parent(card_id_a, card_id_b)
        return ok

    # ── 2.5 语义搜索 ──────────────────────────────────────────────

    async def search_similar_cards(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> List[dict]:
        """语义搜索当前会话中的卡牌。

        使用 bge-small-zh-v1.5 编码查询文本，通过 sqlite-vec
        向量索引进行 KNN 搜索，返回语义相似度最高的卡牌。

        Args:
            query: 搜索文本
            limit: 最大返回数量（默认 10）
            threshold: 距离阈值（默认 0.7，越小越相似）

        Returns:
            [{"card": Card, "score": float}, ...] 列表，按相似度降序排列
        """
        embedder = Embedder.get()
        try:
            embedding = await embedder.encode_one(query)
        except Exception as e:
            logger.error("PipelineAPI.search_similar_cards: encode failed: %s", e)
            return []

        results = self.card_store.vector_search(embedding, limit, threshold)
        cards = []
        for cid, dist in results:
            card = self.card_store.read_card(cid)
            if card:
                cards.append({
                    "card": card,
                    "score": round(1.0 - dist, 4),
                })
        logger.info(
            "PipelineAPI.search_similar_cards: query=%r → %d results", query, len(cards),
        )
        return cards

    # ── 2.6 列出卡牌 ──────────────────────────────────────────────

    def list_cards(self) -> List[Card]:
        """列出当前会话中的所有卡牌。

        Returns:
            Card 对象列表
        """
        return self.card_store.list_cards()

    # ── 2.7 扩展方法 ──────────────────────────────────────────────

    def get_card_tree(self) -> List[dict]:
        """获取当前会话的完整卡牌树形结构。

        以根卡牌（无入链）为起点，递归构建父子层级树。

        Returns:
            [{"card": Card, "children": [...]}, ...] 递归树形结构
        """
        all_cards = self.card_store.list_cards()
        card_map = {c.id: c for c in all_cards}

        def _build_node(card: Card, visited: frozenset) -> dict:
            # visited 防环：历史数据可能有错层/反向边，递归必须终结
            if card.id in visited:
                return {"card": card.model_dump(mode="json"), "children": []}
            children = self.card_store.get_children(card.id)
            return {
                "card": card.model_dump(mode="json"),
                "children": [_build_node(c, visited | {card.id}) for c in children],
            }

        roots = self.card_store.get_root_cards()
        return [_build_node(root, frozenset()) for root in roots]

    def get_card_children(self, card_id: str) -> List[Card]:
        """获取指定卡牌的所有子卡牌。

        子卡牌通过 links 字段中出现在其他卡牌 backlinks 中的方式识别。

        Args:
            card_id: 父卡牌 ID

        Returns:
            子卡牌列表
        """
        return self.card_store.get_children(card_id)

    def create_card(
        self,
        title: str,
        content: str = "",
        parent_id: Optional[str] = None,
        metadata: Optional[Dict] = None,
    ) -> Optional[Card]:
        """创建一张新卡牌，可选关联父卡牌。

        Args:
            title: 卡牌标题
            content: 卡牌内容（支持 Markdown）
            parent_id: 父卡牌 ID（可选，建立 parent 链接）
            metadata: 自定义元数据字典（可选）

        Returns:
            创建的 Card 对象，失败返回 None
        """
        try:
            now = datetime.utcnow()
            card = Card(
                id=str(uuid.uuid4()),
                title=title,
                content=content,
                metadata=metadata or {},
                links=[],
                backlinks=[],
                parent_id=parent_id,
                created_at=now,
                updated_at=now,
                sources=[],
                confidence=0.5,
                tags=[],
            )
            self.card_store.create_card(card)

            if parent_id:
                link_manager = LinkManager(self.card_store)
                self._run_async(link_manager.create_link(parent_id, card.id, "parent"))

            logger.info(
                "PipelineAPI.create_card: title=%r parent=%s → id=%s",
                title, parent_id, card.id,
            )
            return card
        except Exception as e:
            logger.error("PipelineAPI.create_card failed: %s", e)
            return None

    def update_card(
        self,
        card_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        metadata: Optional[Dict] = None,
        sources: Optional[List[str]] = None,
    ) -> Optional[Card]:
        """更新卡牌的标题/内容/元数据/来源。

        Args:
            card_id: 卡牌 ID
            title: 新标题（None 表示不修改）
            content: 新内容（None 表示不修改）
            metadata: 新元数据（None 表示不修改）
            sources: 新来源列表（None 表示不修改）

        Returns:
            更新后的 Card 对象，卡牌不存在或失败返回 None
        """
        try:
            existing = self.card_store.read_card(card_id)
            if existing is None:
                logger.warning("PipelineAPI.update_card: card not found id=%s", card_id)
                return None

            if title is not None:
                existing.title = title
            if content is not None:
                existing.content = content
            if metadata is not None:
                existing.metadata = metadata
            if sources is not None:
                existing.sources = sources
            existing.updated_at = datetime.utcnow()

            self.card_store.update_card(existing)
            logger.info(
                "PipelineAPI.update_card: id=%s updated", card_id,
            )
            return existing
        except Exception as e:
            logger.error("PipelineAPI.update_card failed: %s", e)
            return None

    def delete_card(self, card_id: str) -> bool:
        """删除指定卡牌。

        Args:
            card_id: 卡牌 ID

        Returns:
            是否成功删除
        """
        try:
            success = self.card_store.delete_card(card_id)
            if success:
                logger.info("PipelineAPI.delete_card: id=%s deleted", card_id)
            else:
                logger.warning("PipelineAPI.delete_card: card not found id=%s", card_id)
            return success
        except Exception as e:
            logger.error("PipelineAPI.delete_card failed: %s", e)
            return False

    def create_link(self, from_id: str, to_id: str, link_type: str = "related") -> bool:
        """创建两张卡牌之间的双向链接。

        自动维护 links ↔ backlinks 对称性。

        Args:
            from_id: 源卡牌 ID
            to_id: 目标卡牌 ID
            link_type: 链接类型（parent/expand/related）

        Returns:
            是否成功创建
        """
        try:
            link_manager = LinkManager(self.card_store)
            self._run_async(link_manager.create_link(from_id, to_id, link_type))

            logger.info(
                "PipelineAPI.create_link: %s → %s (%s)", from_id, to_id, link_type,
            )
            return True
        except Exception as e:
            logger.error("PipelineAPI.create_link failed: %s", e)
            return False

    def count_cards(self) -> int:
        """获取当前会话的卡牌总数。

        Returns:
            卡牌数量
        """
        cards = self.card_store.list_cards()
        return len(cards)

    def get_root_cards(self) -> List[Card]:
        """获取根卡牌列表（无入链的顶层卡牌）。

        Returns:
            根卡牌列表
        """
        return self.card_store.get_root_cards()

    def get_outgoing_links(self, card_id: str) -> List[str]:
        """获取卡牌的出链 ID 列表。

        Args:
            card_id: 卡牌 ID

        Returns:
            出链卡牌 ID 列表
        """
        card = self.card_store.read_card(card_id)
        return list(card.links or []) if card else []

    def get_incoming_links(self, card_id: str) -> List[str]:
        """获取卡牌的入链 ID 列表（反向引用）。

        Args:
            card_id: 卡牌 ID

        Returns:
            入链卡牌 ID 列表
        """
        card = self.card_store.read_card(card_id)
        return list(card.backlinks or []) if card else []

    def score_cards_quality(self) -> List[dict]:
        cards = self.card_store.list_cards()
        if not cards:
            return []

        embeddings = self._load_all_embeddings(cards)
        from backend.quality.scorer import score_all_cards
        return score_all_cards(cards, embeddings, raw_store=self.raw_store)

    def find_quality_gaps(self, top_n: int = 5, threshold: float = 0.5) -> List[dict]:
        cards = self.card_store.list_cards()
        if not cards:
            return []

        embeddings = self._load_all_embeddings(cards)
        from backend.quality.scorer import find_weakest_cards
        return find_weakest_cards(cards, embeddings, top_n=top_n, threshold=threshold, raw_store=self.raw_store)

    def _load_all_embeddings(self, cards: List[Card]) -> Optional[Dict[str, List[float]]]:
        """全量加载嵌入向量。

        使用 Embedder.encode_sync() + ThreadPoolExecutor 直接编码，
        绕过 asyncio.run() 避免 PyTorch 在非主线程事件循环清理时卡死。
        """
        try:
            from backend.ai.embedder import Embedder

            embedder = Embedder.get()
            texts = [f"{c.title}\n{c.content}" for c in cards]

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(embedder.encode_sync, texts)
                try:
                    embeddings = future.result(timeout=PIPELINE_TIMEOUT_SYNC)
                except concurrent.futures.TimeoutError:
                    logger.error(
                        "PipelineAPI._load_all_embeddings timed out after %ds",
                        PIPELINE_TIMEOUT_SYNC,
                    )
                    return None

            return {cards[i].id: embeddings[i] for i in range(len(cards))}
        except Exception as e:
            logger.warning("PipelineAPI._load_all_embeddings failed: %s", e)
            return None

    # ── Gap-Driven 探索 ──────────────────────────────────────────

    async def gap_driven_exploration_with_task(
        self,
        keyword: str,
        max_sources: int = 2,
        gap_threshold: float = 0.5,
        structure_threshold: float = 0.5,
        max_iterations: int = 3,
        search_level: str = "default",
        search_provider: str = DEFAULT_SEARCH_PROVIDER,
        external_task=None,
    ) -> tuple[List[Card], str | None]:
        """Gap-Driven 探索循环，支持 Task SSE 流式输出。

        决策逻辑（符合 System Prompt 设计）：
        - structure_score 低 → 内容不足 → refresh_card（补充内容）
        - structure_score 尚可 → 语义覆盖不足 → expand_from_card（扩展子主题）

        Args:
            keyword: 搜索关键词
            max_sources: 每次搜索的最大来源数
            gap_threshold: gap_score 阈值，超过此值视为薄弱
            structure_threshold: structure_score 阈值，低于此值视为内容不足
            max_iterations: 最大迭代次数
            search_level: 搜索级别
            search_provider: 搜索提供商
            external_task: 外部已创建的 Task 对象（复用而非重新创建）

        Returns:
            (生成的卡片列表, 任务ID)
        """
        from backend.models.task import TaskType, TaskProgress, TaskStatus

        cards: List[Card] = []
        task_id: str | None = None
        task = external_task

        try:
            if task is None:
                task = TaskService.get_instance().create(
                    task_type=TaskType.GAP_DRIVEN,
                    username=self.username,
                    session_id=self.session_id,
                    keyword=keyword,
                    params={
                        "gap_threshold": gap_threshold,
                        "structure_threshold": structure_threshold,
                        "max_iterations": max_iterations,
                    },
                )
            task_id = task.task_id
            if task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.updated_at = datetime.utcnow()

            await task.emit("progress", TaskProgress(
                stage="analyzing",
                message="正在评估知识库卡片质量...",
                progress=0.05,
            ))

            for iteration in range(max_iterations):
                progress_base = 0.15 + (0.75 * iteration / max_iterations)

                await task.emit("progress", TaskProgress(
                    stage="analyzing",
                    message=f"迭代 {iteration + 1}/{max_iterations}: 正在查找薄弱卡片...",
                    progress=progress_base,
                ))

                weakest = self.find_quality_gaps(top_n=3, threshold=gap_threshold)

                if not weakest:
                    await task.emit("progress", TaskProgress(
                        stage="complete",
                        message=f"所有卡片质量达标（gap_score < {gap_threshold}）",
                        progress=0.95,
                    ))
                    break

                for i, gap_info in enumerate(weakest):
                    card_title = gap_info["title"]
                    struct_score = gap_info["structure_score"]
                    gap_score = gap_info["gap_score"]
                    card_id = gap_info["card_id"]

                    if struct_score < structure_threshold:
                        await task.emit("progress", TaskProgress(
                            stage="refreshing",
                            message=f"「{card_title}」内容不足（structure={struct_score:.2f}），正在刷新...",
                            progress=progress_base + (0.2 * (i + 0.5) / len(weakest)),
                            current_item=card_title,
                        ))

                        new_cards = await self._refresh_card_for_gap(
                            card_id, task, search_level, search_provider
                        )
                    else:
                        await task.emit("progress", TaskProgress(
                            stage="searching",
                            message=f"「{card_title}」需要扩展（gap={gap_score:.2f}），正在搜索补充...",
                            progress=progress_base + (0.2 * (i + 0.5) / len(weakest)),
                            current_item=card_title,
                        ))

                        # 用卡片标题作为关键词直接搜索，而非 expand_from_card
                        # expand_from_card 会触发 AI 联想 → 递归探索 → 无限级联
                        # search_by_keyword 仅搜索 + 生成卡片 + 停，可控
                        new_cards = await self.search_by_keyword(
                            card_title, max_sources=2, search_level=search_level,
                            search_provider=search_provider,
                        )

                        # 去重：排除与已有卡片标题完全相同的
                        existing_titles = {c.title for c in self.card_store.list_cards()}
                        new_cards = [c for c in new_cards if c.title not in existing_titles]

                        if not new_cards:
                            await task.emit("progress", TaskProgress(
                                stage="searching",
                                message=f"「{card_title}」搜索完成，未产生新卡片",
                                progress=progress_base + (0.2 * (i + 1) / len(weakest)),
                                current_item=card_title,
                            ))
                            continue

                    cards.extend(new_cards)
                    for card in new_cards:
                        await task.emit("card", card)

            await task.emit("complete", {
                "message": f"Gap-Driven 探索完成，共生成 {len(cards)} 张卡片",
                "total_cards": len(cards),
            })

            logger.info(
                "PipelineAPI.gap_driven_exploration_with_task: keyword=%r → %d cards, task=%s",
                keyword, len(cards), task_id,
            )

        except asyncio.CancelledError:
            logger.info("gap_driven_exploration_with_task cancelled")
            if task:
                task.status = TaskStatus.CANCELLED
                task.error = "任务被取消"
        except Exception as e:
            logger.error("gap_driven_exploration_with_task failed: %s", e)
            if task:
                await task.emit("error", {"message": str(e)})
        return cards, task_id

    async def _refresh_card_for_gap(
        self,
        card_id: str,
        task,
        search_level: str = "default",
        search_provider: str = DEFAULT_SEARCH_PROVIDER,
    ) -> List[Card]:
        """刷新卡片内容，用于 Gap-Driven 循环。

        重新搜索原卡片标题，选择最佳结果更新原卡片内容。

        Args:
            card_id: 要刷新的卡片 ID
            task: Task 对象，用于发送进度事件
            search_level: 搜索级别
            search_provider: 搜索提供商

        Returns:
            新生成的卡片列表
        """
        from backend.models.task import TaskProgress

        original = self.card_store.read_card(card_id)
        if not original:
            return []

        new_cards = await self.search_by_keyword(
            original.title, max_sources=2, search_level=search_level,
            search_provider=search_provider,
        )
        if not new_cards:
            return []

        best = max(new_cards, key=lambda c: (len(c.content or ""), len(c.sources or [])))
        old_len = len(original.content or "")

        self.update_card(
            card_id=card_id,
            content=best.content,
            metadata=best.metadata,
        )

        await task.emit("progress", TaskProgress(
            stage="refreshed",
            message=f"已刷新「{original.title}」: {old_len} → {len(best.content or '')} 字",
            progress=0.8,
            current_item=original.title,
        ))

        return new_cards

from __future__ import annotations

import logging

from backend.pipeline.pipeline import Pipeline
from backend.pipeline.defaults import (
    ExaSourceProvider,
    BaiduSourceProvider,
    BochaSourceProvider,
    FreeSourceProvider,
    WebPageTextFetcher,
    DocumentChunkFetcher,
    ScrapeAndSummarizeProcessor,
    LLMCardBuilder,
    CardStorePersister,
    RelatedTopicExplorer,
)
from backend.search import (
    SearchClient,
    BaiduSearchClient,
    BochaSearchClient,
    FreeSearchClient,
    DEFAULT_SEARCH_PROVIDER,
)
from backend.search.hybrid import HybridSearchClient
from backend.search.fallback import AutoFallbackSearchClient
from backend.config import SEARCH_AUTO_FALLBACK
from backend.scraper import WebFetcher
from backend.storage.raw_store import RawPageStore
from backend.ai import OpenAIProvider
from backend.ai.config import load_config
from backend.ai.embedder import Embedder
from backend.config import DEFAULT_SESSION_ID
from backend.storage import SqliteCardStore

logger = logging.getLogger(__name__)


def build_search_source(search_provider: str = DEFAULT_SEARCH_PROVIDER):
    """按 provider 名构造搜索源（统一入口，收集/扩展/文档流水线共用）。

    - free  : 免费多引擎（Bing/AnySearch/Exa-MCP/DDG/SearXNG），无需 key；
              引擎级回退在 FreeSearchClient 内部完成，不做外层熔断包装。
    - bocha : 付费博查；SEARCH_AUTO_FALLBACK=1 时叠加「博查 -> 百度反代」熔断回退。
    - baidu : baidu_serp_api 反代（HybridSearchClient：百度优先、Exa 兜底）。
    - exa   : Exa MCP。
    未识别的取值记 warning 后按 free 处理，避免路由层直接 500。
    """
    name = (search_provider or "").strip().lower()

    if name == "free":
        return FreeSourceProvider(FreeSearchClient())

    if name == "baidu":
        return BaiduSourceProvider(HybridSearchClient())

    if name == "bocha":
        bocha_client = BochaSearchClient()
        if SEARCH_AUTO_FALLBACK == "1":
            # 博查为主、百度反代兜底：额度不足/认证失效/不可用时自动切换
            bocha_client = AutoFallbackSearchClient(
                primary=bocha_client,
                fallback=BaiduSearchClient(),
                name="Bocha->Baidu",
            )
        return BochaSourceProvider(bocha_client)

    if name == "exa":
        return ExaSourceProvider(SearchClient())

    logger.warning(
        "[Pipeline] 未知 search_provider=%r（可选 free/bocha/baidu/exa），按 free 处理",
        search_provider,
    )
    return FreeSourceProvider(FreeSearchClient())


def create_pipeline(
    username: str,
    session_id: str = DEFAULT_SESSION_ID,
    max_explore_depth: int = 1,
    search_level: str = "default",
    max_topics: int = 7,
    search_provider: str = DEFAULT_SEARCH_PROVIDER,
) -> Pipeline:
    ai_config = load_config()
    ai_provider = OpenAIProvider(ai_config)
    web_fetcher = WebFetcher(respect_robots=False)
    card_store = SqliteCardStore(username=username, session_id=session_id)
    raw_store = RawPageStore(username=username, session_id=session_id)

    fetcher = WebPageTextFetcher(web_fetcher, raw_store=raw_store)
    source = build_search_source(search_provider)
    processor = ScrapeAndSummarizeProcessor(fetcher, early_stop=2)

    explorer = RelatedTopicExplorer(ai_provider, max_topics=max_topics, search_level=search_level)

    return Pipeline(
        source=source,
        processor=processor,
        builder=LLMCardBuilder(ai_provider),
        persister=CardStorePersister(card_store),
        explorer=explorer,
        max_explore_depth=max_explore_depth,
        card_store=card_store,
        embedder=Embedder.get(),
    )


def create_document_pipeline(
    username: str,
    session_id: str = DEFAULT_SESSION_ID,
    search_level: str = "default",
    max_topics: int = 7,
    search_provider: str = DEFAULT_SEARCH_PROVIDER,
) -> Pipeline:
    ai_config = load_config()
    ai_provider = OpenAIProvider(ai_config)
    card_store = SqliteCardStore(username=username, session_id=session_id)

    return Pipeline(
        source=build_search_source(search_provider),
        processor=ScrapeAndSummarizeProcessor(DocumentChunkFetcher()),
        builder=LLMCardBuilder(ai_provider),
        persister=CardStorePersister(card_store),
        explorer=RelatedTopicExplorer(ai_provider, max_topics=max_topics, search_level=search_level),
        max_explore_depth=1,
        card_store=card_store,
        embedder=Embedder.get(),
    )

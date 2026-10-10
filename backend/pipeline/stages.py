"""
Pipeline 阶段接口定义。

将搜索、处理、构建、持久化、探索 5 个阶段抽象为独立接口，
任意阶段可以替换实现而不影响其他阶段。

    Pipeline.run(query)
      ├── Source.search(query)        → [SearchResult]
      ├── Processor.process(results)   → [ProcessedContent]
      ├── Builder.build(processed, ctx) → [Card]
      ├── Persister.save(cards)       → saved cards
      └── Explorer.explore(cards)     → AsyncIterator[ExploreTask]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, AsyncIterator, Dict, List, Optional

from pydantic import BaseModel


class PipelineProgress(BaseModel):
    stage: str
    message: str
    progress: float = 0.0
    timestamp: str = ""
    current_item: Optional[str] = None
    ai_output: Optional[str] = None


@dataclass
class SearchResult:
    url: str
    title: str = ""
    snippet: str = ""

@dataclass
class ProcessedContent:
    source: SearchResult
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    # 抓取原文（fetch 层产出）：卡片生成直接基于原文，避免「摘要的摘要」二次压缩
    raw_text: str = ""

@dataclass
class ExploreTask:
    query: str
    depth: int = 1
    parent_card_id: Optional[str] = None


@dataclass
class FetchResult:
    """抓取层产出：URL → 纯文本 + 来源元信息。"""
    text: str
    source_url: str = ""
    source_title: str = ""
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class SearchEvent:
    query: str
    results: List[SearchResult]
    stage: str = "searching"

@dataclass
class ProcessEvent:
    query: str
    count: int
    stage: str = "scraping"

@dataclass
class BuildEvent:
    query: str
    cards: List[Any]

@dataclass
class SaveEvent:
    query: str
    card_ids: List[str]


class SourceProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class ContentProcessor(ABC):
    @abstractmethod
    async def process(self, results: List[SearchResult]) -> List[ProcessedContent]:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class CardBuilder(ABC):
    @abstractmethod
    async def build(
        self, content: List[ProcessedContent], context: str
    ) -> Any:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class CardPersister(ABC):
    @abstractmethod
    def save(self, cards: List[Any]) -> List[Any]:
        ...


class Explorer(ABC):
    search_level: str = "default"
    max_topics: int = 7

    @abstractmethod
    def explore(self, cards: List[Any]) -> AsyncIterator[ExploreTask]:
        raise NotImplementedError

    @abstractmethod
    async def extract_topics(
        self, card_content: str, max_count: int = 7, level: str = "default",
        exclude_title: Optional[str] = None, source_title: Optional[str] = None,
        exclude_titles: Optional[List[str]] = None,
    ) -> List[str]:
        """从卡片正文提取延申主题。

        exclude_title: 源卡标题（禁止提取与它相同/近义的主题）
        source_title: 源卡标题（领域锚定声明）
        exclude_titles: 知识库里**已有**的卡片标题清单，随 prompt 下发，
            让模型直接避开已有主题（见 pipeline/exclusions.py）。
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class TextFetcher(ABC):
    """从来源提取文本的第一层：URL → 网页正文、文档 → 章节等。"""

    @abstractmethod
    async def fetch(self, source: SearchResult) -> FetchResult | None:
        ...

    async def close(self) -> None:
        pass

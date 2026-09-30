"""
AI 提供者抽象基类模块。

定义 AI 服务的数据模型（AIConfig、TopicCluster、GeneratedCard）
和 AIProvider 抽象接口，所有 AI 实现需继承 AIProvider。
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, List

from pydantic import BaseModel


class AIConfig(BaseModel):
    """AI 服务配置模型。

    包含 API URL、Key、模型名称、并发限制和代理端口配置。
    无默认值——所有字段由 backend.config 通过 load_config() 显式提供。
    """
    api_url: str
    api_key: str
    model: str
    model_note: str = ""
    proxy_port: int = 0
    api_concurrency: int = 10


class TopicCluster(BaseModel):
    """主题聚类结果，包含主题名称及其对应的来源索引。"""
    topic_name: str
    source_indices: List[int]
    description: str = ""


class GeneratedCard(BaseModel):
    """AI 生成的卡片数据结构。"""
    title: str
    content: str
    source_indices: List[int]
    tags: List[str] = []
    confidence: float = 0.5


class AIProvider(ABC):
    """AI 提供者抽象基类。

    定义 AI 交互的统一接口，包括文本生成、摘要、主题提取、
    卡片生成和文档分析等核心方法。
    """

    @abstractmethod
    async def generate(self, prompt: str, **kwargs) -> str:
        """发送提示词到 AI 模型，返回完整响应文本。"""
        ...

    @abstractmethod
    async def generate_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]:
        """流式生成：逐 token 返回 AI 响应。"""
        ...

    @abstractmethod
    async def test_connection(self) -> bool:
        """测试与 AI API 的连接是否正常。"""
        ...

    async def summarize(self, text: str) -> str:
        """对文本进行 AI 摘要总结。"""
        raise NotImplementedError

    async def summarize_with_metadata(self, text: str) -> tuple[str, dict]:
        """合并摘要和元数据提取为单次 AI 调用，减少 API 请求次数。"""
        raise NotImplementedError

    async def extract_metadata(self, text: str) -> dict:
        """从文本中提取元数据（标签、关键词等）。"""
        raise NotImplementedError

    async def extract_related_topics(self, text: str, max_topics: int = 5, search_level: str = "default") -> list[str]:
        """从文本中提取相关主题，search_level 控制提取粒度。"""
        raise NotImplementedError

    async def translate_to_chinese(self, text: str) -> str:
        """将文本翻译为中文。"""
        raise NotImplementedError

    async def cluster_summaries(self, summaries: List[dict]) -> List[TopicCluster]:
        """将多个网页摘要按主题聚类。"""
        raise NotImplementedError

    async def generate_cards_from_sources(self, sources: List[dict], keyword: str = "") -> List[GeneratedCard]:
        """从多来源数据生成知识卡片。"""
        raise NotImplementedError

    async def generate_cards_from_sources_stream(
        self, sources: List[dict], keyword: str = ""
    ) -> AsyncIterator[str]:
        """流式生成卡片内容（每个 yield 一个 token）。"""
        raise NotImplementedError

    async def analyze_document_stream(
        self, text: str, filename: str = "", existing_cards: List[str] = None
    ) -> AsyncIterator[str]:
        """分析上传的文档，流式返回三层卡片 JSON。

        格式: { main: {...}, sections: [{title, content, key_points: [...]}] }
        调用方拼接解析完整 JSON。
        """
        raise NotImplementedError

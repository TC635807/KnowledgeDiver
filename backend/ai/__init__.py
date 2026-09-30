"""AI 提供者集成包。

提供 AI 配置、抽象基类和 OpenAI/兼容 API 的实现。
"""

from .provider import AIConfig, AIProvider, TopicCluster, GeneratedCard
from .openai_provider import OpenAIProvider

__all__ = ["AIConfig", "AIProvider", "OpenAIProvider", "TopicCluster", "GeneratedCard"]

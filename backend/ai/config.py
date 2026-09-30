"""
AI 配置加载模块。

从 backend.config 读取全局默认值，支持环境变量覆盖。
"""

from backend.config import AI_API_URL, AI_API_KEY, AI_MODEL, AI_PROXY_PORT, AI_CONCURRENCY
from .provider import AIConfig


def load_config() -> AIConfig:
    """加载 AI 配置，环境变量优先于全局默认值。"""
    return AIConfig(
        api_url=AI_API_URL,
        api_key=AI_API_KEY,
        model=AI_MODEL,
        proxy_port=AI_PROXY_PORT,
        api_concurrency=AI_CONCURRENCY,
    )

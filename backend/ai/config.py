"""
AI 配置加载模块。

优先级：.env 文件（前端「API 配置」直接改写它）
      > 进程环境变量
      > backend/config.py 默认值
"""

from backend.config import AI_PROXY_PORT, AI_CONCURRENCY
from backend.services.ai_settings import effective_settings
from .provider import AIConfig


def load_config() -> AIConfig:
    """加载 AI 配置（每次调用都重新读取 .env，改配置无需重启进程）。"""
    settings = effective_settings()
    return AIConfig(
        api_url=settings["api_url"],
        api_key=settings["api_key"],
        model=settings["model"],
        proxy_port=AI_PROXY_PORT,
        api_concurrency=AI_CONCURRENCY,
    )

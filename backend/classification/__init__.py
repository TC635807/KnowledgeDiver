"""AI 卡片分类包。

提供 CardClassifier 分类器和相关 Prompt 模板。
"""

from .classifier import CardClassifier, ClassificationSuggestion  # noqa: F401
from .prompts import CLASSIFY_PROMPT  # noqa: F401

__all__ = ["CardClassifier", "ClassificationSuggestion", "CLASSIFY_PROMPT"]

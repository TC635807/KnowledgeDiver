"""Pipeline 包。

提供可组合的搜索→处理→构建→持久化→探索流水线。
"""

from .stages import PipelineProgress
from .pipeline import Pipeline
from .factory import create_pipeline

__all__ = ["Pipeline", "PipelineProgress", "create_pipeline"]

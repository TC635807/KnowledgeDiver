"""卡片链接管理包。

提供 LinkManager 管理卡片间双向链接（links/backlinks）。
"""

from .manager import LinkManager, LinkWarning

__all__ = ["LinkManager", "LinkWarning"]

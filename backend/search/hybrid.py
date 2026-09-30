"""混合搜索客户端。

优先使用百度搜索（中文效果好），失败时自动回退到 Exa 搜索。
"""

from __future__ import annotations

import logging
from typing import List

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from backend.pipeline.stages import SearchResult
from backend.search.baidu import BaiduSearchClient
from backend.search.exa import SearchClient as ExaSearchClient

logger = logging.getLogger(__name__)


class HybridSearchClient:
    """混合搜索客户端：百度优先，Exa 兜底。"""

    def __init__(self):
        self.baidu = BaiduSearchClient()
        self.exa = ExaSearchClient()

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        """搜索：先尝试百度，失败或无结果时回退到 Exa。"""
        # 先尝试百度
        try:
            results = await self.baidu.search(query, max_results)
            if results:
                logger.info(f"[Hybrid] Baidu returned {len(results)} results for '{query}'")
                return results
            logger.info(f"[Hybrid] Baidu returned 0 results for '{query}', falling back to Exa")
        except Exception as e:
            logger.warning(f"[Hybrid] Baidu failed for '{query}': {e}, falling back to Exa")

        # 回退到 Exa
        try:
            results = await self.exa.search(query, max_results)
            logger.info(f"[Hybrid] Exa returned {len(results)} results for '{query}'")
            return results
        except Exception as e:
            logger.warning(f"[Hybrid] Exa also failed for '{query}': {e}")
            return []

    async def close(self) -> None:
        """关闭客户端。"""
        if hasattr(self.baidu, 'close'):
            await self.baidu.close()
        if hasattr(self.exa, 'close'):
            await self.exa.close()

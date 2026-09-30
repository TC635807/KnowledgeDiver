"""网页抓取器包。

提供 WebFetcher 抓取器、内容解析器、robots.txt 检查和 proxy 配置。
"""

from .fetcher import ScrapedContent, WebFetcher
from .parser import extract_main_content, extract_title, extract_metadata
from .robots import RobotsChecker
from .domain_quality import get_domain_quality
from .url_prioritizer import select_top

__all__ = [
    "ScrapedContent",
    "WebFetcher",
    "extract_main_content",
    "extract_title",
    "extract_metadata",
    "RobotsChecker",
    "get_domain_quality",
    "select_top",
]

"""
robots.txt 检查器模块。

解析网站的 robots.txt 文件，
根据 Disallow 规则判断是否允许抓取指定 URL。
支持缓存和代理配置。
"""

import asyncio
from urllib.parse import urlparse
from datetime import datetime, timedelta

import httpx
from httpx_curl_cffi import AsyncCurlTransport

from .proxy_config import get_proxy_url


class RobotsChecker:
    """robots.txt 检查器。

    缓存每个域名的 robots.txt 解析结果，
    根据 Disallow 路径前缀判断是否允许抓取。
    """

    _cache: dict[str, dict] = {}
    _MAX_CACHE_SIZE = 1000

    def __init__(self, default_timeout: int = 10):
        self.default_timeout = default_timeout

    async def _fetch_robots(self, domain: str) -> str:
        """从指定域名下载 robots.txt 文件内容。"""
        url = f"https://{domain}/robots.txt"
        try:
            proxy = get_proxy_url()
            client_kwargs = {
                "timeout": self.default_timeout,
                "transport": AsyncCurlTransport(impersonate="chrome120", default_headers=True),
            }
            if proxy:
                client_kwargs["proxy"] = proxy
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
        except Exception:
            return ""
        return ""

    def _parse_robots(self, text: str) -> tuple[list[str], float]:
        """解析 robots.txt 内容，提取 Disallow 路径和 Crawl-Delay。"""
        disallow = []
        crawl_delay = 0.0
        user_agent = None
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        idx = 0
        while idx < len(lines):
            line = lines[idx]
            if line.lower().startswith("user-agent:"):
                user_agent = line.split(":", 1)[1].strip()
                idx += 1
                while idx < len(lines) and not lines[idx].lower().startswith("user-agent:"):
                    d = lines[idx]
                    if d.lower().startswith("disallow:"):
                        path = d.split(":", 1)[1].strip()
                        if path:
                            disallow.append(path)
                    if d.lower().startswith("crawl-delay:"):
                        try:
                            crawl_delay = float(d.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                    idx += 1
                continue
            idx += 1
        return disallow, crawl_delay

    async def can_fetch(self, url: str, domain: str) -> bool:
        """检查指定 URL 是否允许抓取。

        先查缓存，未缓存则下载并解析 robots.txt，
        通过 Disallow 路径前缀匹配判断。

        Args:
            url: 目标 URL
            domain: 目标域名

        Returns:
            True=允许抓取, False=禁止抓取
        """
        if domain in self._cache:
            data = self._cache[domain]
        else:
            robots_text = await self._fetch_robots(domain)
            if not robots_text:
                return True
            disallow, crawl_delay = self._parse_robots(robots_text)
            data = {
                "disallow": disallow,
                "crawl_delay": crawl_delay,
                "fetched_at": datetime.utcnow(),
            }
            if len(self._cache) >= self._MAX_CACHE_SIZE:
                stale = sorted(self._cache, key=lambda d: self._cache[d]["fetched_at"])[:self._MAX_CACHE_SIZE // 2]
                for k in stale:
                    del self._cache[k]
            self._cache[domain] = data
        parsed = urlparse(url)
        path = parsed.path or "/"
        for d in data.get("disallow", []):
            if path.startswith(d):
                return False
        return True

    def can_fetch_sync(self, url: str) -> bool:
        """同步版本的检查方法。"""
        return asyncio.get_event_loop().run_until_complete(self.can_fetch(url, urlparse(url).netloc))

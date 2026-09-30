"""
Wikipedia API 客户端模块。

通过 Wikipedia API 执行搜索和获取页面内容，
支持多语言、parse/extracts 双 API 回退。
"""

import os
import logging
from typing import Optional, List
import asyncio
from concurrent.futures import ThreadPoolExecutor

from .proxy_config import get_proxy_dict

logger = logging.getLogger(__name__)


class WikipediaClient:
    """Wikipedia API 客户端。

    通过 Wikipedia API 搜索和获取页面内容，
    优先使用 Parse API（完整内容），回退到 Extracts API（摘要）。
    支持 zh/en 等多语言和移动端 URL 回退。
    """

    def __init__(self, timeout: int = 5):
        self.timeout = timeout
        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }

    def is_wikipedia_url(self, url: str) -> bool:
        """判断 URL 是否为 Wikipedia 页面。"""
        return "wikipedia.org" in url.lower()

    async def search_wikipedia(self, keyword: str, max_results: int = 5, lang: str = "zh") -> List[dict]:
        """搜索维基百科页面。"""
        def _search_sync():
            try:
                import requests
                params = {
                    "action": "query",
                    "format": "json",
                    "list": "search",
                    "srsearch": keyword,
                    "srlimit": max_results,
                    "srprop": "size|wordcount",
                    "utf8": 1,
                }
                
                api_url = f"https://{lang}.wikipedia.org/w/api.php"
                resp = requests.get(
                    api_url,
                    params=params,
                    headers=self.headers,
                    proxies=get_proxy_dict(),
                    timeout=self.timeout
                )
                
                if resp.status_code != 200:
                    logger.warning(f"[Wikipedia] Search API error: {resp.status_code}")
                    return []
                
                data = resp.json()
                search_results = data.get("query", {}).get("search", [])
                
                results = []
                for item in search_results:
                    title = item.get("title", "")
                    page_id = item.get("pageid", "")
                    if title and page_id:
                        # 构建维基百科URL
                        url = f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"
                        results.append({
                            "title": title,
                            "url": url,
                            "pageid": page_id,
                            "wordcount": item.get("wordcount", 0),
                            "size": item.get("size", 0)
                        })
                
                logger.info(f"[Wikipedia] Search '{keyword}' found {len(results)} results")
                return results
                
            except Exception as e:
                logger.warning(f"[Wikipedia] Search error: {e}")
                return []
        
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            return await loop.run_in_executor(executor, _search_sync)

    async def fetch_page(self, url: str) -> tuple[Optional[str], Optional[str]]:
        """通过 Wikipedia API 获取页面标题和内容。

        优先使用 Parse API（获取完整 HTML 后提取文本），
        失败后回退到 Extracts API（纯文本摘要）。
        同时尝试桌面版和移动版 API 端点。

        Args:
            url: Wikipedia 页面 URL

        Returns:
            (title, content) 元组，失败返回 (None, None)
        """
        import re
        import asyncio
        from concurrent.futures import ThreadPoolExecutor
        from urllib.parse import unquote

        # 支持多种 URL 格式：
        # - https://zh.wikipedia.org/wiki/标题
        # - https://zh.wikipedia.org/zh-cn/标题
        # - https://zh.wikipedia.org/zh-hans/标题
        match = re.search(r"wikipedia\.org/(?:wiki/|zh-\w+/)(.+?)(?:\?|#|$)", url)
        if not match:
            return None, None
        
        # URL解码标题
        title = unquote(match.group(1))
        lang = "zh"
        lang_match = re.search(r"(\w+)\.wikipedia\.org", url)
        if lang_match:
            lang = lang_match.group(1)
        
        def _fetch_sync():
            try:
                import requests
                from bs4 import BeautifulSoup
                
                # 方法1: 使用parse API获取完整HTML内容
                parse_params = {
                    "action": "parse",
                    "format": "json",
                    "page": title,
                    "prop": "text",
                    "disabletoc": True,
                    "disableeditsection": True,
                    "redirects": True,
                }
                
                for base_url in [f"https://{lang}.wikipedia.org/w/api.php", f"https://{lang}.m.wikipedia.org/w/api.php"]:
                    try:
                        resp = requests.get(
                            base_url, 
                            params=parse_params, 
                            headers=self.headers, 
                            proxies=get_proxy_dict(),
                            timeout=self.timeout
                        )
                        
                        if resp.status_code != 200:
                            continue
                        
                        data = resp.json()
                        if "parse" in data:
                            html = data["parse"].get("text", {}).get("*", "")
                            if html:
                                soup = BeautifulSoup(html, "html.parser")
                                # 移除不需要的元素
                                for tag in soup(["script", "style", "table", "figure", "img", "sup"]):
                                    tag.decompose()
                                text = soup.get_text(separator="\n", strip=True)
                                # 清理多余空行
                                lines = [line.strip() for line in text.split("\n") if line.strip()]
                                clean_text = "\n".join(lines)
                                page_title = data["parse"].get("title", title)
                                if len(clean_text) >= 100:
                                    return page_title, clean_text
                    except Exception as e:
                        logger.debug(f"[Wikipedia] Parse API {base_url} error: {e}")
                        continue
                
                # 方法2: 回退到extracts API
                extract_params = {
                    "action": "query",
                    "format": "json",
                    "titles": title,
                    "prop": "extracts",
                    "exintro": False,
                    "explaintext": True,
                    "redirects": True,
                }
                
                for base_url in [f"https://{lang}.wikipedia.org/w/api.php", f"https://{lang}.m.wikipedia.org/w/api.php"]:
                    try:
                        resp = requests.get(
                            base_url, 
                            params=extract_params, 
                            headers=self.headers, 
                            proxies=get_proxy_dict(),
                            timeout=self.timeout
                        )
                        
                        if resp.status_code != 200:
                            continue
                        
                        data = resp.json()
                        pages = data.get("query", {}).get("pages", {})
                        
                        for page_id, page in pages.items():
                            if page_id == "-1":
                                continue
                            page_title = page.get("title", "")
                            extract = page.get("extract", "")
                            if extract:
                                return page_title, extract
                    except Exception as e:
                        logger.debug(f"[Wikipedia] Extract API {base_url} error: {e}")
                        continue
                
                return None, None
            except Exception as e:
                logger.warning(f"[Wikipedia] Fetch error: {e}")
                return None, None
        
        loop = asyncio.get_event_loop()
        with ThreadPoolExecutor() as executor:
            result = await loop.run_in_executor(executor, _fetch_sync)
        
        if result and result[0]:
            logger.info(f"[Wikipedia] Fetched {len(result[1])} chars for '{result[0]}'")
        return result

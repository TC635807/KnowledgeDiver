"""
轻量 HTML 抓取 — trafilatura 和 crawl4ai 之间的二级回退。
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_MIN_CONTENT_LENGTH = 1000

_REMOVE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "form"]

_BOILERPLATE_PATTERNS = [
    r'(^|\W)(nav|menu|sidebar|footer|header|banner|ad|advertisement|ads|sponsor)($|\W)',
    r'(^|\W)(comment|reply|discuss|share|social|follow|related|recommend|suggest)($|\W)',
    r'(^|\W)(cookie|consent|popup|modal|copyright|disclaimer)($|\W)',
]


async def fetch_light(url: str, timeout: int = 8) -> Optional[dict]:
    try:
        import httpx
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    # 完整浏览器指纹: 缺 Sec-Ch-Ua/Referer/Sec-Fetch-*/HTTP2 时 CSDN 等反爬站会概率性返回 521
    from urllib.parse import urlparse
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}/"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": origin,
        "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, http2=True) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            html = resp.text
    except (asyncio.TimeoutError, Exception):
        return None

    if not html or len(html) < 200:
        return None

    return _extract_from_html(html)


def _extract_from_html(html: str) -> Optional[dict]:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    for tag_name in _REMOVE_TAGS:
        for t in soup.find_all(tag_name):
            t.decompose()

    for tag in soup.find_all(True):
        try:
            tag_id = (tag.get("id", "") + " " + " ".join(tag.get("class", []))).lower()
        except Exception:
            continue
        for pattern in _BOILERPLATE_PATTERNS:
            if re.search(pattern, tag_id):
                tag.decompose()
                break

    title = ""
    title_tag = soup.find("title")
    if title_tag:
        title = title_tag.get_text(strip=True)
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    article = soup.find("article") or soup.find("main") or soup.find("body")
    if not article:
        return None

    text = article.get_text(" ", strip=True)
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) >= 15]
    text = "\n".join(lines)
    text = re.sub(r'\n{3,}', '\n\n', text)

    if len(text) < _MIN_CONTENT_LENGTH:
        return None

    return {"title": title, "content": text, "content_length": len(text)}


async def light_fetch_from_html(html: str, url: str) -> Optional[dict]:
    return _extract_from_html(html)

"""
网页内容解析器模块。

使用 BeautifulSoup 从 HTML 中提取主体内容、标题和元数据。
通过多级选择器策略（Wikipedia 专用选择器 → main/article → 通用选择器 → 最大文本块）
逐步提取正文，自动去除导航、广告、侧边栏等噪音元素。
"""

from __future__ import annotations

import re
from bs4 import BeautifulSoup


def extract_main_content(html: str) -> str:
    """从 HTML 中提取正文内容（多级选择器策略）。

    1. 先移除导航/脚本/广告等噪音元素
    2. 尝试 Wikipedia 专用选择器
    3. 尝试 <main>/<article> 标签
    4. 尝试通用 CSS 选择器
    5. 回退到最大文本块
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in ["nav", "footer", "aside", "script", "style", "form", "header", "noscript"]:
        for t in soup.find_all(tag):
            t.decompose()

    for cls in ["sidebar", "menu", "navigation", "footer", "header", "advertisement", "ad-", "social", "share", "comment", "related", "recommend", "infobox", "toc", "navbox"]:
        for t in soup.find_all(class_=lambda x: x and cls in str(x).lower()):
            t.decompose()

    for t in soup.find_all(id=lambda x: x and any(k in str(x).lower() for k in ["sidebar", "footer", "header", "nav", "menu", "comment", "toc"])):
        t.decompose()

    wiki_selectors = [
        "#mw-content-text .mw-parser-output",
        ".mw-parser-output",
        "#mw-content-text",
        "#bodyContent",
        "#content",
    ]
    for selector in wiki_selectors:
        el = soup.select_one(selector)
        if el:
            for t in el.find_all(class_=["infobox", "toc", "navbox", "reference", "noprint"]):
                t.decompose()
            text = el.get_text(" ", strip=True)
            if len(text) > 200:
                return text

    main = soup.find("main")
    if main:
        text = main.get_text(" ", strip=True)
        if len(text) > 200:
            return text

    article = soup.find("article")
    if article:
        text = article.get_text(" ", strip=True)
        if len(text) > 200:
            return text

    for selector in ["#content", "#main", "#article", ".content", ".main", ".article", ".post", ".entry"]:
        elements = soup.select(selector)
        for el in elements:
            text = el.get_text(" ", strip=True)
            if len(text) > 200:
                return text

    candidates = soup.find_all(["div", "section"])
    max_text = ""
    for c in candidates:
        t = c.get_text(" ", strip=True)
        if len(t) > len(max_text):
            max_text = t

    text = re.sub(r'\s+', ' ', max_text).strip()
    return text


def extract_title(soup: BeautifulSoup) -> str:
    """从 BeautifulSoup 对象中提取页面标题（title → h1 → h2 → h3）。"""
    if soup is None:
        return ""
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(strip=True)
    for tag in ["h2", "h3"]:
        node = soup.find(tag)
        if node and node.get_text(strip=True):
            return node.get_text(strip=True)
    return ""


def extract_metadata(soup: BeautifulSoup) -> dict:
    """从 HTML <meta> 标签中提取元数据（property/name → content）。"""
    meta = {}
    if soup is None:
        return meta
    for m in soup.find_all("meta"):
        key = None
        if m.get("property"):
            key = m["property"]
        elif m.get("name"):
            key = m["name"]
        else:
            continue
        value = m.get("content", "")
        if key and value:
            meta[key] = value
    return meta

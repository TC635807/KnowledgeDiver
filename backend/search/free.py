"""免费多引擎搜索客户端。

取链机制对齐 DeepSeek Harness 的 dsh-free-search 插件：不依赖任何付费搜索 API，
直接抓取搜索引擎结果页 / 调用无 key 的公开搜索端点，按优先级依次尝试，
任一引擎返回结果即止。

引擎一览（默认优先级 bing -> anysearch -> exa-mcp -> ddg -> searxng）：

==========  ==============================================================
bing        cn.bing.com SERP HTML 解析（默认首选，中文优化，国内网络可达）
anysearch   api.anysearch.com 匿名免费额度，结构化 JSON，无 key
exa-mcp     mcp.exa.ai 公开 MCP（无 key），复用 backend.search.exa 解析器
ddg         html.duckduckgo.com/html（海外网络更稳，国内直连不通）
ddg-lite    lite.duckduckgo.com/lite
searxng     公共实例元搜索，多实例自动切换（国内实例多不可达）
==========  ==============================================================

设计原则：
  - 引擎函数只负责「查询 + 响应 -> List[Hit]」，HTTP 客户端 / 限流由客户端统一提供；
  - 任一引擎抛异常或返回空，仅记录原因并继续下一个引擎，绝不中断整条链；
  - 全部失败返回 []，由上层 SourceProvider / 回退包装器决定后续动作；
  - 候选池不受 max_results 截断（与 Bocha 固定 count=60 同思路：候选池必须远大于
    目标来源数，截断职责在 scraper.url_prioritizer.select_top）。
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional

import httpx

from backend.config import (
    ANYSEARCH_API_KEY,
    ANYSEARCH_API_URL,
    BING_MARKET,
    FREE_SEARCH_BING_PAGES,
    FREE_SEARCH_CONCURRENCY,
    FREE_SEARCH_ENGINES,
    FREE_SEARCH_POOL_SIZE,
    FREE_SEARCH_RATE_LIMIT,
    FREE_SEARCH_TIMEOUT,
    FREE_SEARCH_USER_AGENT,
    SEARXNG_INSTANCES,
)
from backend.search.base import BaseSearchClient

logger = logging.getLogger(__name__)


@dataclass
class Hit:
    """引擎级搜索结果条目（尚未转换为 pipeline.SearchResult）。"""

    url: str
    title: str = ""
    snippet: str = ""


@dataclass
class EngineAttempt:
    """单次引擎尝试的结果，用于观测与测试断言。"""

    engine: str
    ok: bool
    count: int = 0
    error: str = ""


# ═══════════════════════════════════════════════════════════════════
# 通用清洗 / 解析工具（与 dsh-free-search 同口径）
# ═══════════════════════════════════════════════════════════════════

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
# MD 链接/图片噪音（AnySearch 等引擎的 snippet 常带 markdown）
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
# 登录/订阅/付费墙噪音短语
_SNIPPET_NOISE_RE = re.compile(
    r"\b(sign up|sign in|log in|login|subscribe( to| for)?|member[- ]?only|"
    r"become a member|read more|continue reading|story continues|get started|"
    r"install (the )?app|remember me|unlock this|free to read)\b",
    re.IGNORECASE,
)


def decode_entities(text: str) -> str:
    """反转义 HTML 实体（含数字实体）。"""
    if not text:
        return ""
    text = (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&#x27;", "'")
        .replace("&nbsp;", " ")
    )
    return re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)


def strip_tags(raw: str) -> str:
    """去标签 + 反转义 + 折叠空白。"""
    return _WS_RE.sub(" ", decode_entities(_TAG_RE.sub(" ", raw or ""))).strip()


def clean_snippet(raw: str, max_chars: int = 300) -> str:
    """统一 snippet 清洗：去 markdown 噪音、去订阅话术、截断。"""
    if not raw:
        return ""
    text = _MD_LINK_RE.sub(lambda m: m.group(1), str(raw))
    text = _SNIPPET_NOISE_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars]


def extract_ddg_url(rel: str) -> Optional[str]:
    """DuckDuckGo 结果链接可能是 /l/?uddg=<encoded> 跳转，解出真实 URL。"""
    if not rel:
        return None
    m = re.search(r"uddg=([^&]+)", rel)
    if m:
        from urllib.parse import unquote

        return unquote(m.group(1))
    if rel.startswith("//"):
        return f"https:{rel}"
    return rel


def unique_hits(hits: List[Hit], limit: Optional[int] = None) -> List[Hit]:
    """按 URL 去重并截断到 limit（limit 为 None 时不截断）。"""
    seen = set()
    out: List[Hit] = []
    for h in hits:
        if not h.url or h.url in seen:
            continue
        seen.add(h.url)
        out.append(h)
        if limit is not None and len(out) >= limit:
            break
    return out


def query_tokens(query: str) -> List[str]:
    """查询 token 化：CJK 取 1-2 字组合，拉丁/数字取长度 >= 2 的词。"""
    tokens: List[str] = []
    for run in re.findall(r"[\u4e00-\u9fff]+", str(query)):
        if len(run) <= 2:
            tokens.append(run)
        tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    tokens.extend(w for w in re.split(r"[^a-z0-9]+", str(query).lower()) if len(w) >= 2)
    seen = set()
    out: List[str] = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def looks_relevant(query: str, hits: List[Hit]) -> bool:
    """Bing 在「查询无结果」时会返回一张完全无关的缓存 SERP。

    这里用「查询 token 与结果文本是否重叠」识别该情况，判为 0 结果交给下一个引擎。
    纯符号查询无法判定，不拦截。
    """
    tokens = query_tokens(query)
    if not tokens:
        return True
    for h in hits:
        hay = f"{h.title} {h.snippet} {h.url}".lower()
        if any(t.lower() in hay for t in tokens):
            return True
    return False


# ═══════════════════════════════════════════════════════════════════
# 各引擎的响应解析（纯函数，便于单测）
# ═══════════════════════════════════════════════════════════════════

_BING_BLOCK_RE = re.compile(r'<li class="b_algo"[\s\S]*?</li>')
_BING_HREF_RE = re.compile(r'<a[^>]*href="(https?://[^"]+)"')
_BING_TITLE_RE = re.compile(r"<h2[^>]*>[\s\S]*?<a[^>]*>(.*?)</a>[\s\S]*?</h2>")
_BING_SNIPPET_RE = re.compile(r"<p[^>]*>([\s\S]*?)</p>")


def parse_bing_html(html: str, limit: Optional[int] = None) -> List[Hit]:
    """解析 Bing SERP（<li class="b_algo"> 结果块）。"""
    hits: List[Hit] = []
    for block in _BING_BLOCK_RE.findall(html or ""):
        href = _BING_HREF_RE.search(block)
        if not href:
            continue
        title = _BING_TITLE_RE.search(block)
        snippet = _BING_SNIPPET_RE.search(block)
        hits.append(
            Hit(
                url=href.group(1),
                title=strip_tags(title.group(1)) if title else "",
                snippet=clean_snippet(strip_tags(snippet.group(1))) if snippet else "",
            )
        )
    return unique_hits(hits, limit)


_DDG_BLOCK_RE = re.compile(r'<div class="result results_links[\s\S]*?</div>\s*</div>\s*</div>')
_DDG_LINK_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"')
_DDG_TITLE_RE = re.compile(r'<a[^>]*class="result__a"[^>]*>(.*?)</a>')
_DDG_SNIPPET_RE = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>')


def parse_ddg_html(html: str, limit: Optional[int] = None) -> List[Hit]:
    """解析 DuckDuckGo HTML 版 SERP。"""
    hits: List[Hit] = []
    for block in _DDG_BLOCK_RE.findall(html or ""):
        href = _DDG_LINK_RE.search(block)
        url = extract_ddg_url(href.group(1)) if href else None
        if not url:
            continue
        title = _DDG_TITLE_RE.search(block)
        snippet = _DDG_SNIPPET_RE.search(block)
        hits.append(
            Hit(
                url=url,
                title=strip_tags(title.group(1)) if title else "",
                snippet=clean_snippet(strip_tags(snippet.group(1))) if snippet else "",
            )
        )
    return unique_hits(hits, limit)


_DDG_LITE_LINK_RE = re.compile(r"<a[^>]*class=['\"]result-link['\"][^>]*>[\s\S]*?</a>")
_DDG_LITE_SNIPPET_RE = re.compile(r"class=['\"]result-snippet['\"][^>]*>([\s\S]*?)</td>")


def parse_ddg_lite_html(html: str, limit: Optional[int] = None) -> List[Hit]:
    """解析 DuckDuckGo Lite SERP。"""
    links = _DDG_LITE_LINK_RE.findall(html or "")
    snippets = _DDG_LITE_SNIPPET_RE.findall(html or "")
    hits: List[Hit] = []
    for i, tag in enumerate(links):
        m = re.search(r'href="([^"]*)"', tag)
        url = extract_ddg_url(m.group(1)) if m else None
        if not url:
            continue
        title_m = re.search(r"class=['\"]result-link['\"][^>]*>(.*?)</a>", tag)
        hits.append(
            Hit(
                url=url,
                title=strip_tags(title_m.group(1)) if title_m else "",
                snippet=clean_snippet(strip_tags(snippets[i])) if i < len(snippets) else "",
            )
        )
    return unique_hits(hits, limit)


def parse_anysearch_payload(data: dict, limit: Optional[int] = None) -> List[Hit]:
    """解析 AnySearch 结构化响应（code=0，data.results[]）。"""
    if not isinstance(data, dict) or data.get("code") != 0:
        return []
    results = (data.get("data") or {}).get("results") or []
    hits: List[Hit] = []
    for item in results:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        hits.append(
            Hit(
                url=str(item["url"]),
                title=str(item.get("title", "")),
                snippet=clean_snippet(str(item.get("snippet", ""))),
            )
        )
    return unique_hits(hits, limit)


def parse_searxng_payload(data: dict, limit: Optional[int] = None) -> List[Hit]:
    """解析 SearXNG JSON 响应（results[]）。"""
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return []
    hits: List[Hit] = []
    for item in data["results"]:
        if not isinstance(item, dict) or not item.get("url"):
            continue
        hits.append(
            Hit(
                url=str(item["url"]),
                title=str(item.get("title", "")),
                snippet=clean_snippet(str(item.get("content", ""))),
            )
        )
    return unique_hits(hits, limit)


# ═══════════════════════════════════════════════════════════════════
# 客户端
# ═══════════════════════════════════════════════════════════════════

EngineFn = Callable[["FreeSearchClient", str, int], Awaitable[List[Hit]]]


class FreeSearchClient(BaseSearchClient):
    """免费多引擎搜索客户端：按引擎优先级依次尝试，任一成功即返回。

    用法::

        client = FreeSearchClient()                 # 走 config 里的默认引擎顺序
        results = await client.search("知识管理")     # List[pipeline.SearchResult]
        print(client.last_engine)                    # 实际生效的引擎

    任何引擎都不需要 API key；ANYSEARCH_API_KEY 为可选，仅用于提高匿名额度。
    """

    def __init__(
        self,
        engines: Optional[List[str]] = None,
        *,
        http_client: Optional[httpx.AsyncClient] = None,
        timeout: int = FREE_SEARCH_TIMEOUT,
        pool_size: int = FREE_SEARCH_POOL_SIZE,
        bing_market: str = BING_MARKET,
        bing_pages: int = FREE_SEARCH_BING_PAGES,
        searxng_instances: Optional[List[str]] = None,
        anysearch_api_key: str = ANYSEARCH_API_KEY,
        user_agent: str = FREE_SEARCH_USER_AGENT,
        max_concurrent: int = FREE_SEARCH_CONCURRENCY,
        rate_per_sec: float = FREE_SEARCH_RATE_LIMIT,
    ):
        # max_retries=0：重试语义由「引擎回退链」承担。单引擎失败不再原地重试，
        # 避免一个被墙的引擎把整次搜索拖满超时。
        super().__init__(
            max_concurrent=max_concurrent,
            rate_per_sec=rate_per_sec,
            max_retries=0,
            base_delay=1.0,
        )
        self._engines = [e for e in (engines if engines is not None else FREE_SEARCH_ENGINES) if e]
        self._http_client = http_client
        self._owns_client = False
        self._timeout = timeout
        self._pool_size = max(1, pool_size)
        self._bing_market = bing_market
        self._bing_pages = max(1, bing_pages)
        self._searxng_instances = searxng_instances or SEARXNG_INSTANCES
        self._anysearch_api_key = anysearch_api_key or ""
        self._user_agent = user_agent

        self.last_engine: Optional[str] = None
        self.attempts: List[EngineAttempt] = []
        self.engine_hits: Dict[str, int] = {}

    # ---------- 对外可观测属性 ----------

    @property
    def engines(self) -> List[str]:
        return list(self._engines)

    @property
    def active_provider(self) -> str:
        """最近一次成功搜索实际生效的引擎（供日志/监控）。"""
        return self.last_engine or "none"

    def stats(self) -> dict:
        return {
            "engines": list(self._engines),
            "active_provider": self.active_provider,
            "hits_by_engine": dict(self.engine_hits),
            "last_attempts": [
                {"engine": a.engine, "ok": a.ok, "count": a.count, "error": a.error}
                for a in self.attempts
            ],
        }

    # ---------- HTTP ----------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is not None:
            return self._http_client
        transport = None
        try:
            # 与 scraper/robots.py 同款：curl_cffi 浏览器指纹，抗反爬、少被 SERP 判为爬虫
            from httpx_curl_cffi import AsyncCurlTransport

            transport = AsyncCurlTransport(impersonate="chrome120", default_headers=True)
        except Exception as e:  # pragma: no cover - 依赖缺失时退回原生 httpx
            logger.debug("[FreeSearch] curl_cffi transport 不可用，退回 httpx: %s", e)
        kwargs: dict = {
            "timeout": self._timeout,
            "follow_redirects": True,
            "trust_env": False,
            "headers": {
                "user-agent": self._user_agent,
                "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
        }
        if transport is not None:
            kwargs["transport"] = transport
        # 延迟导入：backend.scraper.__init__ -> backend.pipeline -> factory -> backend.search
        # 会构成循环导入（bocha.py 采用同样的延迟导入写法）
        from backend.scraper.proxy_config import get_proxy_url

        proxy = get_proxy_url()
        if proxy:
            kwargs["proxy"] = proxy
        self._http_client = httpx.AsyncClient(**kwargs)
        self._owns_client = True
        return self._http_client

    def _pool(self, max_results: int) -> int:
        """候选池大小：不小于 max_results，也不小于配置的池下限。"""
        return max(int(max_results or 0), self._pool_size)

    async def _get_html(self, url: str, accept_lang: Optional[str] = None) -> str:
        client = await self._get_client()
        headers = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
        if accept_lang:
            headers["accept-language"] = accept_lang
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}")
        text = resp.text
        if re.search(r"anomaly|captcha|unusual traffic|robot check", text[:4000], re.IGNORECASE):
            raise RuntimeError("anti-bot challenge (rate limited)")
        return text

    # ---------- 引擎回退链 ----------

    async def _do_search(self, query: str, max_results: int) -> list:
        from backend.pipeline.stages import SearchResult  # 延迟导入避免循环依赖

        self.attempts = []
        self.last_engine = None
        pool = self._pool(max_results)

        for name in self._engines:
            fn = ENGINE_REGISTRY.get(name)
            if fn is None:
                self.attempts.append(EngineAttempt(name, False, error="unknown engine"))
                continue
            try:
                hits = await fn(self, query, pool)
            except Exception as e:
                reason = str(e) or e.__class__.__name__
                self.attempts.append(EngineAttempt(name, False, error=reason))
                logger.warning("[FreeSearch] 引擎 %s 失败: %s", name, reason)
                continue

            hits = [h for h in hits if h.url]
            if not hits:
                self.attempts.append(EngineAttempt(name, False, error="0 results"))
                logger.info("[FreeSearch] 引擎 %s 返回 0 条，尝试下一个", name)
                continue

            self.attempts.append(EngineAttempt(name, True, count=len(hits)))
            self.engine_hits[name] = self.engine_hits.get(name, 0) + 1
            self.last_engine = name
            logger.info("[FreeSearch] 引擎 %s 命中 %d 条 (query=%r)", name, len(hits), query)
            return [SearchResult(url=h.url, title=h.title, snippet=h.snippet) for h in hits]

        logger.warning(
            "[FreeSearch] 全部引擎失败 (query=%r): %s",
            query,
            "; ".join(f"{a.engine}:{a.error}" for a in self.attempts) or "no engines configured",
        )
        return []

    async def close(self) -> None:
        if self._owns_client and self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
            self._owns_client = False


# ═══════════════════════════════════════════════════════════════════
# 引擎实现
# ═══════════════════════════════════════════════════════════════════

BING_URL = "https://cn.bing.com/search"
DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
ANYSEARCH_MAX_RESULTS = 50


async def _engine_bing(client: FreeSearchClient, query: str, pool: int) -> List[Hit]:
    """Bing SERP 抓取（默认首选引擎）。"""
    from urllib.parse import urlencode

    pages = min(client._bing_pages, max(1, math.ceil(pool / 10)))
    hits: List[Hit] = []
    for page in range(pages):
        params = {"q": query, "mkt": client._bing_market, "adlt": "off"}
        if page > 0:
            params["first"] = str(page * 10 + 1)
        html = await client._get_html(
            f"{BING_URL}?{urlencode(params)}",
            accept_lang="zh-CN,zh;q=0.9,en;q=0.8",
        )
        page_hits = parse_bing_html(html)
        if not page_hits:
            break
        hits.extend(page_hits)
        if len(unique_hits(hits)) >= pool:
            break
    hits = unique_hits(hits)
    # Bing 无结果时会返回无关缓存页：判 0 条，交给下一个引擎
    if hits and not looks_relevant(query, hits):
        logger.info("[FreeSearch][bing] 结果与查询无重叠，判为无效 SERP")
        return []
    return unique_hits(hits, pool)


async def _engine_anysearch(client: FreeSearchClient, query: str, pool: int) -> List[Hit]:
    """AnySearch 匿名免费额度（可选 key 提额）。"""
    c = await client._get_client()
    key = client._anysearch_api_key.strip()
    payload = {"query": query, "max_results": min(pool, ANYSEARCH_MAX_RESULTS)}

    async def _post(with_key: bool):
        headers = {"content-type": "application/json"}
        if with_key:
            headers["authorization"] = f"Bearer {key}"
        return await c.post(ANYSEARCH_API_URL, json=payload, headers=headers)

    resp = await _post(bool(key))
    if resp.status_code in (401, 403) and key:
        # key 失效：本次忽略该 key，改回匿名免费额度（不自动清空用户配置）
        logger.warning("[FreeSearch][anysearch] key 无效，改回匿名免费额度")
        resp = await _post(False)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return parse_anysearch_payload(resp.json(), pool)


async def _engine_exa_mcp(client: FreeSearchClient, query: str, pool: int) -> List[Hit]:
    """Exa 公开 MCP（无 key）：复用 backend.search.exa 的 MCP 调用与解析。"""
    from backend.search.exa import SearchClient as ExaSearchClient

    exa = ExaSearchClient(http_client=await client._get_client())
    raw = await exa.search(query, min(pool, 25))
    return [Hit(url=r.url, title=r.title or "", snippet=clean_snippet(r.snippet or "")) for r in raw]


async def _engine_ddg(client: FreeSearchClient, query: str, pool: int) -> List[Hit]:
    """DuckDuckGo HTML 版（国内直连通常不可达，海外网络更稳）。"""
    from urllib.parse import urlencode

    params = {"q": query, "adlt": "-1"}
    html = await client._get_html(f"{DDG_HTML_URL}?{urlencode(params)}")
    return parse_ddg_html(html, pool)


async def _engine_ddg_lite(client: FreeSearchClient, query: str, pool: int) -> List[Hit]:
    """DuckDuckGo Lite。"""
    from urllib.parse import urlencode

    params = {"q": query, "adlt": "-1"}
    html = await client._get_html(f"{DDG_LITE_URL}?{urlencode(params)}")
    return parse_ddg_lite_html(html, pool)


async def _engine_searxng(client: FreeSearchClient, query: str, pool: int) -> List[Hit]:
    """SearXNG 公共实例元搜索：逐实例尝试，任一有结果即返回。"""
    from urllib.parse import urlencode

    errors: List[str] = []
    for base in client._searxng_instances or []:
        try:
            url = f"{base.rstrip('/')}/search?{urlencode({'q': query, 'format': 'json'})}"
            c = await client._get_client()
            resp = await c.get(url, headers={"accept": "application/json"})
            if resp.status_code != 200:
                errors.append(f"{base}: HTTP {resp.status_code}")
                continue
            hits = parse_searxng_payload(resp.json(), pool)
            if hits:
                return hits
            errors.append(f"{base}: 0 results")
        except Exception as e:
            errors.append(f"{base}: {e}")
    raise RuntimeError("all SearXNG instances failed: " + ", ".join(errors)[:300])


ENGINE_REGISTRY: Dict[str, EngineFn] = {
    "bing": _engine_bing,
    "anysearch": _engine_anysearch,
    "exa-mcp": _engine_exa_mcp,
    "ddg": _engine_ddg,
    "ddg-lite": _engine_ddg_lite,
    "searxng": _engine_searxng,
}

__all__ = [
    "FreeSearchClient",
    "Hit",
    "ENGINE_REGISTRY",
    "parse_bing_html",
    "parse_ddg_html",
    "parse_ddg_lite_html",
    "parse_anysearch_payload",
    "parse_searxng_payload",
    "looks_relevant",
    "query_tokens",
    "clean_snippet",
    "strip_tags",
]

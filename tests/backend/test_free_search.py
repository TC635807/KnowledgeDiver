"""免费多引擎搜索（provider="free"）测试。

锁定：
- 默认搜索提供商为 free，引擎优先级 bing -> anysearch -> exa-mcp -> ddg -> searxng；
- 各引擎响应解析（Bing SERP / AnySearch JSON / SearXNG JSON / DDG HTML + uddg 解码）；
- Bing「无关缓存页」守卫（looks_relevant）；
- 引擎回退链：前一引擎抛错或 0 结果时继续，任一成功即止；全失败返回 [] 不抛异常；
- 全链路不需要任何 API key；
- FreeSourceProvider 鸭子类型兼容 pipeline.SearchResult / dict / 任意带 url 的对象，
  并复用域名黑名单过滤。
"""

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import pytest  # noqa: E402

from backend import config as config_mod  # noqa: E402
from backend.pipeline import defaults as defaults_mod  # noqa: E402
from backend.pipeline import factory  # noqa: E402
from backend.pipeline.defaults import FreeSourceProvider  # noqa: E402
from backend.pipeline.stages import SearchResult  # noqa: E402
from backend.search import free as free_mod  # noqa: E402
from backend.search.free import (  # noqa: E402
    FreeSearchClient,
    Hit,
    clean_snippet,
    looks_relevant,
    parse_anysearch_payload,
    parse_bing_html,
    parse_ddg_html,
    parse_searxng_payload,
    query_tokens,
)


def _run(coro):
    return asyncio.run(coro)


# ── 夹具：真实 SERP 结构的精简版 ──────────────────────────────────────

BING_HTML = """
<html><body><ol id="b_results">
<li class="b_algo"><h2><a href="https://example.com/a">知识管理入门</a></h2>
<div><p>知识管理是组织内识别、组织、存储与传播信息的流程。</p></div></li>
<li class="b_algo"><h2><a href="https://example.com/a">重复链接</a></h2>
<div><p>重复 URL 应被去重。</p></div></li>
<li class="b_algo"><h2><a href="https://example.com/b">KM 工具对比</a></h2>
<div><p>对比 Notion / Obsidian 等知识管理工具。</p></div></li>
</ol></body></html>
"""

BING_IRRELEVANT_HTML = """
<li class="b_algo"><h2><a href="https://video.example/watch?v=1">Funny cats compilation</a></h2>
<div><p>Subscribe for more videos.</p></div></li>
"""

DDG_HTML = """
<div class="result results_links results_links_deep web-result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fkm&amp;rut=abc">Knowledge management</a>
  <a class="result__snippet">KM is the process of capturing and sharing knowledge.</a>
</div></div></div>
"""

ANYSEARCH_JSON = {
    "code": 0,
    "data": {
        "results": [
            {
                "title": "什么是知识管理？- IBM",
                "url": "https://www.ibm.com/cn-zh/think/topics/knowledge-management",
                "snippet": "# 什么是知识管理？ - [什么是知识管理？](https://x#a) - " + "长" * 400,
            },
            {"title": "无 URL 条目"},
        ]
    },
}

SEARXNG_JSON = {
    "results": [
        {"url": "https://example.org/x", "title": "T", "content": "C"},
        {"title": "no url"},
    ]
}


# ── 配置默认值 ────────────────────────────────────────────────────────

def test_default_provider_is_free():
    if not os.environ.get("DEFAULT_SEARCH_PROVIDER"):
        assert config_mod.DEFAULT_SEARCH_PROVIDER == "free"


def test_default_engine_priority():
    assert config_mod.FREE_SEARCH_ENGINES[:3] == ["exa-mcp", "anysearch", "bing"]
    assert all(e in free_mod.ENGINE_REGISTRY for e in config_mod.FREE_SEARCH_ENGINES)


def test_engine_registry_covers_documented_engines():
    assert set(free_mod.ENGINE_REGISTRY) == {
        "bing",
        "anysearch",
        "exa-mcp",
        "ddg",
        "ddg-lite",
        "searxng",
    }


# ── 解析器 ────────────────────────────────────────────────────────────

def test_parse_bing_html_dedup_and_fields():
    hits = parse_bing_html(BING_HTML)
    assert [h.url for h in hits] == ["https://example.com/a", "https://example.com/b"]
    assert hits[0].title == "知识管理入门"
    assert "知识管理" in hits[0].snippet
    assert parse_bing_html(BING_HTML, limit=1) == hits[:1]


def test_parse_ddg_html_decodes_uddg_redirect():
    hits = parse_ddg_html(DDG_HTML)
    assert len(hits) == 1
    assert hits[0].url == "https://example.com/km"  # &amp; 已反解，uddg 已解码
    assert hits[0].title == "Knowledge management"
    assert "capturing" in hits[0].snippet


def test_parse_anysearch_payload_cleans_and_caps():
    hits = parse_anysearch_payload(ANYSEARCH_JSON)
    assert len(hits) == 1  # 无 url 的条目被丢弃
    assert hits[0].url.startswith("https://www.ibm.com")
    assert "[什么是知识管理？]" not in hits[0].snippet  # markdown 链接已清洗
    assert len(hits[0].snippet) <= 300
    assert parse_anysearch_payload({"code": 500, "message": "err"}) == []
    assert parse_anysearch_payload("not a dict") == []


def test_parse_searxng_payload():
    hits = parse_searxng_payload(SEARXNG_JSON)
    assert [(h.url, h.title, h.snippet) for h in hits] == [
        ("https://example.org/x", "T", "C")
    ]
    assert parse_searxng_payload({"results": "oops"}) == []


def test_clean_snippet_strips_noise_and_markdown():
    out = clean_snippet("Read more [标题](https://x)  Sign up 正文内容")
    assert "Read more" not in out and "Sign up" not in out
    assert "https://x" not in out and "标题" in out


# ── Bing 无关 SERP 守卫 ───────────────────────────────────────────────

def test_query_tokens_cjk_bigrams_and_latin():
    tokens = query_tokens("知识管理 RAG 检索")
    assert "知识" in tokens and "识管" in tokens and "管理" in tokens
    assert "rag" in tokens and "检索" in tokens


def test_looks_relevant_flags_unrelated_cached_serp():
    unrelated = parse_bing_html(BING_IRRELEVANT_HTML)
    assert looks_relevant("量子计算 纠错", unrelated) is False
    assert looks_relevant("知识管理", parse_bing_html(BING_HTML)) is True
    # 纯符号查询无法判定 -> 不拦截
    assert looks_relevant("!!!", unrelated) is True


# ── 引擎回退链 ────────────────────────────────────────────────────────

def test_fallback_chain_stops_at_first_success(monkeypatch):
    calls = []

    async def boom(client, query, pool):
        calls.append("boom")
        raise RuntimeError("blocked")

    async def empty(client, query, pool):
        calls.append("empty")
        return []

    async def good(client, query, pool):
        calls.append("good")
        # 结果必须与查询真正相关，否则链级守门会把它也判为跑题
        return [Hit("https://ok.example/x", "知识管理入门", "组织内知识的识别与传播。")]

    monkeypatch.setitem(free_mod.ENGINE_REGISTRY, "boom", boom)
    monkeypatch.setitem(free_mod.ENGINE_REGISTRY, "empty", empty)
    monkeypatch.setitem(free_mod.ENGINE_REGISTRY, "good", good)

    client = FreeSearchClient(engines=["boom", "empty", "good"])
    results = _run(client.search("知识管理", 3))

    assert calls == ["boom", "empty", "good"]
    assert [r.url for r in results] == ["https://ok.example/x"]
    assert isinstance(results[0], SearchResult)  # 交给 pipeline 的类型
    assert client.active_provider == "good"
    assert [(a.engine, a.ok) for a in client.attempts] == [
        ("boom", False),
        ("empty", False),
        ("good", True),
    ]
    assert client.attempts[0].error == "blocked"

    stats = client.stats()
    assert stats["active_provider"] == "good"
    assert stats["hits_by_engine"] == {"good": 1}


def test_all_engines_fail_returns_empty_without_raising(monkeypatch):
    async def boom(client, query, pool):
        raise RuntimeError("nope")

    monkeypatch.setitem(free_mod.ENGINE_REGISTRY, "boom", boom)
    client = FreeSearchClient(engines=["boom", "boom"])
    assert _run(client.search("q", 3)) == []
    assert client.active_provider == "none"
    assert [a.count for a in client.attempts] == [0, 0]


def test_unknown_engine_is_skipped(monkeypatch):
    async def good(client, query, pool):
        return [Hit("https://ok.example/y")]

    monkeypatch.setitem(free_mod.ENGINE_REGISTRY, "good", good)
    client = FreeSearchClient(engines=["does-not-exist", "good"])
    results = _run(client.search("q", 3))
    assert [r.url for r in results] == ["https://ok.example/y"]
    assert client.attempts[0].error == "unknown engine"


# ── 与 httpx 的接口契约（MockTransport，不联网） ────────────────────────

def test_bing_engine_builds_request_and_parses():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["ua"] = request.headers.get("user-agent", "")
        return httpx.Response(200, text=BING_HTML, headers={"content-type": "text/html"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FreeSearchClient(engines=["bing"], http_client=http, pool_size=5)
    results = _run(client.search("知识管理", 5))

    assert "cn.bing.com/search" in seen["url"]
    assert "mkt=zh-CN" in seen["url"] and "adlt=off" in seen["url"]
    assert seen["ua"]
    assert len(results) == 2 and client.active_provider == "bing"
    _run(http.aclose())


def test_bing_irrelevant_serp_falls_through(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=BING_IRRELEVANT_HTML)

    async def good(client, query, pool):
        return [Hit("https://ok.example/z", "量子计算纠错入门", "量子纠错是量子计算的关键方向。")]

    monkeypatch.setitem(free_mod.ENGINE_REGISTRY, "good", good)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FreeSearchClient(engines=["bing", "good"], http_client=http, pool_size=5)
    results = _run(client.search("量子计算 纠错", 5))

    assert [r.url for r in results] == ["https://ok.example/z"]
    # Bing 返回了 1 条但与查询无关 -> 判为无效 SERP 并继续回退（不是「0 results」）
    assert client.attempts[0].engine == "bing"
    assert client.attempts[0].error == "irrelevant SERP"
    assert client.attempts[0].count == 1
    _run(http.aclose())


def test_anysearch_key_failure_falls_back_to_anonymous():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("authorization"))
        if request.headers.get("authorization"):
            return httpx.Response(401, json={"code": 401, "message": "bad key"})
        return httpx.Response(200, json=ANYSEARCH_JSON)

    from backend.search.free import _engine_anysearch

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = FreeSearchClient(
        engines=["anysearch"], http_client=http, anysearch_api_key="dead-key"
    )
    hits = _run(_engine_anysearch(client, "知识管理", 5))

    assert calls == ["Bearer dead-key", None]  # 401 后改回匿名，而不是直接失败
    assert len(hits) == 1
    _run(http.aclose())


# ── SourceProvider 与工厂接线 ─────────────────────────────────────────

def test_free_source_provider_accepts_mixed_return_types(monkeypatch):
    class _FakeDQ:
        def is_blocked(self, url):
            return "blocked.example" in url

    monkeypatch.setattr(defaults_mod, "_dq", lambda: _FakeDQ())

    class _FakeClient:
        async def search(self, query, max_results):
            return [
                SearchResult(url="https://a.example/1", title="A", snippet="s1"),
                {"url": "https://b.example/2", "title": "B", "snippet": "s2"},
                SimpleNamespace(url="https://c.example/3", title="C", snippet="s3"),
                {"url": "https://blocked.example/4", "title": "X"},
                {"url": "", "title": "empty"},
            ]

        async def close(self):
            pass

    provider = FreeSourceProvider(_FakeClient())
    results = _run(provider.search("q", 5))
    assert [r.url for r in results] == [
        "https://a.example/1",
        "https://b.example/2",
        "https://c.example/3",
    ]
    _run(provider.close())


def test_factory_wires_free_and_unknown_provider():
    source = factory.build_search_source("free")
    assert isinstance(source, FreeSourceProvider)
    assert source.search_client.engines[0] == "exa-mcp"

    # 未知取值不抛异常，按 free 处理（避免路由层 500）
    fallback = factory.build_search_source("no-such-provider")
    assert isinstance(fallback, FreeSourceProvider)


def test_free_client_requires_no_api_key():
    client = FreeSearchClient()
    assert client.engines[:3] == ["exa-mcp", "anysearch", "bing"]
    assert client._anysearch_api_key == config_mod.ANYSEARCH_API_KEY

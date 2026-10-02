"""抓取器资源泄漏回归（实测：浏览器无响应 + 进程 OOM 被杀的根因）。

对应两处修复：
1. _reset_crawler 必须**真正关闭**旧浏览器，否则崩溃后反复重启 Chromium，
   进程成倍累积把内存打满；
2. 页面超时后必须**等 arun 收敛再释放 _crawler_sem**，否则后台存活页面
   不受并发上限约束，一批超时 URL 就能把 Chromium 内存拉爆。
"""

import asyncio
import os
import sys
import types
from pathlib import Path

os.environ.setdefault("JWT_SECRET", "test-secret")

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

import backend.scraper.fetcher as F  # noqa: E402


class FakeCrawler:
    """记录 __aenter__/__aexit__ 与 arun 的并发情况。"""

    def __init__(self, sleep: float = 0.0):
        self.sleep = sleep
        self.entered = 0
        self.exited = 0
        self.running = 0        # 当前进行中的 arun 数
        self.max_running = 0
        self.cancelled = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, *exc):
        self.exited += 1
        return False

    async def arun(self, url=None, config=None):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(self.sleep)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.running -= 1
        return types.SimpleNamespace(success=False, markdown=None, cleaned_html=None, metadata={})


@pytest.fixture()
def fake_crawl4ai(monkeypatch):
    """注入假的 crawl4ai 模块（CrawlerRunConfig）并复位模块级状态。"""
    mod = types.ModuleType("crawl4ai")
    mod.CrawlerRunConfig = lambda **kw: kw
    monkeypatch.setitem(sys.modules, "crawl4ai", mod)
    monkeypatch.setattr(F, "_crawl4ai_disabled", False)
    monkeypatch.setattr(F, "_crawl4ai_disabled_until", 0.0)
    monkeypatch.setattr(F, "_crawl4ai_crashes", 0)
    return mod


def test_reset_crawler_really_closes_browser(fake_crawl4ai, monkeypatch):
    """旧实现只把引用置 None；现在必须调用 __aexit__ 关掉 Chromium。"""
    crawler = FakeCrawler()
    monkeypatch.setattr(F, "_crawler", crawler)

    asyncio.run(F._reset_crawler())

    assert crawler.exited == 1, "重置时必须真正关闭旧浏览器"
    assert F._crawler is None


def test_crash_circuit_breaker_disables_browser(fake_crawl4ai, monkeypatch):
    """连续崩溃到阈值后应临时熔断，避免不停重启 Chromium。"""
    monkeypatch.setattr(F, "_CRAWL4AI_CRASH_LIMIT", 2)
    monkeypatch.setattr(F, "_CRAWL4AI_COOLDOWN", 60.0)
    monkeypatch.setattr(F, "_crawler", FakeCrawler())

    async def scenario():
        await F._reset_crawler()          # 第 1 次崩溃
        await F._reset_crawler()          # 第 2 次崩溃 → 熔断
        return await F._get_crawler()

    assert asyncio.run(scenario()) is None


def test_timeout_waits_for_arun_before_releasing_slot(fake_crawl4ai, monkeypatch):
    """超时返回时，后台 arun 不能还在跑（否则页面/内存不受并发上限约束）。"""
    crawler = FakeCrawler(sleep=0.3)
    monkeypatch.setattr(F, "_crawler", crawler)
    monkeypatch.setattr(F, "_CRAWL_MIN_TIMEOUT", 0.05)
    monkeypatch.setattr(F, "_CRAWL_MAX_TIMEOUT", 0.05)
    monkeypatch.setattr(F, "_CRAWL_TIMEOUT_GRACE", 0.05)
    monkeypatch.setattr(F, "_crawler_sem", asyncio.Semaphore(5))

    fetcher = F.WebFetcher(timeout=1)
    asyncio.run(fetcher._try_crawl4ai("https://example.com"))

    assert crawler.running == 0, "超时返回后后台页面仍在运行 —— 页面泄漏"
    assert crawler.cancelled == 1, "宽限期内未收敛应被取消并 await 收尾"


def test_semaphore_caps_live_pages(fake_crawl4ai, monkeypatch):
    """并发上限必须约束**活着的页面数**（旧实现在超时场景下形同虚设）。"""
    crawler = FakeCrawler(sleep=0.0)
    monkeypatch.setattr(F, "_crawler", crawler)
    monkeypatch.setattr(F, "_crawler_sem", asyncio.Semaphore(1))

    fetcher = F.WebFetcher(timeout=1)

    async def scenario():
        await asyncio.gather(
            fetcher._try_crawl4ai("https://a.example.com"),
            fetcher._try_crawl4ai("https://b.example.com"),
            fetcher._try_crawl4ai("https://c.example.com"),
        )

    asyncio.run(scenario())
    assert crawler.max_running == 1


def test_trafilatura_limits_are_tightened(monkeypatch):
    """trafilatura 默认 30s / 20MB 必须收到项目尺度。"""
    from trafilatura.settings import DEFAULT_CONFIG

    monkeypatch.setattr(F, "_traf_config_applied", False)
    F._apply_trafilatura_limits()

    assert DEFAULT_CONFIG.getint("DEFAULT", "DOWNLOAD_TIMEOUT") == F._TRAFILATURA_TIMEOUT
    assert DEFAULT_CONFIG.getint("DEFAULT", "MAX_FILE_SIZE") == F._TRAFILATURA_MAX_BYTES

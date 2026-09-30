"""
Hybrid web fetcher: trafilatura (fast, primary) → crawl4ai Playwright (slow, fallback).

基于两轮×48 URLs 实测数据:
  - trafilatura 快路径: ~75% 成功率, ~1s
  - crawl4ai 慢路径补充: +~5-10%, ~3-8s
  - 合计: ~80-85% 成功率

接口完全兼容旧版 WebFetcher。

浏览器生命周期（单例）:
  - _get_crawler(): 首次调用时创建共享 AsyncWebCrawler
  - close_crawler(): 应用关闭时调用, 由 main.py 的 shutdown 钩子触发
  - 所有 WebFetcher 实例共享同一个浏览器, 避免每 URL 启动一个 Chromium
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, List, Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# trafilatura 和 crawl4ai 会在方法内延迟导入，
# 避免未安装时炸掉模块导入。

# 内容太短时视为抓取失败
_MIN_CONTENT_LENGTH = 1000

# ── crawl4ai 浏览器单例 ────────────────────────────────
# 所有 WebFetcher 实例共享同一个浏览器，应用退出时由 main.py 关闭。
_crawler: Any = None
_crawler_lock = asyncio.Lock()
_crawl4ai_disabled = False
# 共享浏览器一次只能跑少量并发页面，太多会导致 Chromium OOM/崩溃 → EPIPE
_crawler_sem = asyncio.Semaphore(5)

# trafilatura 单层失败的域名缓存：反爬站（如 CSDN 521）上 trafilatura 必失败，
# TTL 内跳过它直接走 light/crawl4ai，省掉每次 5-8s 的无谓试错。
_traf_fail_domains: dict[str, float] = {}
_TRAF_FAIL_TTL = 600.0


def _mark_traf_fail(domain: str) -> None:
    import time
    _traf_fail_domains[domain] = time.time()


async def _get_crawler():
    """获取共享的 AsyncWebCrawler 实例（延迟初始化 + 双重检查锁）。

    返回:
        AsyncWebCrawler 实例，或 None（不可用/失败时）。
    """
    global _crawler, _crawl4ai_disabled
    if _crawl4ai_disabled:
        return None
    if _crawler is not None:
        return _crawler
    async with _crawler_lock:
        if _crawler is not None:
            return _crawler
        try:
            from crawl4ai import AsyncWebCrawler

            c = AsyncWebCrawler(verbose=False)
            await c.__aenter__()
            _crawler = c
            logger.info("[Fetcher] Shared crawl4ai browser started")
        except ImportError:
            logger.warning("[Fetcher] crawl4ai 未安装，禁用浏览器回退")
            _crawl4ai_disabled = True
            return None
        except NotImplementedError:
            logger.warning("[Fetcher] crawl4ai Playwright 在此环境不可用（子进程创建失败），永久禁用回退")
            _crawl4ai_disabled = True
            return None
        except Exception as e:
            logger.warning("[Fetcher] 启动共享 crawl4ai 浏览器失败: %s", e)
            _crawl4ai_disabled = True
            return None
    return _crawler


async def close_crawler():
    """关闭共享的 crawl4ai 浏览器。由 main.py 的 shutdown 钩子调用。"""
    global _crawler
    if _crawler is None:
        return
    try:
        await _crawler.__aexit__(None, None, None)
        logger.info("[Fetcher] Shared crawl4ai browser closed")
    except Exception as e:
        logger.warning("[Fetcher] 关闭共享浏览器时出错: %s", e)
    finally:
        _crawler = None


def _reset_crawler():
    """Chromium 崩溃后重置单例，下次请求自动重建。"""
    global _crawler
    _crawler = None
    logger.info("[Fetcher] crawl4ai browser reset (crash or close detected)")


class ScrapedContent(BaseModel):
    """网页抓取结果。"""
    url: str                    # 来源 URL
    title: str                  # 页面标题
    content: str                # 提取的正文文本
    metadata: dict[str, str] = {}  # 页面元数据


class WebFetcher:
    """Hybrid web fetcher.

    主路径 crawl4ai Playwright（反爬/JS 渲染支持，内容最全），
    trafilatura / light HTML 作为静态站兜底。

    Args:
        timeout:     每阶段超时基数(秒), trafilatura 用 min(timeout,8),
                     crawl4ai 用 min(timeout*2, 30)。默认 15。
        rate_limit:  请求间隔(秒), 0 = 不限速。默认 0（修复了旧版 bug）。
        respect_robots: 是否检查 robots.txt (默认 False)。
        user_agent:  User-Agent 字符串（兼容旧接口，trafilatura 会用自己的）。
        use_fallback: 是否使用 crawl4ai (默认 True)。
    """

    def __init__(
        self,
        timeout: int = 15,
        rate_limit: float = 0.0,
        respect_robots: bool = False,
        user_agent: str | None = None,
        use_fallback: bool = True,
        use_light_fallback: bool = True,
    ):
        self.timeout = timeout
        self.rate_limit = rate_limit
        self.respect_robots = respect_robots
        self.user_agent = user_agent
        self.use_fallback = use_fallback
        self.use_light_fallback = use_light_fallback
        self._last_fetch_ts = 0.0
        self._lock = asyncio.Lock()
        self._patch_trafilatura_pool()

    @staticmethod
    def _patch_trafilatura_pool():
        """把 trafilatura 的 urllib3 连接池 maxsize 从 1 调到 10。

        trafilatura 默认 num_pools=50 但 maxsize=1，同域名只能串行。
        调大后同域名可并发抓取，减少超时等待。
        """
        try:
            import urllib3
            import trafilatura.downloads as dl

            original_create_pool = dl.create_pool

            def patched_create_pool(**args):
                args.setdefault("maxsize", 10)
                return original_create_pool(**args)

            dl.create_pool = patched_create_pool
            logger.debug("Patched trafilatura urllib3 pool maxsize=10")
        except Exception as e:
            logger.debug("Failed to patch trafilatura pool: %s", e)

    # ── 限速 ────────────────────────────────────────────

    async def _rate_limit(self):
        """请求限速。rate_limit <= 0 时直接跳过（修复旧版除以零 bug）。"""
        if self.rate_limit <= 0:
            return
        async with self._lock:
            import time
            now = time.monotonic()
            min_interval = 1.0 / self.rate_limit
            elapsed = now - self._last_fetch_ts
            to_wait = min_interval - elapsed
            if to_wait > 0:
                await asyncio.sleep(to_wait)
            self._last_fetch_ts = time.monotonic()

    # ── robots.txt ───────────────────────────────────────

    async def _check_robots(self, url: str):
        if not self.respect_robots:
            return
        from urllib.parse import urlparse
        from .robots import RobotsChecker
        domain = urlparse(url).netloc
        try:
            allowed = await RobotsChecker().can_fetch(url, domain)
        except Exception:
            allowed = True
        if not allowed:
            raise RuntimeError(f"Disallowed by robots.txt: {url}")

    # ── 对外接口 ─────────────────────────────────────────

    async def fetch(self, url: str) -> ScrapedContent:
        """抓取并解析一个网页。

        Args:
            url: 目标 URL

        Returns:
            结构化的网页抓取结果

        Raises:
            RuntimeError: 所有抓取方法均失败
        """
        await self._rate_limit()
        await self._check_robots(url)

        # Phase 1: crawl4ai Playwright 主路径（反爬/JS 渲染支持，内容最全）
        if self.use_fallback:
            result = await self._try_crawl4ai(url)
            if result:
                return result

        # Phase 2: trafilatura 快路径兜底（已知必失败域名直接跳过）
        from urllib.parse import urlparse as _urlparse
        import time as _time
        domain = _urlparse(url).netloc
        if domain in _traf_fail_domains and _time.time() - _traf_fail_domains[domain] < _TRAF_FAIL_TTL:
            result = None
        else:
            result = await self._try_trafilatura(url)
        if result:
            return result

        # Phase 3: light HTML (BS4) 兜底
        if self.use_light_fallback:
            result = await self._try_light_html(url)
            if result:
                _mark_traf_fail(domain)
                return result

        raise RuntimeError(f"Failed to fetch {url}: 所有抓取方法均失败")

    async def fetch_multiple(
        self, urls: List[str], max_concurrent: int = 5
    ) -> List[ScrapedContent]:
        """并发抓取多个 URL（带信号量限流）。"""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def fetch_one(url: str) -> ScrapedContent | None:
            async with semaphore:
                try:
                    return await self.fetch(url)
                except Exception as e:
                    logger.warning("[Fetcher] Failed: %s - %s", url, e)
                    return None

        tasks = [fetch_one(url) for url in urls]
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    async def close(self):
        """兼容旧接口。浏览器为模块级单例，由 close_crawler() / main.py shutdown 统一关闭。"""
        pass

    # ── Phase 1: trafilatura ─────────────────────────────

    async def _try_trafilatura(self, url: str) -> ScrapedContent | None:
        """用 trafilatura 抓取+提取正文。

        默认超时: min(max(timeout, 3), 8) 秒。
        """
        import trafilatura

        # ponytail: 缩到 5s（trafilatura 快站 ~1s 就返回，省 3s 留给 BS4 + crawl4ai）
        fast_timeout = min(max(self.timeout, 3), 5)

        try:
            dl = await asyncio.wait_for(
                asyncio.to_thread(trafilatura.fetch_url, url),
                timeout=fast_timeout,
            )
            if not dl:
                logger.debug("[Fetcher] trafilatura fetch_url=None: %s", url[:60])
                return None

            text = await asyncio.to_thread(
                lambda: trafilatura.extract(
                    dl,
                    include_comments=False,
                    include_tables=True,
                    favor_precision=False,
                    no_fallback=False,
                ),
            )
            if not text or len(text) < _MIN_CONTENT_LENGTH:
                logger.debug(
                    "[Fetcher] trafilatura content short (%s): %s",
                    len(text or ""), url[:60],
                )
                return None

            meta = trafilatura.extract_metadata(dl)
            title = meta.title if meta and meta.title else ""
            logger.info(
                "[Fetcher] trafilatura OK: %s chars from %s",
                len(text), url[:60],
            )
            return ScrapedContent(url=url, title=title, content=text, metadata={})

        except asyncio.TimeoutError:
            logger.debug("[Fetcher] trafilatura timeout (>%ss): %s", fast_timeout, url[:60])
            return None
        except Exception as e:
            logger.debug("[Fetcher] trafilatura error: %s - %s", str(e)[:80], url[:60])
            return None

    # ── Phase 2: light HTML (BS4) ──────────────────────────

    async def _try_light_html(self, url: str) -> ScrapedContent | None:
        """用 httpx + BeautifulSoup 抓取正文（trafilatura 和 crawl4ai 之间）。"""
        from backend.scraper.light_fetcher import fetch_light

        result = await fetch_light(url)
        if result is None:
            return None
        return ScrapedContent(
            url=url,
            title=result.get("title", ""),
            content=result["content"],
        )

    # ── Phase 3: crawl4ai 回退 ──────────────────────────
    # 使用模块级单例浏览器（_get_crawler），不再每 URL 创建新实例。

    async def _try_crawl4ai(self, url: str) -> ScrapedContent | None:
        """用 crawl4ai Playwright 抓取 JS 渲染页面（共享浏览器实例）。"""
        c = await _get_crawler()
        if c is None:
            return None

        try:
            from crawl4ai import CrawlerRunConfig
        except ImportError:
            return None

        slow_timeout = min(max(self.timeout * 2, 15), 30)
        page_timeout_ms = min(int(slow_timeout * 1000 * 0.8), 20000)

        async with _crawler_sem:
            r = None
            for attempt in range(2):  # 反爬拦截是概率性的（CSDN 实测 2/3 成功），重试一次
                try:
                    cfg = CrawlerRunConfig(
                        word_count_threshold=10,
                        remove_overlay_elements=True,
                        wait_until="domcontentloaded",
                        page_timeout=page_timeout_ms,
                        magic=True,  # crawl4ai 0.9.0 反爬组合模式（隐藏自动化指纹+模拟人类行为）
                        verbose=False,
                    )
                    arun_task = asyncio.ensure_future(c.arun(url=url, config=cfg))
                    try:
                        r = await asyncio.wait_for(asyncio.shield(arun_task), timeout=slow_timeout)
                    except asyncio.TimeoutError:
                        # 超时不取消 arun：取消会让 playwright 内部 future 异常泄漏
                        # （TargetClosedError: future exception was never retrieved）。
                        # 让它自然结束，done_callback 消费结果/异常防止泄漏。
                        arun_task.add_done_callback(
                            lambda t: t.exception() if not t.cancelled() else None
                        )
                        return None
                except (BrokenPipeError, ConnectionResetError):
                    _reset_crawler()
                    return None
                except Exception as e:
                    msg = str(e)
                    if any(kw in msg for kw in (
                        "Target page, context or browser has been closed",
                        "Protocol error", "Browser has been closed",
                        "has been closed", "Target closed",
                    )):
                        _reset_crawler()
                    logger.debug("[Fetcher] crawl4ai error: %s - %s", msg[:80], url[:60])
                    return None
                if r and r.success:
                    break
                logger.debug(
                    "[Fetcher] crawl4ai attempt %d blocked (status=%s): %s",
                    attempt + 1, getattr(r, "status_code", "?"), url[:60],
                )
                r = None

        if not r or not r.success:
            return None

        text = ""
        if r.markdown:
            md = r.markdown
            text = md.raw_markdown if hasattr(md, "raw_markdown") else str(md)
        if not text and r.cleaned_html:
            from bs4 import BeautifulSoup
            text = BeautifulSoup(r.cleaned_html, "html.parser").get_text(
                " ", strip=True
            )

        if not text or len(text) < _MIN_CONTENT_LENGTH:
            return None

        title = (r.metadata.get("title", "") if r.metadata else "")[:80]
        logger.info(
            "[Fetcher] crawl4ai OK: %s chars from %s",
            len(text), url[:60],
        )
        return ScrapedContent(url=url, title=title, content=text, metadata={})

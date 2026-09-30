"""
域名质量追踪器 — 全局 SQLite 持久化，跨 session 共享抓取经验。
"""

from __future__ import annotations

import logging
import time
import threading
from typing import Optional
from urllib.parse import urlparse

from backend.storage.database import DatabaseManager
from backend.config import (
    DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT,
    DOMAIN_QUALITY_BLOCKED_SECONDS,
    DOMAIN_QUALITY_MIN_SAMPLES,
)


logger = logging.getLogger(__name__)


def _extract_domain(url: str) -> str:
    netloc = urlparse(url).netloc
    return netloc[4:] if netloc.startswith("www.") else netloc


# 已知低质域名（日志实证：抓取成功但内容无价值，或高频失败），永久跳过。
# 与自动拉黑（失败计数）互补：这些域从不"失败"，自动机制对它们盲区。
_LOW_QUALITY_DOMAINS: frozenset[str] = frozenset({
    "book118.com",       # 付费文档预览页，稳定返回 ~1KB 无价值内容
    "doc88.com",         # 文档分享站
    "docin.com",         # 豆丁网
    "xjishu.com",        # 专利采集站
    "zbj.com",           # 问答/外包站，高频超时
    "528045.com",        # 内容农场
    "kmw.com",           # 采集站
    "wenwen.sogou.com",  # 搜狗问问
    "iask.sina.com.cn",  # 新浪爱问（403）
    "xueshu.com",        # 学术采集站
    "xueshu.com.cn",     # 学术采集站
    "zzwws.cn",          # 404 站
    "4qx.net",           # 阿启网：词典/命理类，内容简短无深度
    "jis.pku.edu.cn",    # 北大某子站页面，内容稀少
    "zupu.cn",           # 族谱网词条，内容量少
    "renrendoc.com",     # 人人文库：文档预览页，内容少且常 404
    # ── 文档聚合站/题库站/内容农场（2026-08 数据库实证：抓取"成功"但内容无价值，
    #    长而低质页面骗过长度门槛，自动熔断对它们盲区——从不失败）──
    "wendoc.com",        # 文档分享站（doc.wendoc.com 实测 15.9k 字符聚合页）
    "zixin.com.cn",      # 文档站（实测 12.5k 字符聚合页）
    "dxsbb.com",         # 大学生必备网：低质聚合，且重定向到 yingzaizhiyuan.com
    "yingzaizhiyuan.com",# dxsbb 的重定向目标站
    "360docs.net",       # 360 文档聚合站
    "shuashuati.com",    # 刷刷题：题库预览页
    "zybang.com",        # 作业帮：问答聚合页
    "shangxueba.com",    # 上学吧：题库/答案站
    "jinchutou.com",     # 金锄头文库
    "mx-xz.com",         # 谋学网：文库
    "taodocs.com",       # 淘豆文档
    "doczj.com",         # 文档站（m.doczj.com 12/12 全失败实测）
    "wenkub.com",        # 文库宝
    "dswenku.com",       # 得书文库
    "21cnjy.com",        # 21 世纪教育网：试题资源站
    "ppkao.com",         # 拍拍考：题库站
    "zxxk.com",          # 中学学科网：试卷资源站
    "freetiku.com",      # 免费题库 223：题库答案页（实测 7/7 抓取成功，评分公式给 d_s=1.0 满分）
    "book.qq.com",       # QQ 阅读：电子书章节页（无知识卡片价值）
    # CSDN 低质子域单独入黑名单——白名单豁免救活了 blog.csdn.net，也顺带豁免了
    # 这些垃圾子域（实测 wenku.csdn.net/answer 16.9k 字符聚合页、download 下载页高频入选）
    "wenku.csdn.net",    # CSDN 文库/问答聚合页
    "download.csdn.net", # CSDN 下载页（需积分，无正文价值）
    "arxiv.org",         # arXiv 论文摘要页（/abs/）：均为概述，知识卡片价值低（用户反馈）
})

# 高质量主域白名单：内容质量稳定、正文可抓、与知识库场景高度相关。
# 技术社区 + 人文社科（百科/文化条目）。
# 注：zh.wikipedia.org 实测当前网络环境不可达（429/超时），未列入。
# 这些域名豁免连续失败熔断——失败多为反爬/网络抖动（CSDN 521 反爬、知乎限流），
# 而非内容质量问题，不应被自动拉黑 30 天。
_HIGH_QUALITY_DOMAINS: frozenset[str] = frozenset({
    "csdn.net", "cnblogs.com", "zhihu.com", "juejin.cn",
    "infoq.cn", "segmentfault.com", "developer.aliyun.com",
    "cloud.tencent.com", "volcengine.com", "jianshu.com",
    "oschina.net", "51cto.com", "geekbang.org",
    "learn.microsoft.com", "github.com",
    "baike.baidu.com",   # 百度百科：人文社科词条，实测可抓 12 万字符完整正文
    "douban.com",        # 豆瓣：书籍/文化条目，实测可抓（正文含少量导航噪音）
    # 注：arxiv.org 原在白名单，2026-08 移入黑名单——/abs/ 摘要页均为概述，知识卡片价值低
})


class _DomainQualityCache:
    """线程安全的域名质量单例。惰性初始化，通过 get_domain_quality() 获取。"""

    def __init__(self, network_blocked: set[str] | None = None):
        self._network_blocked: set[str] = (network_blocked or set()) | set(_LOW_QUALITY_DOMAINS)
        self._cache: dict[str, tuple[float, float]] = {}
        self._cache_ttl = 5.0
        self._lock = threading.Lock()

    def record_success(self, url: str, content_length: int, method: str = "") -> None:
        domain = _extract_domain(url)
        now = time.time()
        db = DatabaseManager()
        was_blocked = db.execute(
            "SELECT blocked_until FROM domain_quality WHERE domain = ?",
            (domain,),
        ).fetchone()
        was_blocked_until = was_blocked["blocked_until"] if was_blocked else 0
        db.execute("""
            INSERT INTO domain_quality (
                domain, fetch_count, success_count, fail_count,
                total_content_len, consecutive_fails, blocked_until,
                first_seen, last_updated, last_method
            ) VALUES (?, 1, 1, 0, ?, 0, 0, ?, ?, ?)
            ON CONFLICT(domain) DO UPDATE SET
                fetch_count = fetch_count + 1,
                success_count = success_count + 1,
                total_content_len = total_content_len + ?,
                consecutive_fails = 0,
                blocked_until = 0,
                last_updated = ?,
                last_method = ?
        """, (domain, content_length, now, now, method,
              content_length, now, method))
        db.commit()
        with self._lock:
            self._cache.pop(domain, None)
        if was_blocked_until > now:
            logger.info(
                "[Blocklist] Unblocked %s (was blocked, now fetching OK via %s, %d chars)",
                domain, method or "?", content_length,
            )

    def block_domain(self, url_or_domain: str) -> bool:
        """强制封禁域名 30 天。返回是否新触发封禁。"""
        domain = (
            _extract_domain(url_or_domain)
            if "://" in url_or_domain else url_or_domain
        )
        if not domain:
            return False
        now = time.time()
        blocked_until = now + DOMAIN_QUALITY_BLOCKED_SECONDS
        db = DatabaseManager()
        prev = db.execute(
            "SELECT blocked_until FROM domain_quality WHERE domain = ?",
            (domain,),
        ).fetchone()
        prev_blocked_until = prev["blocked_until"] if prev else 0
        db.execute("""
            INSERT INTO domain_quality (
                domain, fetch_count, success_count, fail_count,
                total_content_len, consecutive_fails, blocked_until,
                first_seen, last_updated, last_method
            ) VALUES (?, 0, 0, 0, 0, ?, ?, ?, ?, '')
            ON CONFLICT(domain) DO UPDATE SET
                consecutive_fails = MAX(consecutive_fails, ?),
                blocked_until = MAX(blocked_until, ?),
                last_updated = ?
        """, (domain, DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT,
              blocked_until, now, now,
              DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT,
              blocked_until, now))
        db.commit()
        with self._lock:
            self._cache.pop(domain, None)
        if prev_blocked_until <= now:
            logger.info(
                "[Blocklist] Blocked %s for %d days (manual block_domain)",
                domain, int(DOMAIN_QUALITY_BLOCKED_SECONDS // 86400),
            )
        else:
            logger.info(
                "[Blocklist] Refreshed ban on %s (was already blocked, extending to %d days)",
                domain, int(DOMAIN_QUALITY_BLOCKED_SECONDS // 86400),
            )
        return True

    def is_blocked(self, url_or_domain: str) -> bool:
        """域名是否处于黑名单（静态低质域或 blocked_until > now 的自动拉黑）。"""
        domain = (
            _extract_domain(url_or_domain)
            if "://" in url_or_domain else url_or_domain
        )
        if not domain:
            return False
        # 静态黑名单：精确匹配 + 子域后缀匹配（max.book118.com 命中 book118.com）
        if domain in self._network_blocked or any(
            domain.endswith("." + d) for d in self._network_blocked
        ):
            return True
        now = time.time()
        db = DatabaseManager()
        row = db.execute(
            "SELECT blocked_until FROM domain_quality WHERE domain = ?",
            (domain,),
        ).fetchone()
        return bool(row and row["blocked_until"] > now)

    def _is_whitelisted(self, domain: str) -> bool:
        """主域是否命中白名单（精确 + 子域后缀匹配：blog.csdn.net 命中 csdn.net）。"""
        return domain in _HIGH_QUALITY_DOMAINS or any(
            domain.endswith("." + d) for d in _HIGH_QUALITY_DOMAINS
        )

    def _unblock_whitelisted(self) -> None:
        """启动时解封被误熔断的白名单域名（幂等，仅执行一次）。

        白名单豁免熔断上线前，blog.csdn.net / zhuanlan.zhihu.com 等子域已因
        连续失败被自动拉黑 30 天。熔断期间不被抓取 → 永远收不到 success 解封 →
        死锁到期。此处一次性清除，恢复这些站点的可用性。
        """
        db = DatabaseManager()
        rows = db.execute(
            "SELECT domain FROM domain_quality WHERE blocked_until > ?",
            (time.time(),),
        ).fetchall()
        fixed = [r["domain"] for r in rows if self._is_whitelisted(r["domain"])]
        for domain in fixed:
            db.execute(
                "UPDATE domain_quality SET blocked_until = 0, consecutive_fails = 0 WHERE domain = ?",
                (domain,),
            )
        if fixed:
            db.commit()
            logger.info(
                "[Blocklist] Unblocked %d whitelisted domains (circuit breaker exemption): %s",
                len(fixed), ", ".join(fixed),
            )

    def record_failure(self, url: str) -> None:
        domain = _extract_domain(url)
        now = time.time()
        db = DatabaseManager()
        if self._is_whitelisted(domain):
            # 白名单豁免熔断：失败多为反爬/网络抖动（CSDN 521、知乎限流），
            # 非内容质量问题。只记录 fetch/fail 供统计，不累计 consecutive_fails、
            # 不触发熔断（历史教训：blog.csdn.net 93% 成功率仍被连续 3 失败拉黑）。
            db.execute("""
                INSERT INTO domain_quality (
                    domain, fetch_count, success_count, fail_count,
                    total_content_len, consecutive_fails, blocked_until,
                    first_seen, last_updated, last_method
                ) VALUES (?, 1, 0, 1, 0, 0, 0, ?, ?, '')
                ON CONFLICT(domain) DO UPDATE SET
                    fetch_count = fetch_count + 1,
                    fail_count = fail_count + 1,
                    last_updated = ?
            """, (domain, now, now, now))
            db.commit()
            with self._lock:
                self._cache.pop(domain, None)
            return
        row = db.execute(
            "SELECT consecutive_fails, blocked_until FROM domain_quality WHERE domain = ?",
            (domain,)
        ).fetchone()
        old_consecutive = row["consecutive_fails"] if row else 0
        old_blocked_until = row["blocked_until"] if row else 0
        new_consecutive = old_consecutive + 1
        will_block = new_consecutive >= DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT
        blocked = (
            now + DOMAIN_QUALITY_BLOCKED_SECONDS if will_block else 0
        )
        db.execute("""
            INSERT INTO domain_quality (
                domain, fetch_count, success_count, fail_count,
                total_content_len, consecutive_fails, blocked_until,
                first_seen, last_updated, last_method
            ) VALUES (?, 1, 0, 1, 0, ?, ?, ?, ?, '')
            ON CONFLICT(domain) DO UPDATE SET
                fetch_count = fetch_count + 1,
                fail_count = fail_count + 1,
                consecutive_fails = ?,
                blocked_until = ?,
                last_updated = ?
        """, (domain, new_consecutive, blocked, now, now,
              new_consecutive, blocked, now))
        db.commit()
        with self._lock:
            self._cache.pop(domain, None)
        if will_block:
            if old_blocked_until <= now:
                logger.info(
                    "[Blocklist] Blocked %s for %d days (failed %d/%d consecutive times)",
                    domain,
                    int(DOMAIN_QUALITY_BLOCKED_SECONDS // 86400),
                    new_consecutive,
                    DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT,
                )
            else:
                logger.info(
                    "[Blocklist] Extended ban on %s to %d more days (failed %d consecutive times)",
                    domain,
                    int(DOMAIN_QUALITY_BLOCKED_SECONDS // 86400),
                    new_consecutive,
                )
        else:
            logger.debug(
                "[Blocklist] Failure recorded for %s (%d/%d consecutive, will block at %d)",
                domain, new_consecutive,
                DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT,
                DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT,
            )

    def score(self, url_or_domain: str) -> float:
        domain = (
            _extract_domain(url_or_domain)
            if "://" in url_or_domain else url_or_domain
        )
        # 静态黑名单：精确 + 子域后缀匹配（doc.wendoc.com 命中 wendoc.com）
        if domain in self._network_blocked or any(
            domain.endswith("." + d) for d in self._network_blocked
        ):
            return 0.0

        now = time.time()
        with self._lock:
            cached = self._cache.get(domain)
            if cached and now - cached[1] < self._cache_ttl:
                return cached[0]

        db = DatabaseManager()
        row = db.execute(
            "SELECT * FROM domain_quality WHERE domain = ?", (domain,)
        ).fetchone()

        if row is None:
            s = 0.5
        elif row["blocked_until"] > now:
            s = 0.0
        elif row["fetch_count"] < DOMAIN_QUALITY_MIN_SAMPLES:
            s = 0.55
        else:
            total = max(row["fetch_count"], 1)
            success_rate = row["success_count"] / total
            avg_len = row["total_content_len"] / max(row["success_count"], 1)
            content_score = min(avg_len / 500, 1.0)
            fail_penalty = min(row["consecutive_fails"] * 0.15, 0.5)
            s = max(success_rate * 0.6 + content_score * 0.4 - fail_penalty, 0.0)

        with self._lock:
            self._cache[domain] = (s, now)
        return s

    def is_network_blocked(self, domain: str) -> bool:
        """静态黑名单：精确 + 子域后缀匹配（doc.wendoc.com 命中 wendoc.com）。

        select_top 的剔除检查——子域不匹配的历史 bug 导致 max.book118.com /
        doc.wendoc.com 等子域从未被静态黑名单拦住（数据库 35 次抓取实证）。
        """
        return domain in self._network_blocked or any(
            domain.endswith("." + d) for d in self._network_blocked
        )

    def list_blocked(self, limit: int = 100) -> list[str]:
        """返回当前所有应排除的域名（静态低质域 + 自动拉黑），按封禁时间倒序取前 limit 个。

        Bocha API 的 exclude 参数最多 100 个域名，超过会被服务端截断。
        """
        db = DatabaseManager()
        rows = db.execute(
            "SELECT domain FROM domain_quality WHERE blocked_until > ? "
            "ORDER BY blocked_until DESC LIMIT ?",
            (time.time(), limit),
        ).fetchall()
        blocked = [r["domain"] for r in rows]
        # 静态低质域优先（它们才是主要浪费源），自动拉黑补位
        static = [d for d in self._network_blocked if d not in blocked]
        return (static + blocked)[:limit]

    def summary(self) -> dict:
        db = DatabaseManager()
        total = db.execute("SELECT COUNT(*) as c FROM domain_quality").fetchone()
        blocked = db.execute(
            "SELECT COUNT(*) as c FROM domain_quality WHERE blocked_until > ?",
            (time.time(),)
        ).fetchone()
        return {"total_domains": total["c"] if total else 0,
                "blocked": blocked["c"] if blocked else 0}


_instance: Optional[_DomainQualityCache] = None
_instance_lock = threading.Lock()


def get_domain_quality(network_blocked: set[str] | None = None) -> _DomainQualityCache:
    global _instance
    if _instance is not None:
        return _instance
    with _instance_lock:
        if _instance is None:
            _instance = _DomainQualityCache(network_blocked=network_blocked)
            _instance._unblock_whitelisted()
    return _instance

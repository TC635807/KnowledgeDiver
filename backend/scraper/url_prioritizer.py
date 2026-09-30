"""
URL 优先级评分器 — 从搜索结果筛选最值得抓取的 URL。
所有评分信号零模型依赖，纯启发式。
"""

from __future__ import annotations

import logging
import re
from typing import List
from urllib.parse import urlparse

from backend.pipeline.stages import SearchResult
from backend.scraper.domain_quality import get_domain_quality, _extract_domain, _HIGH_QUALITY_DOMAINS
from backend.config import DOMAIN_QUALITY_TOP_URLS

logger = logging.getLogger(__name__)


_LOW_QUALITY_PATH_PATTERNS = [
    r'/tag/', r'/tags/', r'/category/', r'/categories/',
    r'/author/', r'/authors/', r'/user/', r'/users/',
    r'/search', r'/login', r'/register', r'/signup',
    r'/about', r'/contact', r'/privacy', r'/terms',
    r'/page/\d+', r'\?page=\d+', r'/archives?/', r'/feed/', r'/rss/',
    r'/item/', r'/course/', r'/ask/', r'/question/',
]

# 硬排除：这些路径类型的页面无正文价值（个人主页/商品页/PR讨论/视频页），
# 直接丢弃，避免占 select_top 配额和浪费抓取。
_EXCLUDE_PATH_PATTERNS = [
    # 个人主页 / 用户页
    r'/user/', r'/users/', r'/profile/', r'/people/', r'/member/', r'/author/',
    # 电商商品页
    r'/product/', r'/itemvideo/', r'/goods/', r'/sku/', r'/shop/',
    # 电商子域（product.dangdang.com / item.jd.com / detail.tmall.com 等）
    r'//product\.', r'//item\.jd\.com/', r'//detail\.tmall\.com/', r'//item\.taobao\.com/',
    # GitHub 非内容页（PR/Issue 讨论、文件 diff）
    r'/pull/', r'/issues/', r'/commit/',
    # CSDN 下载/标签聚合页（无正文价值，实测在候选池高频出现）
    r'/download/', r'/tagalbum/',
    # 视频页（JS 渲染，抓不到正文）
    r'/video/', r'/watch\?',
    # 登录跳转
    r'/login', r'/accounts/page/login',
]

_LOW_QUALITY_TITLE_KEYWORDS = [
    '404', '403', 'error', 'not found', '页面不存在',
    '登录', '注册', '验证码',
]

# 高质量主域白名单定义已移至 domain_quality.py（域名质量语义归属底层模块，
# 且 record_failure 需用同一份白名单做熔断豁免，避免双份定义漂移）。
# 见 _HIGH_QUALITY_DOMAINS：技术社区 + 人文社科（百科/文化条目）。


def _site_quality(domain: str) -> float:
    """主域匹配高质量技术社区 → 1.0，否则 0.5（同分时高质量站点优先）。"""
    for hd in _HIGH_QUALITY_DOMAINS:
        if domain == hd or domain.endswith("." + hd):
            return 1.0
    return 0.5


def is_excluded_url(url: str) -> bool:
    """URL 结构硬排除：命中个人主页/商品页/PR/视频等模式返回 True。"""
    for pattern in _EXCLUDE_PATH_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return True
    return False


def _url_structure_score(url: str) -> float:
    for pattern in _LOW_QUALITY_PATH_PATTERNS:
        if re.search(pattern, url, re.IGNORECASE):
            return 0.2
    return 1.0


def _title_score(title: str) -> float:
    if not title or not title.strip():
        return 0.3
    t = title.strip()
    for kw in _LOW_QUALITY_TITLE_KEYWORDS:
        if kw.lower() in t.lower():
            return 0.1
    if re.match(r'^[A-Z\s]{10,}$', t):
        return 0.2
    if len(t) <= 5:
        return 0.4
    return 1.0


def _snippet_relevance(snippet: str, query: str) -> float:
    if not snippet or not query:
        return 0.5
    query_words = set(re.findall(r'[\u4e00-\u9fff\w]{2,}', query.lower()))
    if not query_words:
        return 0.5
    snippet_lower = snippet.lower()
    hits = sum(1 for w in query_words if w in snippet_lower)
    return hits / len(query_words)


def select_top(
    results: List[SearchResult],
    query: str = "",
    top_n: int = DOMAIN_QUALITY_TOP_URLS,
    min_score: float = 0.15,
) -> List[SearchResult]:
    dqc = get_domain_quality()
    excluded = 0
    dup_skipped = 0
    blocked = 0
    scored = []
    seen_paths: set[str] = set()
    for r in results:
        if is_excluded_url(r.url):
            excluded += 1
            continue
        # 按 path 去重（剥离 query）：?locationNum=11 与 ?locationNum=10 是同一页面，
        # 候选池扩大后此类重复显著增多，不剔除会重复抓取浪费抓取额度。
        path = urlparse(r.url).path
        if path in seen_paths:
            dup_skipped += 1
            continue
        seen_paths.add(path)
        domain = _extract_domain(r.url)
        if dqc.is_network_blocked(domain):
            blocked += 1
            continue
        d_s = dqc.score(domain)
        u_s = _url_structure_score(r.url)
        t_s = _title_score(r.title)
        s_s = _snippet_relevance(r.snippet, query)
        site_s = _site_quality(domain)
        total = d_s * 0.30 + u_s * 0.10 + t_s * 0.10 + s_s * 0.30 + site_s * 0.20
        scored.append((r, total, site_s == 1.0, d_s, u_s, t_s, s_s, site_s))
    if excluded or dup_skipped or blocked:
        logger.info(
            "[select_top] query=%r: %d candidates → excluded %d (structure) / dup %d (path) / blocklist %d → %d scored",
            query, len(results), excluded, dup_skipped, blocked, len(scored),
        )
    # 相关性门槛：query 非空时，snippet 与搜索词零重叠的 URL 不参与评选——
    # 实测"太刀/猎人小刀/彩鸟"等跨领域多义词的搜索结果里，snippet 不含搜索词的
    # 页面多是其他游戏/真实世界的同名概念（讨鬼传太刀、星界边境小刀、真实鸟类），
    # 白名单硬优先会让它们照样入选。仅当全部候选都无相关性时才放宽（不空手而归）。
    if query:
        relevant = [x for x in scored if x[6] > 0.0]
        if relevant:
            dropped = len(scored) - len(relevant)
            scored = relevant
            logger.info(
                "[select_top] relevance gate: %d/%d candidates have snippet overlap with query, dropped %d",
                len(relevant), len(scored) + dropped, dropped,
            )
    scored.sort(key=lambda x: x[1], reverse=True)
    # 白名单硬优先：分层取选——site_s 0.2 权重仅 +0.1 优势，压不住 d_s=1.0 的低质站
    # （freetiku 7/7 成功实测 0.900 vs cnblogs 0.992 只差 0.09）。
    # 白名单候选先按分数取满 top_n，非白名单只能补位——候选池有白名单站点时优先选它们。
    whitelisted = [x for x in scored if x[2]]
    others = [x for x in scored if not x[2]]
    selected: list[tuple] = []
    for item in whitelisted:
        if item[1] < min_score:
            continue
        selected.append(item)
        if len(selected) >= top_n:
            break
    for item in others:
        if len(selected) >= top_n:
            break
        if item[1] < min_score:
            break
        selected.append(item)
    # 详细日志：进入爬取的链接及其评分明细（排查低质 URL 入选时直接看这里）
    logger.info("[select_top] → selected %d URLs for fetching (score / d=域名分 site=白名单 title=标题 snippet=相关性 struct=结构):",
                len(selected))
    for i, (r, s, is_wl, d_s, u_s, t_s, s_s, site_s) in enumerate(selected, 1):
        logger.info(
            "  %d. %s  score=%.3f%s (d=%.2f site=%.1f title=%.2f snippet=%.2f struct=%.2f)",
            i, r.url, s, " [白名单]" if is_wl else "",
            d_s, site_s, t_s, s_s, u_s,
        )
    return [x[0] for x in selected]

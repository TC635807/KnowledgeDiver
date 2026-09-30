"""
全局默认配置模块。

所有环境变量读取和默认值集中于此文件，改此一处全局生效。
按类别组织：AI、搜索、流水线、抓取、存储、服务。

Usage:
    from backend.config import AI_API_KEY, BOCHA_API_URL, PIPELINE_DEFAULTS, ...
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
# AI 提供商配置
# ═══════════════════════════════════════════════════════════════════

AI_API_URL: str = os.getenv("AI_API_URL", "https://api.deepseek.com")
AI_API_KEY: str = os.getenv("AI_API_KEY", "")
AI_MODEL: str = os.getenv("AI_MODEL", "deepseek-v4-flash")
AI_CONCURRENCY: int = int(os.getenv("AI_CONCURRENCY", "2"))
AI_PROXY_PORT: int = int(os.getenv("PROXY_PORT", "0"))

# Agent 专用模型（默认与 AI_MODEL 一致，统一绑定 .env 的 AI_MODEL）
AGENT_MODEL: str = os.getenv("AGENT_MODEL", AI_MODEL)
# Agent 思考强度（deepseek-v4-flash 支持 low/high/max；思考模式开启时 temperature 不生效）
AGENT_REASONING_EFFORT: str = os.getenv("AGENT_REASONING_EFFORT", "high")
# 实验开关：full=完整 Agent；no_quality=移除质量工具与树注入（消融条件）。
AGENT_EVAL_MODE: str = os.getenv("AGENT_EVAL_MODE", "full")
# 实验目标卡数上限：达到后 decide 只给读工具，不再允许联网写卡。0=禁用。
AGENT_EVAL_MAX_CARDS: int = int(os.getenv("AGENT_EVAL_MAX_CARDS", "0"))
# 实验目标卡数下限：/loop 模式下低于该值不允许输出最终总结，强制继续建卡。0=禁用。
AGENT_EVAL_MIN_CARDS: int = int(os.getenv("AGENT_EVAL_MIN_CARDS", "0"))

# ── T12 P0 安全开关（默认全部保持旧行为；实现见 backend/agent/tool_registry.py）
# 工具权限分级：legacy=不拦截（默认）| no_write=禁用写层 | readonly=仅允许读层
TOOL_PERMISSION_MODE: str = os.getenv("KD_TOOL_PERMISSION", "legacy")
# 工具调用审计（JSONL）：0=关闭（默认，零副作用）| 1=开启
TOOL_AUDIT_ENABLED: bool = os.getenv("KD_TOOL_AUDIT", "0").strip().lower() in ("1", "true", "yes", "on")
TOOL_AUDIT_PATH: str = os.getenv("KD_TOOL_AUDIT_PATH", "data/agent_tool_audit.jsonl")
# 不可信外部内容包裹（间接提示注入防护）：legacy=原样（默认）| on=加边界+免责声明
UNTRUSTED_WRAP_MODE: str = os.getenv("KD_UNTRUSTED_WRAP", "legacy")
# 入口级覆盖（task-44 补齐 E4/E5）：inherit=跟随 KD_UNTRUSTED_WRAP（默认）| on | off
#   E4 = 工具返回值跨 MCP 边界（backend/mcp/server.py::to_mcp_content）
#   E5 = MCP 资源描述/内容（外部 server 描述与出站资源文本）
UNTRUSTED_WRAP_TOOL_RESULTS: str = os.getenv("KD_UNTRUSTED_WRAP_TOOL_RESULTS", "inherit")
UNTRUSTED_WRAP_MCP: str = os.getenv("KD_UNTRUSTED_WRAP_MCP", "inherit")

AGENT_MAX_TURNS: int = 20               # Agent 每轮最多 ReAct 循环次数（实测 12 轮仍常见一层树——盘点/挂载杂务占 6-7 轮，扩到 20 支持更深树；成本上限相应放宽）
AGENT_CONTEXT_MAX_MESSAGES: int = 20    # 保留最近 N 条对话历史
AGENT_CARD_CONTENT_MAX_CHARS: int = 2000  # get_card_info 返回内容截断长度
AGENT_TIMEOUT: int = 300               # Agent 单次请求总超时（秒）— pipeline 操作耗时长


# ═══════════════════════════════════════════════════════════════════
# 搜索提供商配置
# ═══════════════════════════════════════════════════════════════════

# 全局默认搜索提供商，改此一处，所有路由/工厂/PipelineAPI 同步切换
# 可选值: "free" | "bocha" | "baidu" | "exa"
#   free  —— 免费多引擎（Bing/AnySearch/Exa-MCP/DDG/SearXNG），无需任何 API key，
#            取链机制对齐 DeepSeek Harness 的 dsh-free-search（backend/search/free.py）
#   bocha —— 付费博查 API，需 BOCHA_API_KEY
#   baidu —— baidu_serp_api 反代
#   exa   —— Exa MCP（有 key 时额度更高，无 key 走公开 MCP）
# 可用环境变量 DEFAULT_SEARCH_PROVIDER 覆盖（本环境需经 WSLENV 透传）
DEFAULT_SEARCH_PROVIDER: str = os.getenv("DEFAULT_SEARCH_PROVIDER", "free")

# 搜索自动回退开关："1" 时 bocha 认证/额度/不可用自动切百度反代（backend/search/fallback.py）
SEARCH_AUTO_FALLBACK: str = os.getenv("SEARCH_AUTO_FALLBACK", "1")

# ── 免费多引擎搜索（provider="free"，无需 API key）────────────────────────
# 引擎优先级：从左到右依次尝试，任一引擎返回结果即止；全部失败才返回空。
# 可选引擎: bing | anysearch | exa-mcp | ddg | ddg-lite | searxng
#   国内网络实测: bing ✅ / anysearch ✅ / exa-mcp ✅ / ddg ❌ / searxng ❌（被墙）
#   海外网络: ddg / searxng 可用，可作为 bing 的补充。按部署网络环境用环境变量调整。
FREE_SEARCH_ENGINES: List[str] = [
    e.strip()
    for e in os.getenv("FREE_SEARCH_ENGINES", "bing,anysearch,exa-mcp,ddg,searxng").split(",")
    if e.strip()
]
FREE_SEARCH_TIMEOUT: int = int(os.getenv("FREE_SEARCH_TIMEOUT", "15"))       # 单请求超时（秒）
FREE_SEARCH_CONCURRENCY: int = int(os.getenv("FREE_SEARCH_CONCURRENCY", "5"))
FREE_SEARCH_RATE_LIMIT: float = float(os.getenv("FREE_SEARCH_RATE_LIMIT", "1.0"))  # QPS，防风控
FREE_SEARCH_POOL_SIZE: int = int(os.getenv("FREE_SEARCH_POOL_SIZE", "20"))   # 候选池下限（截断交给 select_top）
# Bing 最多抓几页 SERP。实测 cn.bing.com 对无 cookie 的 GET 请求忽略 first/count，
# 第 2 页与第 1 页完全相同，因此默认 1 页（多抓只是白等一次请求 + 抬高被风控概率）。
FREE_SEARCH_BING_PAGES: int = int(os.getenv("FREE_SEARCH_BING_PAGES", "1"))
BING_MARKET: str = os.getenv("BING_MARKET", "zh-CN")                        # Bing 市场（中文优先）
FREE_SEARCH_USER_AGENT: str = os.getenv(
    "FREE_SEARCH_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
)

# AnySearch（免费匿名额度；可选 key 仅用于提额，401/403 时自动忽略该 key）
ANYSEARCH_API_URL: str = os.getenv("ANYSEARCH_API_URL", "https://api.anysearch.com/v1/search")
ANYSEARCH_API_KEY: str = os.getenv("ANYSEARCH_API_KEY", "")

# SearXNG 公共实例（多实例自动切换；format=json 需实例开启 API）
SEARXNG_INSTANCES: List[str] = [
    e.strip()
    for e in os.getenv(
        "SEARXNG_INSTANCES",
        "https://opnxng.com,https://priv.au,https://searx.be,"
        "https://searx.tiekoetter.com,https://search.inetol.net,https://paulgo.io",
    ).split(",")
    if e.strip()
]

# 博查 (Bocha) API（provider="bocha" 时才需要）
BOCHA_API_URL: str = os.getenv("BOCHA_API_URL", "https://api.bocha.cn/v1/web-search")
BOCHA_API_KEY: str = os.getenv("BOCHA_API_KEY", "")
BOCHA_MAX_CONCURRENT: int = int(os.getenv("BOCHA_CONCURRENCY", "5"))
BOCHA_TIMEOUT: int = 30               # HTTP 请求超时（秒）

# 百度搜索（provider="baidu" 时才需要）
BAIDU_MAX_CONCURRENT: int = 3
BAIDU_RATE_LIMIT: float = 1.0  # QPS
BAIDU_RETRY_LIMIT: int = 2

# Exa 搜索
EXA_BASE_DELAY: float = 1.0
EXA_TIMEOUT: int = 30                # HTTP 请求超时（秒）
EXA_MAX_RETRIES: int = 3


# ═══════════════════════════════════════════════════════════════════
# 流水线 (Pipeline) 配置
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PipelineDefaults:
    """流水线运行时默认参数。"""
    max_sources: int = 2           # 单次搜索最大来源数（5→2：一次卡牌 2 个来源即可，大幅降低 LLM 排队压力）
    max_topics: int = 5             # 最大联想关键词数
    max_explore_depth: int = 1     # 最大递归探索深度
    explore_concurrency: int = 2   # 探索阶段并发数
    expand_max_sources: int = 2    # 扩展搜索最大来源数（5→2：同 max_sources，控制单卡输入量与耗时）
    expand_max_topics: int = 5     # 扩展搜索最大关键词数
    re_search_threshold: int = 3   # 有效来源不足时重新搜索的阈值
    re_search_max_results: int = 5 # 重新搜索的最大结果数
    content_processor_concurrency: int = 7  # 内容处理并发数
    vector_search_limit: int = 10  # 语义搜索默认返回数
    vector_search_threshold: float = 0.7  # 语义搜索距离阈值


PIPELINE_DEFAULTS = PipelineDefaults()

# 流水线超时（秒）
PIPELINE_TIMEOUT_QUEUE: int = 10       # 事件队列等待超时
PIPELINE_TIMEOUT_FETCH: int = 40       # 单次抓取超时（拉长以容纳 crawl4ai 慢路径 20s + 前两级）
PIPELINE_TIMEOUT_SUMMARIZE: int = 180   # 单次 AI 摘要超时（长摘要输入全量 + 输出 1500-3000 字，实测并发下可达 2.5 分钟）
PIPELINE_TIMEOUT_SYNC: int = 30        # 同步桥接超时 (_run_async)


# ═══════════════════════════════════════════════════════════════════
# 抓取 (Scraper) 配置
# ═══════════════════════════════════════════════════════════════════

SCRAPER_TIMEOUT: int = int(os.getenv("SCRAPER_TIMEOUT", "10"))  # HTTP 请求超时（秒）
SCRAPER_RATE_LIMIT: float = 1.0      # 请求间隔（秒）
SCRAPER_RESPECT_ROBOTS: bool = False # 是否遵守 robots.txt

# 域名质量追踪
DOMAIN_QUALITY_CONSECUTIVE_FAIL_LIMIT: int = 3     # 连续失败 N 次触发熔断
DOMAIN_QUALITY_BLOCKED_SECONDS: float = 30 * 24 * 3600  # 黑名单30天自动解除
DOMAIN_QUALITY_MIN_SAMPLES: int = 3                 # 评分所需最小样本数
DOMAIN_QUALITY_TOP_URLS: int = 5                    # URL 优先级筛选取 top N


# ═══════════════════════════════════════════════════════════════════
# 存储 (Storage) 配置
# ═══════════════════════════════════════════════════════════════════

# 全局数据库路径（相对于项目根目录）
DATABASE_PATH: str = os.getenv("KNOWLEDGEDIVER_DB", "data/knowledgediver.db")

# 默认会话 ID——所有路由/工厂/存储层的 session_id 参数默认值
DEFAULT_SESSION_ID: str = "default"


# ═══════════════════════════════════════════════════════════════════
# 服务 (Services) 配置
# ═══════════════════════════════════════════════════════════════════

# 并发任务限制
MAX_CONCURRENT_TASKS: int = 5       # 每用户最大并发搜索任务数

# Rate Limiting
RATE_LIMIT_DEFAULT: str = os.getenv("RATE_LIMIT_DEFAULT", "30/minute")
RATE_LIMIT_AUTH: str = os.getenv("RATE_LIMIT_AUTH", "5/minute")

# CORS
CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

# JWT
JWT_SECRET: str = os.getenv("JWT_SECRET", "")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable is not set. Please set it in your .env file or system environment.")
JWT_ALGORITHM: str = "HS256"
JWT_EXPIRATION_HOURS: int = int(os.getenv("JWT_EXPIRATION_HOURS", "4"))

# 头像
AVATAR_MAX_SIZE: int = 5 * 1024 * 1024  # 5MB
AVATAR_ALLOWED_EXTENSIONS: List[str] = field(default_factory=lambda: [".png", ".jpg", ".jpeg", ".gif", ".webp"])

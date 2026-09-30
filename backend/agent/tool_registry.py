"""Agent 工具注册表 —— 工具 schema / 权限层级 / 审计的唯一来源。

为什么要拆（T13 tech_debt D4）
==============================
backend/agent/tools.py 原先 1279 行，单个 ToolExecutor 同时承担 schema 声明、执行分支、
决策规则与阈值四件事，任何新工具/新策略都要改这个类。本模块先把"工具是什么"
（schema + 层级 + 权限元数据）独立出来，作为两个热点方向的前置：
  * MCP 网关（T13 extension_points 建议①）：只需遍历本注册表即可导出只读工具；
  * 工具权限分级（T12 热点方向 7 · P0 安全）。

权限层级（T12）
==============
  read      只读：不改知识库、不消耗 AI 调用
  prescribe 处方：只读，但消耗一次 AI 调用（LLM 盘点）
  write     写：联网、生成/修改卡片内容或链接

注意：write 层级与"搜索预算熔断"（ToolExecutor._WRITE_TOOLS）不是同一概念——
link_card 属 write 层（改动知识结构），但不消耗搜索预算，故不在搜索熔断名单里。

开关（默认全部保持旧行为，见 backend/config.py）
==============================================
  KD_TOOL_PERMISSION = legacy（默认，不拦截）| no_write（禁写层）| readonly（只允许读层）
  KD_TOOL_AUDIT      = 0（默认，零副作用）| 1（工具调用审计日志）
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

from backend import config as _config
from backend.quality.thresholds import (
    TOOL_PERMISSION_LEGACY,
    TOOL_PERMISSION_MODES,
    TOOL_PERMISSION_NO_WRITE,
    TOOL_PERMISSION_READONLY,
)

logger = logging.getLogger(__name__)

TIER_READ = "read"
TIER_PRESCRIBE = "prescribe"
TIER_WRITE = "write"
TOOL_TIERS = (TIER_READ, TIER_PRESCRIBE, TIER_WRITE)

TOOL_PERMISSION_ENV = "KD_TOOL_PERMISSION"
TOOL_AUDIT_ENV = "KD_TOOL_AUDIT"
DEFAULT_AUDIT_PATH = "data/agent_tool_audit.jsonl"

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_similar_cards",
            "description": "在本会话已有卡片中语义搜索。这是查找已有知识的第一步，应始终优先调用。返回卡片标题、ID 与语义相似度 score（0~1，越高越匹配）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词或问题"},
                    "limit": {"type": "integer", "description": "最多返回条数", "default": 5},
                    "threshold": {"type": "number", "description": "相似度门槛（0~1），低于此值的卡片不返回，默认 0.35。这是召回门槛，不是最终相关判断：返回后仍需 get_card_info 读内容确认", "default": 0.35},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_card_info",
            "description": "读取指定卡片的完整内容。在 search_similar_cards 命中后调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string", "description": "卡片 ID"},
                },
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_linked_cards",
            "description": "获取与指定卡片关联的其他卡片 ID 列表。用于扩展上下文，了解相关主题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string", "description": "卡片 ID"},
                },
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_cards",
            "description": "列出本会话所有卡片的标题和 ID。在没有找到需要的卡牌或用户问「有哪些卡片」「知识库概览」时使用。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_card_tree",
            "description": "查看当前知识库的完整树形结构（父卡→子卡的层级文本视图，含子卡数量和内容长度）。在决定新卡挂载位置、评估哪一层缺内容、选择 expand_from_card 的源卡时使用。这是结构视图，不是语义相似度。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_by_keyword",
            "description": "联网搜索关键词并自动生成知识卡片。仅当本地知识库确实无相关内容时使用。若搜索主题与知识库已有卡片相关/近似（是某卡的子主题或同领域主题），必须先用 search_similar_cards 找到最相关的卡片，把其 ID 传入 source_card_id，新卡片将自动与该卡建立双向链接；若搜索主题与现有内容确实独立、属于全新领域，可不传 source_card_id——但生成新卡后若发现与已有卡相关，应立即用 link_card 建立链接（link_card 支持直接传卡片标题）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词——必须是自包含的完整概念名。歧义短词/跨领域多义词必须自带领域限定（如「Zero（合金装备）」「毒蛇（合金装备V）」），禁止搜索无领域限定的裸短词（如单独的「Zero」「毒蛇」——联网会返回区块链/其他游戏等同名异义内容）。短词（≤4字且无括号）联网时后端会自动拼接知识库根卡标题锚定领域，但长词/限定词不会，请优先自查自包含。"},
                    "max_sources": {"type": "integer", "description": "最大搜索来源数", "default": 2},
                    "source_card_id": {"type": "string", "description": "源卡片 ID（仅当搜索主题与已有卡片相关时传入）：新卡片将作为此卡片的子主题自动建链；主题独立无关时可省略"},
                },
                "required": ["keyword"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "expand_from_card",
            "description": "从指定卡片联想扩展，搜索其子主题并生成新卡片，新卡片会自动链接到源卡片下。这是深入了解某个主题的首选方式：优先用它补充薄弱卡牌的相关内容，而不是反复 search_by_keyword 创建并列卡。card_id 必须是 list_cards / search_similar_cards 返回的真实 ID（直接传卡片标题也能自动匹配，但不保证唯一）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string", "description": "源卡片 ID（来自 list_cards / search_similar_cards）"},
                },
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_card",
            "description": "重新搜索并刷新某张卡牌的内容。这是最后手段，触发条件非常严格：通常不调用。仅当某卡绝对 gap 很高、同时是本会话相对最弱的卡之一、且结构分很低时，才考虑 refresh；绝大多数情况下应优先 expand_from_card。refresh 只替换原卡内容，不产生新卡片。",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string", "description": "要刷新的卡牌 ID"},
                },
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_card_quality",
            "description": "评估当前知识库中所有卡牌的质量，找到最薄弱的卡牌。gap_score 越高表示卡牌质量越差，可能需要上网搜索补充。返回按 gap_score 降序排列的卡牌列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "top_n": {"type": "integer", "description": "返回最薄弱的卡片数量，默认 5"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_knowledge_base",
            "description": "评估知识库整体健康度（纯本地计算）。把全部卡片按语义聚成主题簇，返回：需补强的薄弱簇清单（weak=内容薄弱 / fragmented=碎片化，按优先级排序，含簇内卡 ID 列表）与覆盖不足卡清单（孤立/微簇卡，疑似未覆盖主题）。用于决定优先补充哪个主题域，或回答宽泛任务前了解库的整体覆盖状态。",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan_knowledge_gaps",
            "description": "对薄弱主题簇做 LLM 盘点（消耗一次 AI 调用）。输入薄弱簇 ID，返回该主题域缺失的子主题清单 + 建议搜索关键词，用于决定 search_by_keyword 补什么。通常先调用 assess_knowledge_base 获取薄弱簇 ID，再调用本工具。省略 cluster_id 时自动选补强优先级最高的簇。",
            "parameters": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "integer", "description": "薄弱簇 ID（来自 assess_knowledge_base 返回的 cluster_id），省略时自动选优先级最高的簇"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assess_exploration_need",
            "description": "在调用联网搜索工具（search_by_keyword / expand_from_card / refresh_card）之前调用此工具，评估联网搜索的必要性。返回本地已有相关知识的质量评分和建议操作，避免不必要的联网搜索。需提供 card_id（评估某张卡片是否需要刷新或扩展）或 keyword（评估搜索某个关键词是否本地已有足够内容）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string", "description": "要评估的卡片 ID（用于判断是否需要 expand 或 refresh）"},
                    "keyword": {"type": "string", "description": "要搜索的关键词（用于判断本地是否已有相关内容）"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "link_card",
            "description": "在两个卡片之间建立无向链接（Obsidian 风格：链接是对称的，card_id_a / card_id_b 顺序无关）。如果两张卡片已存在链接关系，此操作无副作用。适用场景：发现两张卡片内容高度相关但尚未链接时（如 search_by_keyword 返回\"新卡片暂未挂载\"时）。可选参数 parent 用于指定树形挂载方向：parent=\"a\" 表示把 card_id_a 设为 card_id_b 的父卡，parent=\"b\" 反之；省略 parent 只建立无向引用链接、不改变树结构。需要把某张卡挂到另一张卡下面时**必须**传 parent。card_id_a / card_id_b 必须是 list_cards / search_similar_cards / get_card_info 返回的真实卡片 ID（直接传卡片标题也能自动匹配，但不保证唯一；标题含括号等歧义时请先 get_card_info 确认）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id_a": {"type": "string", "description": "卡片 A 的 ID 或精确标题"},
                    "card_id_b": {"type": "string", "description": "卡片 B 的 ID 或精确标题"},
                    "parent": {"type": "string", "enum": ["a", "b"], "description": "可选：树形挂载方向。'a' = card_id_a 是 card_id_b 的父卡；'b' = card_id_b 是 card_id_a 的父卡。省略 = 仅无向链接，不改树结构。"},
                },
                "required": ["card_id_a", "card_id_b"],
            },
        },
    },
]

# 全部工具名，用于 LLM 幻觉工具名时的模糊匹配
_TOOL_NAMES = frozenset(s["function"]["name"] for s in TOOL_SCHEMAS)

# 常见 LLM 口误别名：精确匹配优先，避免 get_card 在 get_card_info/get_card_tree 之间产生歧义。
_TOOL_ALIASES = {
    "get_card": "get_card_info",
    "search_keyword": "search_by_keyword",
    "link_cards": "link_card",
    "assess_exploration": "assess_exploration_need",
    "search_similar": "search_similar_cards",
}

# ── 层级映射（新增工具必须在此登记，registry 是唯一来源）──────────────
TOOL_TIER_MAP: dict[str, str] = {
    "search_similar_cards": TIER_READ,
    "get_card_info": TIER_READ,
    "get_linked_cards": TIER_READ,
    "list_cards": TIER_READ,
    "get_card_tree": TIER_READ,
    "assess_card_quality": TIER_READ,
    "assess_knowledge_base": TIER_READ,
    "assess_exploration_need": TIER_READ,
    "plan_knowledge_gaps": TIER_PRESCRIBE,
    "search_by_keyword": TIER_WRITE,
    "expand_from_card": TIER_WRITE,
    "refresh_card": TIER_WRITE,
    "link_card": TIER_WRITE,
}

_DENY_TEMPLATE = (
    "工具 {name} 属于 {tier} 层，当前权限模式 {mode} 已禁用该层，调用被拒绝。"
    "请改用只读工具（list_cards / search_similar_cards / get_card_info / get_card_tree / "
    "assess_card_quality / assess_knowledge_base / assess_exploration_need）完成当前任务；"
    "如确需联网或写库，请让用户显式开启写权限后重试。"
)


def tool_tier(name: str) -> str:
    """工具层级；未登记的工具按 read 处理（保守：不会因未知工具而放宽权限）。"""
    return TOOL_TIER_MAP.get(name, TIER_READ)


def tools_by_tier(tier: str) -> tuple[str, ...]:
    return tuple(sorted(n for n in _TOOL_NAMES if TOOL_TIER_MAP.get(n) == tier))


def schemas_for_tiers(tiers: Iterable[str]) -> list[dict]:
    """按层级过滤 schema（MCP 只读导出 / 上下文裁剪共用）。"""
    allowed = set(tiers)
    return [s for s in TOOL_SCHEMAS if TOOL_TIER_MAP.get(s["function"]["name"]) in allowed]


# ── E4（task-44）：返回值可能嵌入"外部派生文本"的工具 ────────────────────
# 用途：MCP 边界出站包裹（backend/mcp/server.py）与 L1 判定；
# 只有 success=True 的结果会被包裹：错误/拒绝消息是本系统自己的引导语，不包裹。
TOOLS_WITH_UNTRUSTED_RESULT_TEXT: frozenset[str] = frozenset({
    "search_similar_cards", "get_card_info", "get_linked_cards", "list_cards", "get_card_tree",
    "assess_card_quality", "assess_knowledge_base", "assess_exploration_need",
    "plan_knowledge_gaps", "search_by_keyword", "expand_from_card", "refresh_card", "link_card",
})


def returns_untrusted_text(name: str) -> bool:
    """该工具的**成功返回值**是否可能包含外部派生文本（E4 判据）。"""
    return name in TOOLS_WITH_UNTRUSTED_RESULT_TEXT


def _resolve(env_name: str, default: object) -> str:
    raw = os.getenv(env_name)
    if raw is None:
        raw = default
    return str(raw).strip().lower()


def permission_mode() -> str:
    """当前工具权限模式；未知取值一律回退 legacy（默认不拦截，保证兼容）。"""
    mode = _resolve(TOOL_PERMISSION_ENV, getattr(_config, "TOOL_PERMISSION_MODE", TOOL_PERMISSION_LEGACY))
    return mode if mode in TOOL_PERMISSION_MODES else TOOL_PERMISSION_LEGACY


def blocked_tiers(mode: str | None = None) -> frozenset[str]:
    """当前模式禁用的层级集合。legacy -> 空集（零拦截、零开销）。"""
    m = permission_mode() if mode is None else str(mode).strip().lower()
    if m == TOOL_PERMISSION_NO_WRITE:
        return frozenset({TIER_WRITE})
    if m == TOOL_PERMISSION_READONLY:
        return frozenset({TIER_WRITE, TIER_PRESCRIBE})
    return frozenset()


def is_tool_allowed(name: str, mode: str | None = None) -> tuple[bool, str]:
    """返回 (是否允许, 拒绝原因)。legacy 模式下恒为 (True, "")。"""
    tiers = blocked_tiers(mode)
    if not tiers:
        return True, ""
    tier = tool_tier(name)
    if tier not in tiers:
        return True, ""
    m = permission_mode() if mode is None else str(mode).strip().lower()
    return False, _DENY_TEMPLATE.format(name=name, tier=tier, mode=m)


def audit_enabled() -> bool:
    """审计日志开关；默认关闭（关闭时 record_audit 是纯 no-op）。"""
    default = "1" if bool(getattr(_config, "TOOL_AUDIT_ENABLED", False)) else "0"
    return _resolve(TOOL_AUDIT_ENV, default) in ("1", "true", "yes", "on")


def audit_log_path() -> Path:
    return Path(getattr(_config, "TOOL_AUDIT_PATH", DEFAULT_AUDIT_PATH))


def record_audit(event: dict) -> None:
    """追加一条工具调用审计（JSONL）。关闭时零副作用；任何异常只告警不抛出。"""
    if not audit_enabled():
        return
    try:
        rec = {"ts": datetime.utcnow().isoformat(timespec="seconds"), **event}
        logger.info("[ToolAudit] %s", json.dumps(rec, ensure_ascii=False, default=str))
        path = audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # 审计失败绝不能影响工具执行
        logger.warning("[ToolAudit] 写审计失败: %s", e)


__all__ = [
    "TOOL_SCHEMAS",
    "TOOL_TIERS",
    "TOOL_TIER_MAP",
    "TIER_READ",
    "TIER_PRESCRIBE",
    "TIER_WRITE",
    "tool_tier",
    "tools_by_tier",
    "schemas_for_tiers",
    "permission_mode",
    "blocked_tiers",
    "is_tool_allowed",
    "TOOLS_WITH_UNTRUSTED_RESULT_TEXT",
    "audit_enabled",
    "audit_log_path",
    "record_audit",
    "returns_untrusted_text",
]

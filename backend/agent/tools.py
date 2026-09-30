"""Agent 工具定义 — 13 个工具 schema + 执行器。

设计原则：
- 读层工具（只读，不改变知识库）: search_similar_cards, get_card_info, get_linked_cards, list_cards, get_card_tree, assess_card_quality, assess_exploration_need, assess_knowledge_base
- 处方层工具（LLM 盘点，消耗 AI 调用）: plan_knowledge_gaps
- 写层工具（联网搜索，生成新卡片）: search_by_keyword, expand_from_card, refresh_card
- 所有工具通过 PipelineAPI 执行，不直接操作存储
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from backend.ai import OpenAIProvider
from backend.ai.config import load_config
from backend.config import AGENT_CARD_CONTENT_MAX_CHARS
from backend.quality.provider import QualityProvider
from backend.quality.thresholds import (
    GAP_HIGH,
    GAP_LOW,
    LOCAL_SUFFICIENT_PERCENTILE,
    REFRESH_GAP_ABS,
    REFRESH_PERCENTILE_MIN,
    REFRESH_STRUCTURE_MAX,
    SIMILAR_AUTO_LINK,
    SIMILAR_LOCAL_HIT,
    SIMILAR_RECALL_THRESHOLD,
)
from backend.quality.topic_guard import topic_aware_decision
from backend.utils.titles import find_card_by_normalized_title
from .schemas import ToolResult
from .tool_registry import (
    TOOL_SCHEMAS,
    TOOL_TIER_MAP,
    _TOOL_ALIASES,
    _TOOL_NAMES,
    audit_enabled,
    is_tool_allowed,
    record_audit,
    tool_tier,
)
from .tree_cache import TreeSummaryCache
from .untrusted import wrap_untrusted

logger = logging.getLogger(__name__)

# 决策阈值统一来自 backend/quality/thresholds.py（单一来源）。
# 该模块 docstring 记录了绝对档已实测失效：refresh 三条件 0/736、主题饱和绝对档 0/64。
# 此处 import 同名常量仅为向后兼容（外部仍可 from backend.agent.tools import REFRESH_GAP_ABS）。

# ── 决策模式开关（implementation_plan.md P0，默认 legacy = 论文口径不变）────────
# legacy:      现有绝对阈值链，输出逐字段不变（默认，保证向后兼容）。
# topic_guard: 主题饱和守卫（P0-2）——饱和主题不再推荐 expand，改指向 undercovered 代表卡；
#              同时输出可达性审计（P0-1），使"绝对门槛 0 命中"这类硬伤可观测。
# 用环境变量而非 backend/config.py：本次写入范围限定 backend/agent 与 backend/quality，
# config.py 不在范围内；语义与 implementation_plan §6 的 KD_DECISION_MODE 保持一致。
DECISION_MODE_ENV = "KD_DECISION_MODE"
REFRESH_RULE_ENV = "KD_REFRESH_RULE"
VALID_DECISION_MODES = ("legacy", "topic_guard")
VALID_REFRESH_RULES = ("legacy", "percentile")


def _decision_mode() -> str:
    """当前决策模式；未知取值一律回退 legacy（安全默认）。"""
    mode = (os.getenv(DECISION_MODE_ENV) or "legacy").strip().lower()
    return mode if mode in VALID_DECISION_MODES else "legacy"


def _refresh_rule() -> str:
    """refresh 触发规则：legacy=绝对 gap 门槛；percentile=会话内分位数驱动。

    默认 legacy（论文已披露口径）。percentile 修复"绝对门槛在分布漂移下 0 命中"
    （实测 736 卡 0 命中），保留 REFRESH_GAP_ABS 常量但不再作为默认必要条件。
    """
    rule = (os.getenv(REFRESH_RULE_ENV) or "legacy").strip().lower()
    return rule if rule in VALID_REFRESH_RULES else "legacy"


def _reachability_report(scores: list[dict]) -> dict:
    """refresh 三条件的可达性审计（纯函数，仅消费已算好的评分）。

    回应实测硬伤"736 卡中 0 张满足三条件"：把每次决策的阈值可达性变成可观测字段，
    避免"规则在分布漂移下永久失效却无人察觉"。
    """
    n = len(scores)
    if n == 0:
        return {
            "n_cards": 0, "abs_gap_threshold": REFRESH_GAP_ABS,
            "pct_min": REFRESH_PERCENTILE_MIN, "struct_max": REFRESH_STRUCTURE_MAX,
            "n_abs_ok": 0, "n_pct_ok": 0, "n_struct_ok": 0,
            "n_reachable_legacy": 0, "n_reachable_percentile": 0,
            "max_gap": None, "reachable_legacy": False, "reachable_percentile": False,
        }
    abs_ok = [float(s.get("gap_score", 0.0) or 0.0) >= REFRESH_GAP_ABS for s in scores]
    pct_ok = [float(s.get("gap_percentile", 0.0) or 0.0) >= REFRESH_PERCENTILE_MIN for s in scores]
    struct_ok = [float(s.get("structure_score", 1.0) or 0.0) <= REFRESH_STRUCTURE_MAX for s in scores]
    n_legacy = sum(1 for i in range(n) if abs_ok[i] and pct_ok[i] and struct_ok[i])
    n_pct = sum(1 for i in range(n) if pct_ok[i] and struct_ok[i])
    return {
        "n_cards": n, "abs_gap_threshold": REFRESH_GAP_ABS,
        "pct_min": REFRESH_PERCENTILE_MIN, "struct_max": REFRESH_STRUCTURE_MAX,
        "n_abs_ok": sum(abs_ok), "n_pct_ok": sum(pct_ok), "n_struct_ok": sum(struct_ok),
        "n_reachable_legacy": n_legacy, "n_reachable_percentile": n_pct,
        "max_gap": round(max(float(s.get("gap_score", 0.0) or 0.0) for s in scores), 4),
        "reachable_legacy": n_legacy > 0, "reachable_percentile": n_pct > 0,
    }


def _levenshtein(a: str, b: str) -> int:
    """编辑距离，用于工具名模糊匹配。"""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class ToolExecutor:
    # 层级来自注册表（backend/agent/tool_registry.py）——registry 是"工具是什么"的唯一来源。
    # 读层（含处方层）：清零连续写层计数（调用后允许继续联网搜索）
    _READ_TOOLS = frozenset(
        name for name in _TOOL_NAMES
        if TOOL_TIER_MAP.get(name, "read") in ("read", "prescribe")
    )
    # 搜索预算熔断名单：只针对"联网搜索/扩展/刷新"。
    # 注意 link_card 属 write 权限层，但不消耗搜索预算，故意不在本名单（与重构前一致）。
    _WRITE_TOOLS = frozenset({"search_by_keyword", "expand_from_card", "refresh_card"})

    def __init__(self, api, enabled_tools: set[str] | None = None):
        self._api = api
        self._quality = QualityProvider(api.card_store, raw_store=api.raw_store)
        self._enabled_tools = enabled_tools or set(_TOOL_NAMES)
        self._write_streak = 0  # 连续写层工具计数（无读层间隔），≥3 时 search 被拒

    def _invalidate_tree_cache(self) -> None:
        """写层工具成功（卡片/链接已变）→ 失效树摘要增量缓存。"""
        TreeSummaryCache.instance().invalidate(self._api.username, self._api.session_id)

    def prepare_task_id(self, name: str, args: dict) -> str | None:
        """在执行前预先创建 Task，返回 task_id 供 SSE tool_call 携带。"""
        task = None
        if name == "search_by_keyword":
            from backend.task import TaskService
            from backend.models.task import TaskType
            task = TaskService.get_instance().create(
                task_type=TaskType.COLLECT,
                username=self._api.username,
                session_id=self._api.session_id,
                keyword=args.get("keyword", ""),
                params={"max_sources": args.get("max_sources", 2)},
            )
        elif name == "expand_from_card":
            card_id = args.get("card_id", "")
            card = self._api.get_card_info(card_id) if card_id else None
            from backend.task import TaskService
            from backend.models.task import TaskType
            task = TaskService.get_instance().create(
                task_type=TaskType.EXPAND,
                username=self._api.username,
                session_id=self._api.session_id,
                keyword=f"延申: {card.title}" if card else "延申搜索",
                params={"source_card_id": card_id},
            )
        elif name == "refresh_card":
            card_id = args.get("card_id", "")
            card = self._api.get_card_info(card_id) if card_id else None
            from backend.task import TaskService
            from backend.models.task import TaskType
            task = TaskService.get_instance().create(
                task_type=TaskType.REFRESH,
                username=self._api.username,
                session_id=self._api.session_id,
                keyword=f"刷新: {card.title}" if card else "刷新卡片",
                params={"card_id": card_id},
            )
        if task is not None:
            # Agent 持有任务生命周期：前端页面断开不应触发 idle-cancel
            task.agent_owned = True
            return task.task_id
        return None

    @staticmethod
    def _normalize_tool_name(name: str) -> str | None:
        """LLM 幻觉工具名时做模糊匹配（包含匹配 + 编辑距离）。

        唯一命中返回真实工具名，无法确定返回 None（调用方报"未知工具"并列候选）。
        """
        if name in _TOOL_NAMES:
            return name
        if name in _TOOL_ALIASES:
            return _TOOL_ALIASES[name]
        target = re.sub(r"[\s_\-（）()]", "", name.lower())
        norm_names = {n: re.sub(r"[\s_\-]", "", n.lower()) for n in _TOOL_NAMES}
        hits = [n for n, nn in norm_names.items() if target in nn or nn in target]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            # 多个包含命中（如 "link" 命中 link_card 与 get_linked_cards），编辑距离再分
            close = [n for n in hits if _levenshtein(target, norm_names[n]) <= 2]
            return close[0] if len(close) == 1 else None
        # 编辑距离（归一化后比较，如 search_keyword → search_by_keyword 距离 2）
        close = [n for n in _TOOL_NAMES if _levenshtein(target, norm_names[n]) <= 2]
        return close[0] if len(close) == 1 else None

    def _format_tree_text(self, nodes: list, limit: int = 80) -> str:
        """把 get_card_tree() 的嵌套 dict 转成 Agent 友好的层级文本。"""
        lines: list[str] = []
        total = 0

        def walk(items: list, depth: int) -> None:
            nonlocal total
            for node in items:
                card = node.get("card") or {}
                children = node.get("children") or []
                title = card.get("title", "?")
                chars = len(card.get("content") or "")
                marker = f"{len(children)}子/{chars}字"
                lines.append("  " * depth + f"- {title}（{marker}）")
                total += 1
                walk(children, depth + 1)

        walk(nodes, 0)
        if not lines:
            return "知识库为空，还没有卡片。"
        header = f"知识库树形结构（共 {total} 张卡片，缩进表示父子层级）:\n"
        body = "\n".join(lines[:limit])
        if len(lines) > limit:
            body += f"\n…（仅显示前 {limit} 行，剩余 {len(lines) - limit} 个节点）"
        return header + body

    async def execute(self, name: str, args: dict) -> ToolResult:
        """工具执行入口：执行 + 可选审计（审计关闭时纯 no-op，零行为变化）。"""
        result = await self._execute_impl(name, args)
        if audit_enabled():
            record_audit({
                "tool": result.tool,
                "tier": tool_tier(result.tool),
                "success": bool(result.success),
                "blocked": bool((result.data or {}).get("blocked")),
                "reason": (result.data or {}).get("reason"),
                "username": getattr(self._api, "username", None),
                "session_id": getattr(self._api, "session_id", None),
            })
        return result

    async def _execute_impl(self, name: str, args: dict) -> ToolResult:
        try:
            # LLM 输出的 arguments 坏 JSON 时，provider 会放入 _raw_args 原样透传
            raw_args = args.get("_raw_args")
            if raw_args is not None:
                return ToolResult(
                    tool=name, success=False,
                    summary=(f"参数解析失败: LLM 输出的工具参数不是合法 JSON，原文: {str(raw_args)[:200]}。"
                             "请重新调用该工具，参数必须是合法 JSON 对象（键名与字符串值都用双引号）。"),
                )
            # 工具名模糊匹配：LLM 可能拼错工具名（如 search_keyword / get_card）
            real_name = self._normalize_tool_name(name)
            if real_name is None:
                candidates = "、".join(sorted(_TOOL_NAMES))
                return ToolResult(
                    tool=name, success=False,
                    summary=f"未知工具: {name}。可用工具: {candidates}。请从列表中选择正确的工具名重新调用。",
                )
            if real_name != name:
                logger.warning("[Tool] 工具名 '%s' 未匹配，已按近似名 '%s' 执行", name, real_name)
                name = real_name
            if name not in self._enabled_tools:
                return ToolResult(
                    tool=name, success=False,
                    summary=f"当前实验模式已禁用工具 {name}。请使用可用工具完成当前任务。",
                )
            # T12 P0 安全：工具权限分级。legacy 模式（默认）恒允许，零行为变化。
            permitted, deny_reason = is_tool_allowed(name)
            if not permitted:
                return ToolResult(
                    tool=name, success=False,
                    summary=deny_reason,
                    data={"blocked": True, "reason": "permission", "tier": tool_tier(name)},
                )
            if name in self._READ_TOOLS:
                self._write_streak = 0
            elif name == "search_by_keyword":
                # 盲搜熔断只针对 search_by_keyword：expand/refresh 是从明确卡片出发的
                # 定向动作，不应把 search 的预算锁死。
                self._write_streak += 1
                if self._write_streak >= 3:
                    self._write_streak -= 1
                    return ToolResult(
                        tool="search_by_keyword", success=False,
                        summary=("已连续 3 次联网搜索而未检查本地知识库。请先调用 "
                                 "list_cards / search_similar_cards / assess_knowledge_base "
                                 "检查本地已有内容与薄弱点，确认需要补充的主题后再搜索。"),
                        data={"blocked": True, "reason": "write_streak", "write_streak": self._write_streak},
                    )
            if name == "search_similar_cards":
                return await self._search_similar(args)
            elif name == "get_card_info":
                return await self._get_card_info(args)
            elif name == "get_linked_cards":
                return self._get_linked(args)
            elif name == "list_cards":
                return await self._list_cards()
            elif name == "get_card_tree":
                return self._get_card_tree()
            elif name == "search_by_keyword":
                return await self._search_keyword(args)
            elif name == "expand_from_card":
                return await self._expand(args)
            elif name == "refresh_card":
                return await self._refresh_card(args)
            elif name == "assess_card_quality":
                return await self._assess_quality(args)
            elif name == "assess_knowledge_base":
                return await self._assess_knowledge_base(args)
            elif name == "plan_knowledge_gaps":
                return await self._plan_knowledge_gaps(args)
            elif name == "assess_exploration_need":
                return await self._assess_exploration_need(args)
            elif name == "link_card":
                return self._link_card(args)
            else:
                return ToolResult(tool=name, success=False, summary=f"未知工具: {name}")
        except Exception as e:
            logger.error("Tool '%s' failed: %s", name, e)
            return ToolResult(tool=name, success=False, summary=f"执行失败: {e}")

    async def _search_similar(self, args: dict) -> ToolResult:
        query = args.get("query")
        if not query or not str(query).strip():
            return ToolResult(
                tool="search_similar_cards", success=False,
                summary="参数错误：缺少 query（搜索关键词）。请重新调用并传入 query。",
            )
        limit = args.get("limit", 5)
        threshold = args.get("threshold", 0.5)
        results = await self._api.search_similar_cards(query, limit=limit, threshold=threshold)
        if not results:
            return ToolResult(
                tool="search_similar_cards", success=True,
                summary=f"本地知识库中未找到与「{query}」相关的内容。",
                data={"count": 0, "cards": []},
            )
        cards = []
        for r in results:
            enriched = await asyncio.to_thread(self._quality.enrich, r["card"])
            cards.append({
                "id": r["card"].id, "title": r["card"].title, "score": r["score"],
                "quality": enriched.get("quality", {}),
            })
        summary_lines = [f"找到 {len(cards)} 条相关卡片:"]
        for c in cards:
            q = c["quality"]
            extra = ""
            if q:
                extra = f", gap={q.get('gap_score', '?')}"
            summary_lines.append(f"  - [{c['title']}] (id={c['id']}, score={c['score']}{extra})")
        return ToolResult(
            tool="search_similar_cards", success=True,
            summary="\n".join(summary_lines),
            data={"count": len(cards), "cards": cards},
        )

    def _resolve_card_id(self, card_id: str) -> tuple[str | None, str | None]:
        """解析 Agent 传入的 card_id——LLM 可能拿卡片标题当 ID 调用（幻觉，日志实证
        "get_linked_cards: card not found id=杀戮尖塔2"）。

        匹配顺序：真实 ID → 标题精确匹配 → 归一化匹配（去空白/统一括号）→
        唯一包含匹配（标题含关键词或反之）。包含匹配仅当唯一命中时才回退，
        避免歧义时猜错卡。

        Returns:
            (真实 card_id, 回退匹配到的标题)；均为 None 表示不存在。
        """
        if self._api.get_card_info(card_id) is not None:
            return card_id, None
        try:
            cards = self._api.list_cards()
        except Exception as e:
            logger.warning("[Tool] _resolve_card_id list_cards failed: %s", e)
            return None, None
        # 标题精确匹配
        for c in cards:
            if c.title == card_id:
                return c.id, c.title
        # 归一化匹配：去空白、统一全半角括号
        def norm(s: str) -> str:
            s = s.strip()
            s = re.sub(r"[\s\u3000]+", "", s)
            return s.replace("（", "(").replace("）", ")").lower()
        target = norm(card_id)
        exact = [c for c in cards if norm(c.title) == target]
        if len(exact) == 1:
            return exact[0].id, exact[0].title
        if len(exact) > 1:
            return None, None  # 归一化后仍多张，歧义不猜
        # 唯一包含匹配（如标题带括号后缀时关键词命中）
        hits = [c for c in cards if target in norm(c.title) or norm(c.title) in target]
        if len(hits) == 1:
            return hits[0].id, hits[0].title
        return None, None

    def _require(self, args: dict, key: str, tool: str, hint: str = "") -> ToolResult | None:
        """参数安全读取：缺失/空值时返回点名报错（供 LLM 纠正），不抛 KeyError。"""
        v = args.get(key)
        if v is None or (isinstance(v, str) and not v.strip()):
            hint = f"。{hint}" if hint else ""
            return ToolResult(
                tool=tool, success=False,
                summary=f"参数错误：缺少 {key}{hint}。请重新调用并传入 {key}。",
            )
        return None

    async def _get_card_info(self, args: dict) -> ToolResult:
        missing = self._require(args, "card_id", "get_card_info",
                                 hint="卡片 ID 来自 list_cards / search_similar_cards 的返回，也支持直接传精确标题")
        if missing:
            return missing
        card_id = args["card_id"]
        real_id, fallback_title = self._resolve_card_id(card_id)
        if real_id is None:
            return ToolResult(tool="get_card_info", success=False, summary=f"卡片 {card_id} 不存在（ID 与标题均未匹配）")
        card = self._api.get_card_info(real_id)
        content = card.content or ""
        if len(content) > AGENT_CARD_CONTENT_MAX_CHARS:
            content = content[:AGENT_CARD_CONTENT_MAX_CHARS] + "\n...(内容已截断)"
        # T12 P0 安全：卡片正文源自抓取网页，属不可信内容。KD_UNTRUSTED_WRAP=on 时
        # 加边界+声明，防止 indirect prompt injection；legacy（默认）原样返回。
        content = wrap_untrusted(content, label=f"card:{card.id}")
        enriched = await asyncio.to_thread(self._quality.enrich, card)
        fallback_note = f"（注：card_id '{card_id}' 未匹配，已按标题回退到 id={real_id}）" if fallback_title else ""
        return ToolResult(
            tool="get_card_info", success=True,
            summary=f"卡片「{card.title}」内容:{fallback_note}\n{content}",
            data={
                "id": card.id, "title": card.title,
                "content_length": len(card.content or ""),
                "quality": enriched.get("quality", {}),
            },
        )

    def _get_linked(self, args: dict) -> ToolResult:
        missing = self._require(args, "card_id", "get_linked_cards",
                                 hint="卡片 ID 来自 list_cards / search_similar_cards 的返回，也支持直接传精确标题")
        if missing:
            return missing
        card_id = args["card_id"]
        real_id, fallback_title = self._resolve_card_id(card_id)
        if real_id is None:
            return ToolResult(tool="get_linked_cards", success=False, summary=f"卡片 {card_id} 不存在（ID 与标题均未匹配）")
        linked = self._api.get_linked_cards(real_id)
        if not linked:
            return ToolResult(tool="get_linked_cards", success=True, summary="该卡片没有关联卡片")
        fallback_note = f"（已按标题回退: {fallback_title}）" if fallback_title else ""
        return ToolResult(
            tool="get_linked_cards", success=True,
            summary=f"关联卡片 ID{fallback_note}: {', '.join(linked)}",
            data={"linked_ids": linked},
        )

    async def _list_cards(self) -> ToolResult:
        cards = self._api.list_cards()
        if not cards:
            return ToolResult(tool="list_cards", success=True, summary="知识库为空，还没有卡片")
        enriched = await asyncio.to_thread(self._quality.enrich_all, cards)
        summaries = [f"{i+1}. [{c.title}] (id={c.id})" for i, c in enumerate(cards[:50])]
        return ToolResult(
            tool="list_cards", success=True,
            summary=f"知识库共 {len(cards)} 张卡片:\n" + "\n".join(summaries),
            data={
                "total": len(cards),
                "cards": [
                    {"id": e["id"], "title": e["title"], "quality": e.get("quality", {})}
                    for e in enriched[:50]
                ],
            },
        )

    def _get_card_tree(self) -> ToolResult:
        """读层工具：完整树形结构视图，帮助 Agent 决定挂载/扩展位置。"""
        try:
            tree = self._api.get_card_tree()
        except Exception as e:
            logger.warning("[Tool] get_card_tree failed: %s", e)
            return ToolResult(tool="get_card_tree", success=False, summary=f"获取知识树失败: {e}")
        text = self._format_tree_text(tree)
        return ToolResult(tool="get_card_tree", success=True, summary=text, data={"tree": tree})

    async def _search_keyword(self, args: dict) -> ToolResult:
        keyword = (args.get("keyword") or "").strip()
        if not keyword:
            return ToolResult(
                tool="search_by_keyword", success=False,
                summary="参数错误：缺少 keyword（要搜索的独立概念名）。请重新调用并传入 keyword，例如 keyword=\"提示词工程\"。",
            )
        max_sources = args.get("max_sources", 2)
        source_card_id = args.get("source_card_id")
        # 校验 source_card_id 存在；LLM 可能幻觉出不存在的 ID（如 "root"）或传标题，
        # 无效时回退到自动找最相似卡建立链接，而不是报错中断
        if source_card_id:
            resolved, _fb = self._resolve_card_id(source_card_id)
            if resolved is None:
                logger.warning(
                    "[Tool] search_by_keyword: source_card_id %r 不存在，回退自动链接",
                    source_card_id,
                )
                source_card_id = None
            else:
                source_card_id = resolved
        # 自动挂载：仅当语义相似度 ≥0.7（高置信，真正同主题）才自动建链。
        # 阈值 0.5 + 根卡兜底会让所有子主题都挂到根卡——根卡内容大而全，
        # 与任何子主题 query 的相似度天然最高（日志实证：蜥蜴人/矮人/种族特性/
        # 技能学派全部链到根卡，知识库退化为 1 根 + N 并列子卡）。
        # 低于 0.7 的挂载层级是结构决策，交给 Agent 用 link_card 完成
        # （它能看到树形注入的层级现状；link_card 支持直接传标题，见下方候选建议）。
        auto_linked_title = None
        similar = []
        if not source_card_id:
            # 低阈值召回本地候选：既用于本地命中检查，也用于未挂载时的候选父卡建议。
            # 召回层从宽，最终判断交给 Agent 读 get_card_info 后完成。
            similar = await self._api.search_similar_cards(
                keyword, limit=5, threshold=SIMILAR_RECALL_THRESHOLD,
            )

        # 标题预检（方案 B）：归一化精确同名 → 不联网。比向量相似度（需 encode）
        # 更便宜、更确定——「PID控制」vs「PID 控制」这类变体只有精确归一化能拦。
        existing = find_card_by_normalized_title(
            self._api.list_cards(), keyword, exclude_id=source_card_id,
        )
        if existing:
            return ToolResult(
                tool="search_by_keyword", success=False,
                summary=(f"本地知识库已有同名卡片「{existing.title}」。"
                         f"请先用 get_card_info 查看该卡片确认信息覆盖情况；"
                         f"如需补充细节，优先对该卡片调用 expand_from_card 扩展子主题；"
                         f"如需更新内容用 refresh_card，不要重复搜索相同主题。"),
                data={"local_hit": True, "blocked": True, "exact_title": True,
                      "card_id": existing.id, "title": existing.title},
            )

        # 本地预检拦截：只把向量分数达到 SIMILAR_LOCAL_HIT 的候选视为“强命中”。
        # 阈值从 0.80 放宽到 0.65，让 Agent 更愿意读本地卡；但向量只是召回层，
        # 返回后必须由 Agent 读 get_card_info 做最终判断，而不是替 Agent 下结论。
        strong = [r for r in similar if r.get("score", 0) >= SIMILAR_LOCAL_HIT]
        if strong:
            top = strong[0]
            card = top["card"]
            enriched = await asyncio.to_thread(self._quality.enrich, card)
            q = enriched.get("quality", {})
            return ToolResult(
                tool="search_by_keyword", success=False,
                summary=(f"本地知识库已有与「{keyword}」相近的卡片「{card.title}」"
                         f"(向量相似度 {top.get('score', 0):.2f}, gap={q.get('gap_score', '?')})。"
                         f"请先 get_card_info 读取该卡内容，确认是否已经覆盖；"
                         f"如果内容足够就直接使用本地卡，如果只是部分相关可 expand_from_card 补子主题，"
                         f"确认本地确实不覆盖后再重新搜索。"),
                data={"local_hit": True, "blocked": True, "card_id": card.id,
                      "title": card.title, "score": top.get("score", 0),
                      "local_candidates": [
                          {"card_id": r["card"].id, "title": r["card"].title, "score": r.get("score", 0)}
                          for r in strong[:3]
                      ]},
            )
        high_conf = [r for r in similar if r.get("score", 0) >= SIMILAR_AUTO_LINK]
        if high_conf:
            source_card_id = high_conf[0]["card"].id
            auto_linked_title = high_conf[0]["card"].title
        # 不再根卡兜底——挂载层级由 Agent 用 link_card 决策，避免全部平铺到根
        task_id_pre = args.get("_task_id")
        from backend.task import TaskService
        task = TaskService.get_instance().get(task_id_pre) if task_id_pre else None
        cards, task_id = await self._api.search_by_keyword_with_task(
            keyword, max_sources=max_sources, source_card_id=source_card_id,
            external_task=task, explore=False,
        )
        if not cards:
            err = task.error if task else None
            summary = f"搜索「{keyword}」完成，但未能生成有效卡片。"
            if err:
                summary += f" 原因: {err}"
            return ToolResult(
                tool="search_by_keyword", success=False,
                summary=summary,
                data={"cards_count": 0},
            )
        titles = [c.title for c in cards]
        await asyncio.to_thread(self._quality.refresh)
        self._invalidate_tree_cache()
        if auto_linked_title:
            link_note = f"，已自动链接到「{auto_linked_title}」"
        else:
            # 挂载层级交给 Agent 决策：给出候选父卡建议（标题形式，link_card 支持标题回退），
            # 避免 Agent 从 list_cards 大海捞针或干脆不挂载造成平铺
            cand = [r for r in similar if r.get("score", 0) >= SIMILAR_RECALL_THRESHOLD]
            if cand:
                cand_desc = "、".join(f"「{r['card'].title}」(相似度 {r.get('score', 0):.2f})" for r in cand[:3])
                link_note = (f"。新卡片暂未挂载——如与已有卡相关，请用 link_card 建立链接，"
                             f"候选父卡: {cand_desc}（link_card 支持直接传卡片标题，无需查 ID）")
            else:
                link_note = "。新卡片暂未挂载——如应挂到某张已有卡下，请用 link_card 建立链接（先 get_card_info 确认合适的父卡）"
        return ToolResult(
            tool="search_by_keyword", success=True,
            summary=f"搜索「{keyword}」完成，生成 {len(cards)} 张新卡片: {', '.join(titles)}{link_note}",
            data={
                "cards_count": len(cards), "titles": titles,
                "task_id": task_id, "task_type": "collect", "keyword": keyword,
                "source_card_id": source_card_id,
            },
        )

    async def _expand(self, args: dict) -> ToolResult:
        missing = self._require(args, "card_id", "expand_from_card",
                                 hint="卡片 ID 来自 list_cards / search_similar_cards 的返回，也支持直接传精确标题")
        if missing:
            return missing
        card_id = args["card_id"]
        real_id, fallback_title = self._resolve_card_id(card_id)
        if real_id is None:
            return ToolResult(
                tool="expand_from_card", success=False,
                summary=f"卡片 {card_id} 不存在（ID 与标题均未匹配）。请先用 list_cards / search_similar_cards 获取真实卡片 ID 或精确标题后重试。",
            )
        card = self._api.get_card_info(real_id)
        task_id_pre = args.get("_task_id")
        from backend.task import TaskService
        task = TaskService.get_instance().get(task_id_pre) if task_id_pre else None
        cards, task_id = await self._api.expand_from_card_with_task(
            real_id, max_topics=6, external_task=task,
        )
        if not cards:
            err = task.error if task else None
            summary = f"从「{card.title}」联想扩展完成，但未能生成新卡片。"
            if err:
                summary += f" 原因: {err}"
            return ToolResult(
                tool="expand_from_card", success=False,
                summary=summary,
                data={"cards_count": 0},
            )
        titles = [c.title for c in cards]
        await asyncio.to_thread(self._quality.refresh)
        self._invalidate_tree_cache()
        fallback_note = f"（注: card_id '{card_id}' 未匹配，已按标题回退到 id={real_id}）" if fallback_title else ""
        return ToolResult(
            tool="expand_from_card", success=True,
            summary=f"从「{card.title}」联想扩展完成{fallback_note}，生成 {len(cards)} 张新卡片: {', '.join(titles)}（新卡已自动链接到源卡）",
            data={"cards_count": len(cards), "titles": titles, "task_id": task_id, "task_type": "expand", "keyword": card.title},
        )

    async def _refresh_card(self, args: dict) -> ToolResult:
        missing = self._require(args, "card_id", "refresh_card",
                                 hint="卡片 ID 来自 list_cards / search_similar_cards 的返回，也支持直接传精确标题")
        if missing:
            return missing
        card_id = args["card_id"]
        real_id, fallback_title = self._resolve_card_id(card_id)
        if real_id is None:
            return ToolResult(
                tool="refresh_card", success=False,
                summary=f"卡片 {card_id} 不存在（ID 与标题均未匹配）。请先用 list_cards / search_similar_cards 获取真实卡片 ID 或精确标题后重试。",
            )
        original = self._api.get_card_info(real_id)

        task_id_pre = args.get("_task_id")
        from backend.task import TaskService
        task = TaskService.get_instance().get(task_id_pre) if task_id_pre else None
        # persist=False: 仅生成候选内容用于更新原卡，不产生新卡片
        new_cards, task_id = await self._api.search_by_keyword_with_task(
            original.title, max_sources=2, external_task=task, persist=False, explore=False,
        )
        await asyncio.to_thread(self._quality.refresh)
        if not new_cards:
            err = task.error if task else None
            summary = f"重新搜索「{original.title}」未生成有效内容，原卡保持不变"
            if err:
                summary += f"（原因: {err}）"
            return ToolResult(
                tool="refresh_card", success=False,
                summary=summary,
                data={"card_id": card_id, "task_id": task_id},
            )

        best = max(new_cards, key=lambda c: (len(c.content or ""), len(c.sources or [])))
        old_len = len(original.content or "")

        self._api.update_card(
            card_id=card_id,
            content=best.content,
            metadata=best.metadata,
            sources=best.sources,
        )

        self._quality.invalidate_card(card_id)
        await asyncio.to_thread(self._quality.refresh)
        self._invalidate_tree_cache()

        return ToolResult(
            tool="refresh_card", success=True,
            summary=(
                f"已刷新「{original.title}」: "
                f"内容从 {old_len} 字更新至 {len(best.content or '')} 字, "
                f"新来源 {len(best.sources or [])} 个"
            ),
            data={
                "card_id": card_id,
                "old_content_length": old_len,
                "new_content_length": len(best.content or ""),
                "generated_cards": len(new_cards),
                "task_id": task_id,
                "task_type": "refresh",
                "keyword": original.title,
            },
        )

    async def _assess_exploration_need(self, args: dict) -> ToolResult:
        card_id = args.get("card_id")
        keyword = args.get("keyword")
        if not card_id and not keyword:
            return ToolResult(
                tool="assess_exploration_need", success=False,
                summary="请提供 card_id 或 keyword",
            )

        if card_id:
            # 先解析 ID——LLM 可能拿标题/捏造 ID 调用，解析失败时明确报错引导纠正，
            # 而不是误报"可以直接联网搜索"（此前幻觉 ID 会被诱导成搜索指令）
            real_id, fallback_title = self._resolve_card_id(card_id)
            if real_id is None:
                return ToolResult(
                    tool="assess_exploration_need", success=False,
                    summary=f"卡片 {card_id} 不存在（ID 与标题均未匹配）。请先用 list_cards / search_similar_cards 获取真实卡片 ID 或精确标题后重试。",
                )
            card_id = real_id
            score = await asyncio.to_thread(self._quality.get_score, card_id)
            if score is None:
                title = fallback_title or card_id
                return ToolResult(
                    tool="assess_exploration_need", success=True,
                    summary=f"卡片「{title}」暂无评分数据（可能尚未评估），建议先用 get_card_info 读取内容确认现状，再决定是否 expand_from_card 扩展。",
                    data={"decision": "no_data", "card_id": card_id, "title": title,
                          "recommendation": "先 get_card_info 确认现状，再决定是否扩展"},
                )

            gap = score.get("gap_score", 0.5)
            gap_pct = score.get("gap_percentile", 0.5)
            struct = score.get("structure_score", 0.5)
            sem = score.get("semantic_score", 0.5)
            graph = score.get("graph_score", 0.5)
            conf = score.get("confidence_score", 0.5)
            title = score.get("title", card_id)

            # 簇级信号：该卡所属主题域的覆盖状态（单卡质量好 ≠ 域覆盖完整）
            cluster_status = None
            cluster_size = None
            report = await asyncio.to_thread(self._quality.get_cluster_report)
            for c in report.get("clusters", []):
                if card_id in c["card_ids"]:
                    cluster_status = c["status"]
                    cluster_size = c["size"]
                    break
            if cluster_status is None:
                for u in report.get("undercovered", []):
                    if u["card_id"] == card_id:
                        cluster_status = "undercovered"
                        cluster_size = u["cluster_size"]
                        break

            # 主题饱和守卫（P0-2，默认关闭）：在旧判定链之前做一次确定性检查。
            # 主题饱和 ⇒ 本主题 expand 期望覆盖增益≈0 ⇒ 不推荐 expand 本卡，改向 undercovered。
            # 任何异常都回退 legacy（guard=None），保证不因新逻辑打断既有工具。
            mode = _decision_mode()
            guard = None
            if mode == "topic_guard":
                try:
                    guard = topic_aware_decision(report, card_id)
                except Exception as e:
                    logger.warning("[Tool] topic_guard 决策失败，回退 legacy: %s", e)
                    guard = None

            if _refresh_rule() == "percentile":
                # P0-1：绝对门槛在分布漂移下 0 命中（实测 736 卡），改以会话内分位数为主。
                needs_refresh = (
                    gap_pct >= REFRESH_PERCENTILE_MIN
                    and struct <= REFRESH_STRUCTURE_MAX
                )
            else:
                needs_refresh = (
                    gap >= REFRESH_GAP_ABS
                    and gap_pct >= REFRESH_PERCENTILE_MIN
                    and struct <= REFRESH_STRUCTURE_MAX
                )
            if needs_refresh:
                decision = "needs_refresh"
                reason = (
                    f"gap={gap:.2f}（会话内相对薄弱 P{gap_pct:.0%}）且结构分 {struct:.2f} 极低，"
                    f"属于极少数需要 refresh_card 的情况（最后手段）"
                )
            elif guard is not None and guard["guard_applied"]:
                # 主题已饱和：不再把 expand 推荐指向本卡（卡级 gap 与主题级目标的错配点）
                decision = guard["decision"]
                reason = guard["reason"]
            elif cluster_status == "undercovered":
                decision = "needs_expand"
                reason = f"该卡属覆盖不足主题（微簇仅 {cluster_size} 卡），即使单卡质量尚可也建议 expand_from_card 扩展该域"
            elif cluster_status and cluster_status != "healthy":
                decision = "needs_expand"
                reason = f"该卡所在主题簇状态为 {cluster_status}（{cluster_size} 卡），域整体薄弱，建议 expand_from_card 扩展"
            elif gap < GAP_LOW or gap_pct <= LOCAL_SUFFICIENT_PERCENTILE:
                decision = "local_sufficient"
                reason = (
                    f"gap={gap:.2f}，会话内相对较好（P{gap_pct:.0%}），"
                    f"本地内容足够，优先 get_card_info 直接使用"
                )
            else:
                decision = "needs_expand"
                reason = f"gap={gap:.2f} 处于中间区间，优先用 expand_from_card 扩展关联内容（graph={graph:.2f}）"

            data = {
                "card_id": card_id, "title": title,
                "gap_score": gap, "gap_percentile": gap_pct,
                "structure_score": struct, "semantic_score": sem,
                "graph_score": graph, "confidence_score": conf,
                "cluster_status": cluster_status, "cluster_size": cluster_size,
                "decision": decision, "recommendation": reason,
            }
            if guard is not None:
                # 仅在新模式追加字段：legacy 下 data 与改动前逐字段一致
                data.update({
                    "topic_status": guard["topic_status"],
                    "topic_gap": guard["topic_gap"],
                    "topic_saturated": guard["topic_saturated"],
                    "topic_guard_applied": guard["guard_applied"],
                    "redirect_card_id": guard["redirect_card_id"],
                    "redirect_title": guard["redirect_title"],
                })
            if mode == "topic_guard" or _refresh_rule() == "percentile":
                # P0-1 可达性审计：让"绝对门槛 0 命中"这类失效可被观测
                data["reachability"] = _reachability_report(
                    await asyncio.to_thread(self._quality.get_all_scores)
                )

            return ToolResult(
                tool="assess_exploration_need", success=True,
                summary=f"「{title}」质量评分: gap={gap:.2f}, struct={struct:.2f}, sem={sem:.2f}, graph={graph:.2f}, conf={conf:.2f}。{reason}",
                data=data,
            )

        keyword = keyword.strip()
        similar = await self._api.search_similar_cards(
            keyword, limit=5, threshold=SIMILAR_RECALL_THRESHOLD,
        )
        if not similar:
            return ToolResult(
                tool="assess_exploration_need", success=True,
                summary=f"本地知识库中未找到与「{keyword}」相关的内容，建议使用 search_by_keyword 联网搜索。",
                data={"keyword": keyword, "local_count": 0, "decision": "no_local", "recommendation": "建议联网搜索"},
            )

        best = None
        best_card_id = None
        quality_info = []
        for r in similar:
            c = r["card"]
            s = await asyncio.to_thread(self._quality.get_score, c.id)
            info = {
                "title": c.title,
                "gap_score": s.get("gap_score", 0.5) if s else 0.5,
                "gap_percentile": s.get("gap_percentile", 0.5) if s else 0.5,
                "structure_score": s.get("structure_score", 0.5) if s else 0.5,
                "score": r.get("score", 0),
            }
            quality_info.append(info)
            if best is None or info["gap_score"] < best["gap_score"]:
                best = info
                best_card_id = c.id

        # 主题饱和守卫（P0-2，默认关闭）：关键词落在已饱和主题时，本地已覆盖该主题，
        # 继续联网搜索的期望覆盖增益≈0 —— 与 card_id 分支共用同一套主题级检查。
        mode = _decision_mode()
        guard = None
        if mode == "topic_guard" and best_card_id:
            try:
                report = await asyncio.to_thread(self._quality.get_cluster_report)
                guard = topic_aware_decision(report, best_card_id)
            except Exception as e:
                logger.warning("[Tool] topic_guard(keyword) 决策失败，回退 legacy: %s", e)
                guard = None

        if guard is not None and guard["guard_applied"]:
            # 主题已饱和：本地足够，不联网；确有缺口时改向覆盖不足代表卡
            decision = "local_sufficient"
            rec = guard["reason"]
        elif best is not None and (best["gap_score"] < GAP_LOW or best["gap_percentile"] <= LOCAL_SUFFICIENT_PERCENTILE):
            decision = "local_sufficient"
            rec = f"本地已有相关卡片且质量足够（最佳 gap={best['gap_score']:.2f}, 会话内 P{best['gap_percentile']:.0%}），建议先 get_card_info 阅读现有内容，不需要重复搜索"
        elif (
            best is not None
            and best["gap_score"] >= REFRESH_GAP_ABS
            and best["gap_percentile"] >= REFRESH_PERCENTILE_MIN
            and best["structure_score"] <= REFRESH_STRUCTURE_MAX
        ):
            decision = "needs_refresh"
            rec = f"本地虽有相关卡片但质量极差（最佳 gap={best['gap_score']:.2f}, P{best['gap_percentile']:.0%}, 结构 {best['structure_score']:.2f}），极少见地建议 refresh_card 刷新"
        else:
            decision = "optional"
            rec = f"本地有部分相关内容（最佳 gap={best['gap_score'] if best else 0.5:.2f}），先读本地卡确认覆盖，仍不足再 search_by_keyword"

        data = {
            "keyword": keyword, "local_count": len(similar),
            # 修复潜在崩溃：原实现引用未定义的 best_gap，关键字分支必定抛 NameError
            # （被 execute 捕获后报"执行失败"）——即关键字输入从未真正返回过决策。
            "best_gap": best["gap_score"] if best else 0.5,
            "local_cards": quality_info,
            "decision": decision, "recommendation": rec,
        }
        if guard is not None:
            data.update({
                "best_local_card_id": best_card_id,
                "topic_status": guard["topic_status"],
                "topic_gap": guard["topic_gap"],
                "topic_saturated": guard["topic_saturated"],
                "topic_guard_applied": guard["guard_applied"],
                "redirect_card_id": guard["redirect_card_id"],
                "redirect_title": guard["redirect_title"],
            })
        if mode == "topic_guard" or _refresh_rule() == "percentile":
            data["reachability"] = _reachability_report(
                await asyncio.to_thread(self._quality.get_all_scores)
            )

        return ToolResult(
            tool="assess_exploration_need", success=True,
            summary=f"本地找到 {len(similar)} 条与「{keyword}」相关的卡片。{rec}",
            data=data,
        )

    async def _assess_quality(self, args: dict) -> ToolResult:
        top_n = args.get("top_n", 5)
        all_scores = await asyncio.to_thread(self._quality.get_all_scores)
        if not all_scores:
            return ToolResult(tool="assess_card_quality", success=True, summary="知识库为空，没有卡片可供评估")
        gaps = all_scores[:top_n]
        lines = [f"知识库共 {len(all_scores)} 张卡片，最薄弱的前 {len(gaps)} 张:"]
        for g in gaps:
            lines.append(f"  - [{g['title']}] gap={g['gap_score']:.2f} struct={g['structure_score']:.2f} sem={g['semantic_score']:.2f}")
        return ToolResult(
            tool="assess_card_quality", success=True,
            summary="\n".join(lines),
            data={"weakest_cards": gaps},
        )

    async def _assess_knowledge_base(self, args: dict) -> ToolResult:
        report = await asyncio.to_thread(self._quality.get_cluster_report)
        if not report.get("clusters") and not report.get("undercovered"):
            return ToolResult(
                tool="assess_knowledge_base", success=True,
                summary="知识库卡片过少（<4），暂无簇级评估",
                data={"report": report},
            )

        weak = [c for c in report["clusters"] if c["status"] != "healthy"]
        uc = report["undercovered"]
        lines = [f"知识库共 {report['n_cards']} 张卡片，{report['n_clusters']} 个主题簇，"
                 f"覆盖不足卡 {report['n_undercovered']} 张（簇间 gap 中位数 {report['median_avg_gap']}）"]
        if weak:
            lines.append(f"需补强的簇 {len(weak)} 个（按优先级）:")
            for c in weak[:5]:
                lines.append(
                    f"  - 簇{c['cluster_id']} [{c['status']}] {c['size']}卡 "
                    f"avg_gap={c['avg_gap']:.2f} 缺{'/'.join(k for k, v in c['dims'].items() if v < 0.5) or '内容'} | "
                    f"代表卡: {', '.join(c['titles'][:3])} | 薄弱卡ID: {', '.join(c['card_ids'][:3])}"
                )
        if uc:
            lines.append(f"覆盖不足卡 {len(uc)} 张（疑似未覆盖主题，建议扩展）:")
            for u in uc[:5]:
                lines.append(f"  - [{u['title']}] gap={u['gap_score']:.2f} id={u['card_id']}")
        if not weak and not uc:
            lines.append("整体健康：无薄弱簇、无覆盖不足卡")
        return ToolResult(
            tool="assess_knowledge_base", success=True,
            summary="\n".join(lines),
            data={"report": report},
        )

    async def _plan_knowledge_gaps(self, args: dict) -> ToolResult:
        report = await asyncio.to_thread(self._quality.get_cluster_report)
        clusters = report.get("clusters", [])
        if not clusters:
            return ToolResult(
                tool="plan_knowledge_gaps", success=False,
                summary="知识库无主题簇，请先使用 search_by_keyword 建立卡片",
            )

        cluster_id = args.get("cluster_id")
        if cluster_id is not None:
            target = next((c for c in clusters if c["cluster_id"] == cluster_id), None)
            if target is None:
                return ToolResult(
                    tool="plan_knowledge_gaps", success=False,
                    summary=f"簇 {cluster_id} 不存在，可用 assess_knowledge_base 查看当前簇列表",
                )
        else:
            weak_clusters = [c for c in clusters if c["status"] != "healthy"]
            if not weak_clusters:
                return ToolResult(
                    tool="plan_knowledge_gaps", success=False,
                    summary="知识库无薄弱簇，暂不需要补缺规划。可指定 cluster_id 对指定簇盘点。",
                )
            target = max(weak_clusters, key=lambda c: (c["priority"], -c["size"]))

        card_lines = []
        for cid in target["card_ids"]:
            card = self._api.get_card_info(cid)
            if not card:
                continue
            snippet = (card.content or "").strip().replace("\n", " ")[:300]
            # T12 P0 安全：卡片摘要在进入 LLM 盘点提示词前做不可信内容隔离
            snippet = wrap_untrusted(snippet, label=f"card:{card.id}")
            card_lines.append(f"- {card.title}: {snippet}")
        if not card_lines:
            return ToolResult(
                tool="plan_knowledge_gaps", success=False,
                summary="无法读取簇内卡片内容",
            )

        prompt = (
            "你是知识库管理员。以下是一个主题域内的现有卡片（标题: 内容摘要）：\n\n"
            + "\n".join(card_lines)
            + "\n\n请分析这个主题域还缺少哪些重要子主题。要求：\n"
            "1. 列出 3-8 个缺失或薄弱的子主题\n"
            "2. 每个子主题给出 1 个适合联网搜索的中文关键词\n"
            "3. 输出格式：每行一条「子主题名称 | 搜索关键词」，不要任何多余解释\n"
        )

        try:
            ai_config = load_config()
            provider = OpenAIProvider(ai_config)
            resp = await asyncio.wait_for(provider.generate(prompt), timeout=120.0)
        except asyncio.TimeoutError:
            return ToolResult(
                tool="plan_knowledge_gaps", success=False,
                summary="LLM 盘点超时（120s），请稍后重试",
            )
        except Exception as e:
            logger.error("plan_knowledge_gaps: LLM call failed: %s", e)
            return ToolResult(
                tool="plan_knowledge_gaps", success=False,
                summary=f"LLM 盘点失败: {e}",
            )

        items = []
        for line in resp.strip().splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            topic, kw = [p.strip() for p in line.split("|", 1)]
            items.append({"subtopic": topic, "keyword": kw})
        if not items:
            return ToolResult(
                tool="plan_knowledge_gaps", success=False,
                summary="LLM 未返回有效盘点结果",
                data={"raw": resp},
            )

        domain = target["titles"][0] if target["titles"] else f"簇{target['cluster_id']}"
        lines = [f"「{domain}」域（簇{target['cluster_id']}）缺失子主题 {len(items)} 个:"]
        for it in items:
            lines.append(f"  - {it['subtopic']} → 搜索: {it['keyword']}")
        return ToolResult(
            tool="plan_knowledge_gaps", success=True,
            summary="\n".join(lines),
            data={"cluster_id": target["cluster_id"], "domain": domain, "gaps": items},
        )

    def _link_card(self, args: dict) -> ToolResult:
        missing = self._require(args, "card_id_a", "link_card", hint="卡片 ID 来自 list_cards / search_similar_cards 的返回，也支持直接传精确标题")
        if missing:
            return missing
        missing = self._require(args, "card_id_b", "link_card", hint="卡片 ID 来自 list_cards / search_similar_cards 的返回，也支持直接传精确标题")
        if missing:
            return missing
        card_id_a = args["card_id_a"]
        card_id_b = args["card_id_b"]
        if card_id_a == card_id_b:
            return ToolResult(tool="link_card", success=False, summary="不能将卡片链接到自身")
        # 双 ID 分别解析，失败时定位到具体哪个无效（LLM 常见：一张传 ID、另一张传标题）
        real_a, fb_a = self._resolve_card_id(card_id_a)
        real_b, fb_b = self._resolve_card_id(card_id_b)
        if real_a is None and real_b is None:
            return ToolResult(
                tool="link_card", success=False,
                summary=f"链接失败: 卡片 A「{card_id_a}」与卡片 B「{card_id_b}」都不存在（ID 与标题均未匹配）。请先用 list_cards 查看现有卡片，再传真实 ID 或精确标题。",
            )
        if real_a is None:
            return ToolResult(
                tool="link_card", success=False,
                summary=f"链接失败: 卡片 A「{card_id_a}」不存在（ID 与标题均未匹配），卡片 B「{card_id_b}」正常。请先用 list_cards 查看现有卡片，修正 A 后再重试。",
            )
        if real_b is None:
            return ToolResult(
                tool="link_card", success=False,
                summary=f"链接失败: 卡片 B「{card_id_b}」不存在（ID 与标题均未匹配），卡片 A「{card_id_a}」正常。请先用 list_cards 查看现有卡片，修正 B 后再重试。",
            )
        ok = self._api.link_cards(real_a, real_b, parent=args.get("parent"))
        if not ok:
            return ToolResult(tool="link_card", success=False, summary=f"链接失败: 内部错误（{card_id_a} ↔ {card_id_b}）")
        self._invalidate_tree_cache()
        notes = []
        if fb_a:
            notes.append(f"A 按标题回退到 id={real_a}")
        if fb_b:
            notes.append(f"B 按标题回退到 id={real_b}")
        note = "（" + "；".join(notes) + "）" if notes else ""
        parent = args.get("parent")
        parent_note = {"a": f"，已把 A 设为 B 的父卡", "b": f"，已把 B 设为 A 的父卡"}.get(parent, "，无向链接")
        return ToolResult(
            tool="link_card", success=True,
            summary=f"已在「{card_id_a}」和「{card_id_b}」之间建立双向链接{note}{parent_note}",
            data={"card_id_a": real_a, "card_id_b": real_b, "parent": parent},
        )

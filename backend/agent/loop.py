"""Agent 主循环 — ReAct 模式 + /loop 自主迭代模式。

普通模式 SSE 事件流: tool_call → tool_result → ... → text* → complete
Loop 模式 SSE 事件流: [tool_call → tool_result → text]×N → complete
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from typing import AsyncIterator

from backend.config import AGENT_MAX_TURNS, AGENT_CONTEXT_MAX_MESSAGES, AGENT_TIMEOUT, AGENT_EVAL_MODE, AGENT_EVAL_MAX_CARDS, AGENT_EVAL_MIN_CARDS
from backend.pipeline.api import PipelineAPI
from .context import AgentContext, AgentSession
from .provider import AgentLLM, ToolDecision
from .schemas import ToolCall, ToolResult
from .tool_registry import blocked_tiers, tool_tier
from .tools import ToolExecutor, TOOL_SCHEMAS
from .bridge import loop_bridge
from .tree_cache import TreeSummaryCache

logger = logging.getLogger(__name__)

LOOP_MAX_ITERATIONS = 50
LOOP_TIMEOUT = 1800  # 30 min — pipeline 每轮 30-60s
LOOP_PREFIX = "/loop"

# 实验消融：no_quality 移除质量评估工具和树注入，其余与 full 完全一致。
_EVAL_QUALITY_TOOLS = frozenset({
    "assess_card_quality", "assess_knowledge_base",
    "assess_exploration_need", "plan_knowledge_gaps",
})


def _eval_tool_schemas(mode: str) -> list[dict]:
    mode = (mode or "full").strip().lower()
    if mode == "full":
        return list(TOOL_SCHEMAS)
    if mode == "no_quality":
        return [s for s in TOOL_SCHEMAS if s["function"]["name"] not in _EVAL_QUALITY_TOOLS]
    logger.warning("[AgentLoop] unknown AGENT_EVAL_MODE=%s, fallback full", mode)
    return list(TOOL_SCHEMAS)


def _eval_tree_enabled(mode: str) -> bool:
    return (mode or "full").strip().lower() == "full"

# 单工具执行上限。普通工具与 AGENT_TIMEOUT(300s) 统一；expand 要跑多个子主题的
# 搜索+抓取+LLM 生成，单独给 600s，避免 3 个主题就把 300s 打满被误杀。
TOOL_EXEC_TIMEOUT = 300.0
TOOL_EXEC_TIMEOUT_EXPAND = 600.0
TOOL_HEARTBEAT_INTERVAL = 15.0  # 工具执行期间 SSE 心跳间隔 — 前端 stall 只认"有数据"

LOOP_INSTRUCTIONS = """# Loop 模式行为约定

你正运行在自主迭代模式下，可以连续调用工具改进知识库。

## 对话约定
1. 每轮工具调用后**必须**产出总结文本（2-3 句话）：工具结果返回后，在下一轮回复的开头先用 2-3 句话总结本轮做了什么、质量变化（引用 gap_score）、是否需要继续，再决定是否调用下一个工具；**禁止**连续调用工具而不产出任何总结文本
2. 文本需包含: 本轮做了什么、质量变化（引用 gap_score）、是否需要继续
3. 对话到达停止条件时，输出最终总结文本，**不要**再调用工具
4. 用户中途发送的消息需立即响应——如果用户要求停止，输出总结后**不要**再调用工具
5. **禁止在文本中输出工具调用格式**（如 `<search_by_keyword>`、XML、`🔧 调用工具`）——需要工具时走真正的工具调用；文本只描述已完成的动作

## 停止条件
满足以下任一即可停止:
- 大部分薄弱卡片的 gap_score 已降至 0.35 以下
- 连续两轮改进不明显（gap 下降 <0.05）
- 知识库质量已满足当前搜索需求
- 用户主动要求停止

## 薄弱簇处理（有界补强，防循环）
- assess_knowledge_base 返回 weak / fragmented / undercovered 簇时，**优先**对簇内代表卡或薄弱卡
  做一轮 expand_from_card 补强（expand 的新卡会自动挂载到源卡下），然后复检；
- 补强尝试**有界**，满足以下任一即可停止并说明原因：
  1) 复检后不再有薄弱簇；
  2) 针对同一簇连续两轮 expand 后 gap 无下降（无改善）；
  3) 本轮 expand 未生成新卡片（无内容可补）；
  4) 用户要求停止。
- 禁止对同一张卡或同一个簇无限重复 expand——补强无效时如实汇报，不要空转。
- 卡片挂载必须用 link_card 的 parent 参数指明方向（如 link_card(card_id_a='父卡标题', card_id_b='子卡标题', parent='a')），
  不要留下平铺的孤立根卡。

## 决策节奏
每次决定是否继续之前，先想: 再搜索一轮能带来多大改善？如果收益很小，不如直接总结。"""


def _to_sse(event_type: str, data: dict) -> str:
    payload = {"type": event_type, "data": data}
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


def _build_tree_summary(api: "PipelineAPI") -> str:
    """构建当前知识库树形结构摘要，供 decide 注入。

    作用：让 LLM 每轮"看到"知识图谱的层级现状——新卡片该挂到哪个节点、
    哪一层缺内容，而不是盲搜平铺。动态注入不进 session 历史（不膨胀上下文）。
    """
    try:
        roots = api.get_card_tree()
    except Exception as e:
        logger.warning("[AgentLoop] Tree summary failed: %s", e)
        return ""
    lines: list[str] = []
    total = 0

    def walk(nodes: list, depth: int) -> None:
        nonlocal total
        for node in nodes:
            card = node.get("card") or {}
            children = node.get("children") or []
            title = card.get("title", "?")
            chars = len(card.get("content") or "")
            lines.append("  " * depth + f"- {title}（{len(children)}子/{chars}字）")
            total += 1
            walk(children, depth + 1)

    walk(roots, 0)
    if not lines:
        return ""
    header = f"当前知识库树结构（共 {total} 张卡片，缩进表示层级，括号内为子卡数/正文字符数）:\n"
    body = "\n".join(lines[:60])
    if len(lines) > 60:
        body += f"\n…（仅显示前 60 行，完整结构可调用 get_card_tree 工具）"
    return header + body


def _get_tree_summary(api: "PipelineAPI", username: str, session_id: str) -> str:
    """带缓存的树摘要：写层工具成功 / 新一轮用户消息后失效，读层轮次零查询。"""
    cache = TreeSummaryCache.instance()
    return cache.get(username, session_id, lambda: _build_tree_summary(api))


def _build_decision_messages(
    session: "AgentSession",
    last_user_msg: str | None,
    tree_summary: str = "",
) -> list[dict]:
    """构建 decide() 用的消息列表。

    前缀缓存纪律（对齐 DeepSeek Harness 的 KV-cache 约定）：
    [SYSTEM_PROMPT + 追加式会话历史] 是跨轮稳定的连续前缀；
    树摘要、用户最新指令等动态内容全部合并为一条 user 消息追加到最末尾，
    绝不插在历史中间——这样每轮 decide 都能命中 provider 的前缀缓存。
    """
    messages = session.to_llm_messages()
    dynamic_parts: list[str] = []
    if tree_summary:
        dynamic_parts.append(tree_summary + (
            "\n决策要求：新卡片必须挂到最相关的已有节点下（搜索时传入 source_card_id，"
            "或生成后用 link_card 挂载——link_card 支持直接传卡片标题，无需查 ID），"
            "不得平铺成并列根卡；先看清树中缺什么再决定搜索什么；"
            "优先用 expand_from_card 在已有节点下深入扩展，而非反复 search_by_keyword 创建并列卡。"
        ))
    if last_user_msg:
        dynamic_parts.append(f"用户最新指令: {last_user_msg}\n请优先响应这条指令。如果用户要求停止，立即输出总结文本，不要再调用工具。")
    if dynamic_parts:
        messages.append({"role": "user", "content": "\n\n".join(dynamic_parts)})
    return messages


async def _execute_tools(
    decision_tool_calls: list[ToolCall],
    executor: ToolExecutor,
    session: "AgentContext.Session",
    tools_used: list[str],
    failed_tools: list[str],
    on_failure=None,
    on_result=None,
    reasoning_content: str = "",
) -> AsyncIterator[str]:
    session.add_tool_calls([
        {"id": tc.id, "type": "function",
         "function": {"name": tc.name, "arguments": json.dumps(tc.args, ensure_ascii=False)}}
        for tc in decision_tool_calls
    ], reasoning_content=reasoning_content)
    for tc in decision_tool_calls:
        try:
            task_id = executor.prepare_task_id(tc.name, tc.args)
        except Exception as e:
            logger.warning("[Tool] prepare_task_id failed for %s: %s", tc.name, e)
            task_id = None
        if task_id:
            tc.args["_task_id"] = task_id
        tc_data = {"name": tc.name, "args": {k: v for k, v in tc.args.items() if k != "_task_id"}}
        if task_id:
            tc_data["task_id"] = task_id
        yield _to_sse("tool_call", tc_data)

    async def _safe_execute(tc: ToolCall) -> ToolResult:
        t0 = time.time()
        try:
            tool_timeout = TOOL_EXEC_TIMEOUT_EXPAND if tc.name == "expand_from_card" else TOOL_EXEC_TIMEOUT
            result = await asyncio.wait_for(
                executor.execute(tc.name, tc.args), timeout=tool_timeout,
            )
            logger.info(
                "[Tool] %s 执行完成 %.1fs success=%s — %s",
                tc.name, time.time() - t0, result.success,
                (result.summary or "")[:120],
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("[Tool] %s 执行超时（>%.0fs，实际 %.1fs）", tc.name, tool_timeout, time.time() - t0)
            return ToolResult(tool=tc.name, success=False, summary=f"工具执行超时（>{TOOL_EXEC_TIMEOUT:.0f}s）")
        except asyncio.CancelledError:
            logger.warning("[Tool] %s 执行被取消（%.1fs）", tc.name, time.time() - t0)
            return ToolResult(tool=tc.name, success=False, summary="工具执行被取消")
        except Exception as ex:
            logger.error("Tool '%s' failed after %.1fs: %s", tc.name, time.time() - t0, ex)
            return ToolResult(tool=tc.name, success=False, summary=f"执行失败: {ex}")

    tasks = {
        asyncio.ensure_future(_safe_execute(tc)): i
        for i, tc in enumerate(decision_tool_calls)
    }
    results: list[ToolResult | None] = [None] * len(tasks)
    done_tc_ids: set[str] = set()
    try:
        while tasks:
            done, pending = await asyncio.wait(tasks, timeout=TOOL_HEARTBEAT_INTERVAL)
            for t in done:
                idx = tasks.pop(t)
                r = t.result()
                if isinstance(r, BaseException):
                    r = ToolResult(
                        tool=decision_tool_calls[idx].name, success=False,
                        summary=f"执行异常: {r}",
                    )
                results[idx] = r
                done_tc_ids.add(decision_tool_calls[idx].id)
            if pending:
                yield _to_sse("heartbeat", {"pending": len(pending)})
    except asyncio.CancelledError:
        # 未完成工具补记结果，否则 _sanitize 会删掉整条调用记录，Agent 误以为没调过
        for tc in decision_tool_calls:
            if tc.id not in done_tc_ids:
                session.add_tool_result(tc.id, tc.name, "工具执行被取消")
        raise

    for tc, r in zip(decision_tool_calls, results):
        if r is None:
            r = ToolResult(tool=tc.name, success=False, summary="执行异常: 无结果")
        tools_used.append(tc.name)
        if r.success:
            failed_tools.clear()
        else:
            failed_tools.append(tc.name)
            # 约束反馈（blocked / local_hit）是故意返回的引导，不是失败，不触发纠正
            is_guard = bool(r.data and (r.data.get("blocked") or r.data.get("local_hit")))
            if on_failure and not is_guard:
                on_failure(tc.name, r)
        if on_result:
            on_result(tc.name, r)
        yield _to_sse("tool_result", {
            "tool": r.tool, "success": r.success,
            "summary": r.summary, "data": r.data,
        })
        session.add_tool_result(tc.id, tc.name, r.summary)


def _strip_xml_tool_blocks(text: str) -> str:
    """剥离模型文本里模拟工具调用的 XML 块（如 <search_by_keyword>...</search_by_keyword>）。

    不清理会进入会话历史，下一轮模型会模仿，形成"表演工具调用"的恶性循环。
    覆盖三种形态：裸 XML 块、markdown 代码块包裹的 XML、🔧 调用工具 行。
    """
    names = "|".join(re.escape(s["function"]["name"]) for s in TOOL_SCHEMAS)
    if not names:
        return text
    # 1) XML 块：<name>...</name> 或 <name ... />（含被 ``` 代码块包裹的形式）
    pattern = (
        rf"```(?:xml|python|json)?\s*<({names})[^>]*>.*?</\1>\s*```"
        rf"|<({names})[^>]*>.*?</\2>"
        rf"|<({names})\s*[^>]*/>"
    )
    text = re.sub(pattern, "", text, flags=re.DOTALL)
    # 2) "🔧 正在使用工具/调用工具: xxx" 行（前端消息式调用形态，同样会诱导下一轮模仿）
    text = re.sub(r"^.*🔧[^\n]*工具[^\n]*$", "", text, flags=re.MULTILINE)
    return text.strip()


def _build_correction(tool_name: str, summary: str) -> str:
    """工具失败后生成下一轮决策的纠正引导（动态 system 消息，不进会话历史）。

    按失败类型给针对性修正建议——只回写失败摘要时 LLM 会盲猜重试，
    连续 3 次失败直接熔断停机；中间加一步"怎么改"的引导能把失败转化为学习。
    """
    if tool_name == "plan_knowledge_gaps" or "cluster_id" in summary:
        return ("工具 plan_knowledge_gaps 调用失败。注意：它的参数是 cluster_id（整数，"
                "来自 assess_knowledge_base 返回的 cluster_id），不是 card_id。"
                "请先调用 assess_knowledge_base 获取薄弱簇 ID 后再试。")
    if "未知工具" in summary:
        return "工具名拼写有误。请从工具描述中选择正确的工具名重新调用（不要自创工具名）。"
    if "不存在" in summary or "not found" in summary.lower():
        return ("卡片 ID 无效或卡片不存在（可能传了标题或捏造了 ID）。"
                "请先用 list_cards / search_similar_cards 获取真实卡片 ID 或精确标题，再重试。")
    if "参数" in summary:
        return "工具参数有误。请按工具描述中的参数名与类型重新调用（参数必须是合法 JSON 对象）。"
    return "工具调用失败。请根据失败原因修正后重试；若仍失败，先检查本地知识库现状再决定下一步。"


def _build_mount_hint(tool_name: str, titles: list[str]) -> str:
    """新卡生成后，注入紧接着下一轮决策的挂载检查指令（动态 system 消息，不进历史）。

    取证实证：agent 搜索生成新卡后从不调用 link_card / expand_from_card，
    新卡全部平铺成孤立根卡（"先建根再挂载"的剧本执行了三轮搜索也没挂）。
    长 prompt 里的规则 LLM 不遵守，改为在生卡后的下一轮决策直接下指令。
    """
    t = "、".join(f"「{x}」" for x in titles)
    return (
        f"刚才 {tool_name} 生成了新卡片 {t}。挂载检查（必做，紧接着完成）：\n"
        f"1) 若新卡是某张已有卡片的子主题或同领域概念 → 立即调用 link_card 挂载"
        f"（link_card 支持直接传卡片标题，且必须传 parent 指明父卡，"
        f"如 link_card(card_id_a='父卡标题', card_id_b='新卡标题', parent='a')）；\n"
        f"2) 若新卡应作为新的根卡 → 检查已有卡片中是否有应挂到它下面的，用 link_card 挂载（同样传 parent）；\n"
        f"3) 若新卡确实独立 → 在回复中向用户说明原因。\n"
        f"不要留下平铺的孤立根卡。"
    )


async def _stream_text(
    llm: AgentLLM,
    messages: list[dict],
    session: "AgentContext.Session",
    max_tokens: int | None = None,
) -> AsyncIterator[str]:
    full_text = ""
    async for token in llm.stream(messages, [], max_tokens=max_tokens):
        yield _to_sse("text", {"content": token})
        full_text += token
    if full_text:
        cleaned = _strip_xml_tool_blocks(full_text)
        if cleaned:
            session.add_assistant_message(cleaned)


async def run_agent_loop(
    username: str,
    session_id: str,
    user_message: str,
) -> AsyncIterator[str]:
    is_loop = user_message.strip().startswith(LOOP_PREFIX)
    if is_loop:
        instruction = user_message.strip()[len(LOOP_PREFIX):].strip() or "持续改进知识库质量"

    ctx = AgentContext.get()
    lock = ctx.lock(username, session_id)

    async with lock:
        eval_mode = (AGENT_EVAL_MODE or "full").strip().lower()
        tool_schemas = _eval_tool_schemas(eval_mode)
        # T12 P0 权限分级：非 legacy 模式下先把被禁用层级的工具从 schema 移除，
        # 避免模型反复调用必然被拒的工具；legacy 下 blocked_tiers() 为空集，行为不变。
        blocked = blocked_tiers()
        if blocked:
            tool_schemas = [
                s for s in tool_schemas
                if tool_tier(s["function"]["name"]) not in blocked
            ]
        tree_enabled = _eval_tree_enabled(eval_mode)
        max_cards = max(0, int(AGENT_EVAL_MAX_CARDS or 0))
        min_cards = max(0, int(AGENT_EVAL_MIN_CARDS or 0))
        logger.info("[AgentLoop] eval_mode=%s tools=%d tree_injection=%s max_cards=%s min_cards=%s",
                    eval_mode, len(tool_schemas), tree_enabled, max_cards, min_cards)

        api = PipelineAPI(username=username, session_id=session_id)

        def _n_cards() -> int:
            try:
                return int(api.count_cards() or 0)
            except Exception:
                return 0

        def _schemas_with_card_limit() -> list[dict]:
            """达到目标卡数后，decide 只允许读工具，强制模型检查并收尾。"""
            if max_cards <= 0:
                return tool_schemas
            if _n_cards() < max_cards:
                return tool_schemas
            read_only = [
                s for s in tool_schemas
                if s["function"]["name"] not in ("search_by_keyword", "expand_from_card", "refresh_card")
            ]
            return read_only

        if eval_mode == "full":
            executor = ToolExecutor(api)
        else:
            executor = ToolExecutor(api, enabled_tools={t["function"]["name"] for t in tool_schemas})
        llm = AgentLLM()
        session = ctx.get_or_create(username, session_id)
        session.add_user_message(user_message)
        # 新一轮对话开始：外部（收集器页面/文档上传）可能已改过卡片，树摘要缓存失效。
        TreeSummaryCache.instance().invalidate(username, session_id)

        tools_used: list[str] = []
        failed_tools: list[str] = []  # 连续失败的工具名（成功时清空），≥3 次熔断止损
        needs_text_response = False
        start_time = datetime.utcnow()
        key = ctx._key(username, session_id)
        _last_user_msg: str | None = None
        _correction_hint: str | None = None  # 工具失败后的纠正引导（动态注入，不进历史）
        _mount_hint: str | None = None       # 新卡生成后的挂载检查指令（一次性注入）

        def _decide_messages() -> list[dict]:
            """decide 消息 = 会话历史 + 动态上下文（树摘要/纠正/挂载/最新指令，均不持久化）。

            动态上下文全部作为 user 消息追加在最末尾，保持历史前缀跨轮稳定
            （provider 前缀缓存友好）；树摘要走增量缓存，读层轮次零 DB 查询。
            """
            nonlocal _mount_hint
            tree_summary = _get_tree_summary(api, username, session_id) if tree_enabled else ""
            msgs = _build_decision_messages(session, _last_user_msg, tree_summary)
            extras: list[str] = []
            if _correction_hint:
                extras.append(_correction_hint)
            if _mount_hint:
                extras.append(_mount_hint)
                _mount_hint = None  # 一次性：只影响紧接着的下一轮决策
            if max_cards > 0:
                n_cards = _n_cards()
                if n_cards >= max_cards:
                    extras.append(
                        f"当前知识库已有 {n_cards} 张卡片，达到本月实验上限 {max_cards}。"
                        "请不要再创建新卡片；用 list_cards / get_card_tree 检查结构，"
                        "处理必要的挂载，然后输出最终总结并停止。\n"
                    )
            if min_cards > 0:
                n_cards = _n_cards()
                if n_cards < min_cards:
                    extras.append(
                        f"当前知识库只有 {n_cards} 张卡片，低于本月实验下限 {min_cards}。\n"
                        "禁止输出最终总结或停止；请先 list_cards 查看现有卡片，\n"
                        "优先对尚未扩展的叶子卡调用 expand_from_card；\n"
                        "若 expand 返回 0 张新卡，改用 search_by_keyword 搜索缺失主题并挂载。\n"
                    )
            if extras:
                msgs.append({"role": "user", "content": "\n\n".join(extras)})
            return msgs

        def _on_failure(tool_name: str, r: ToolResult) -> None:
            nonlocal _correction_hint
            _correction_hint = _build_correction(tool_name, r.summary)

        def _on_result(tool_name: str, r: ToolResult) -> None:
            nonlocal _mount_hint
            if tool_name == "link_card" and r.success:
                _mount_hint = None  # 挂载动作已完成，清除待办提示
                return
            if r.success and tool_name in ("search_by_keyword", "expand_from_card"):
                titles = (r.data or {}).get("titles") or []
                if titles:
                    _mount_hint = _build_mount_hint(tool_name, titles)

        loop_bridge.register(key)

        try:
            if is_loop:
                try:
                    session._messages.append({"role": "system", "content": LOOP_INSTRUCTIONS})
                    loop_end = start_time.timestamp() + LOOP_TIMEOUT
                    yield _to_sse("text", {"content": f"🔄 Loop 模式已启动 — {instruction}\n\n"})

                    for iteration in range(LOOP_MAX_ITERATIONS):
                        if datetime.utcnow().timestamp() > loop_end:
                            yield _to_sse("text", {"content": "\n\n⏰ Loop 超时"})
                            break

                        # 读取用户中途消息（非阻塞）
                        user_msg = loop_bridge.receive(ctx._key(username, session_id))
                        if user_msg:
                            session.add_user_message(user_msg)
                            TreeSummaryCache.instance().invalidate(username, session_id)
                            yield _to_sse("text", {"content": f"📩 收到用户消息: {user_msg}\n"})

                        await session.flush()  # 持久化检查点：模型请求前保证已入日志的事实落盘
                        messages = _decide_messages()
                        decision: ToolDecision | None = None
                        _decide_t0 = time.time()
                        async for item, token in llm.decide_stream(messages, _schemas_with_card_limit()):
                            if token is not None:
                                yield _to_sse("text", {"content": token})
                            else:
                                decision = item

                        if decision.has_tool_calls():
                            logger.info(
                                "[AgentLoop] 迭代 %d 决策: tools=%s (%.1fs)",
                                iteration, [tc.name for tc in decision.tool_calls], time.time() - _decide_t0,
                            )
                            if decision.has_text():
                                session.add_assistant_message(
                                    _strip_xml_tool_blocks(decision.text),
                                    reasoning_content=decision.reasoning_content,
                                )
                            async for event in _execute_tools(
                                decision.tool_calls, executor, session, tools_used, failed_tools,
                                on_failure=_on_failure, on_result=_on_result,
                                reasoning_content=decision.reasoning_content,
                            ):
                                yield event

                            if len(failed_tools) >= 3:
                                session.add_system_message(
                                    "⚠️ 工具连续失败（≥3 次）。请立即停止重试，直接向用户如实汇报失败原因和当前进度，不要再调用任何工具。"
                                )
                                async for event in _stream_text(
                                    llm, _decide_messages(), session, max_tokens=300,
                                ):
                                    yield event
                                session.prune(AGENT_CONTEXT_MAX_MESSAGES)
                                yield _to_sse("complete", {"turns": iteration + 1, "tools_used": tools_used, "loop": True})
                                return

                            # 不再强制总结：下一轮 decide 的流式文本即本轮总结
                            # （loop 提示词要求总结后才能继续调用工具；工具事件本身是可见反馈）。

                        elif decision.has_text():
                            session.add_assistant_message(
                                _strip_xml_tool_blocks(decision.text),
                                reasoning_content=decision.reasoning_content,
                            )  # 文本已流式发出
                            if min_cards > 0 and _n_cards() < min_cards:
                                continue
                            session.prune(AGENT_CONTEXT_MAX_MESSAGES)
                            yield _to_sse("complete", {"turns": iteration + 1, "tools_used": tools_used, "loop": True})
                            return
                        else:
                            async for event in _stream_text(llm, messages, session):
                                yield event
                            if min_cards > 0 and _n_cards() < min_cards:
                                continue
                            session.prune(AGENT_CONTEXT_MAX_MESSAGES)
                            yield _to_sse("complete", {"turns": iteration + 1, "tools_used": tools_used, "loop": True})
                            return

                        if iteration > 0 and iteration % 5 == 0:
                            session.prune(AGENT_CONTEXT_MAX_MESSAGES * 2)

                    # 轮数耗尽 → 强制总结（流式）
                    async for event in _stream_text(
                        llm, _decide_messages(), session,
                    ):
                        yield event
                    session.prune(AGENT_CONTEXT_MAX_MESSAGES)
                    yield _to_sse("complete", {"turns": iteration + 1, "tools_used": tools_used, "loop": True})
                    return
                except Exception:
                    pass

            for turn in range(AGENT_MAX_TURNS):
                if (datetime.utcnow() - start_time).total_seconds() > AGENT_TIMEOUT:
                    if not needs_text_response:
                        yield _to_sse("error", {"message": "Agent 超时"})
                        return

                user_msg = loop_bridge.receive(key)
                if user_msg:
                    _last_user_msg = user_msg
                    session.add_user_message(user_msg)
                    TreeSummaryCache.instance().invalidate(username, session_id)
                    yield _to_sse("text", {"content": f"📩 收到用户消息: {user_msg}\n"})

                await session.flush()  # 持久化检查点：模型请求前保证已入日志的事实落盘
                decision: ToolDecision | None = None
                _decide_t0 = time.time()
                async for item, token in llm.decide_stream(
                    _decide_messages(), _schemas_with_card_limit(),
                ):
                    if token is not None:
                        yield _to_sse("text", {"content": token})
                    else:
                        decision = item

                if decision.has_tool_calls():
                    logger.info(
                        "[AgentLoop] 第 %d 轮决策: tools=%s (%.1fs)",
                        turn, [tc.name for tc in decision.tool_calls], time.time() - _decide_t0,
                    )
                    if decision.has_text():
                        session.add_assistant_message(
                            _strip_xml_tool_blocks(decision.text),
                            reasoning_content=decision.reasoning_content,
                        )
                    async for event in _execute_tools(
                        decision.tool_calls, executor, session, tools_used, failed_tools,
                        on_failure=_on_failure, on_result=_on_result,
                        reasoning_content=decision.reasoning_content,
                    ):
                        yield event

                    if len(failed_tools) >= 3:
                        session.add_system_message(
                            "⚠️ 工具连续失败（≥3 次）。请立即停止重试，直接向用户如实汇报失败原因和当前进度，不要再调用任何工具。"
                        )
                        async for event in _stream_text(
                            llm, _decide_messages(), session, max_tokens=300,
                        ):
                            yield event
                        session.prune(AGENT_CONTEXT_MAX_MESSAGES)
                        yield _to_sse("complete", {"turns": turn + 1, "tools_used": tools_used})
                        return

                    # 不再强制总结（省掉每轮 1 次 LLM 往返）：工具事件本身就是可见反馈，
                    # 下一轮 decide 会在工具结果之上自然流式产出文本（最终回答或继续调用）。
                    needs_text_response = True

                else:
                    needs_text_response = False
                    if decision.has_text():
                        session.add_assistant_message(
                            _strip_xml_tool_blocks(decision.text),
                            reasoning_content=decision.reasoning_content,
                        )  # 文本已流式发出
                    else:
                        async for event in _stream_text(
                            llm, _decide_messages(), session
                        ):
                            yield event
                    break

            session.prune(AGENT_CONTEXT_MAX_MESSAGES)

            if needs_text_response:
                await session.flush()  # 轮数耗尽前的收尾总结：先落盘再请求模型
                async for event in _stream_text(
                    llm, _decide_messages(), session,
                ):
                    yield event

            yield _to_sse("complete", {"turns": turn + 1, "tools_used": tools_used})

        except asyncio.CancelledError:
            yield _to_sse("error", {"message": "Agent 任务被取消"})  # 工具补记已在 _execute_tools 内部完成
        except Exception as e:
            logger.error("[AgentLoop] Error: %s", e)
            yield _to_sse("error", {"message": str(e)})
        finally:
            # 最终持久化检查点：正常完成/取消/异常路径都保证内存历史落盘。
            await session.flush()
            loop_bridge.unregister(key)

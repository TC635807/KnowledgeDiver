"""Agent LLM 提供者 — 封装 tool-calling 的流式调用。

decide_stream: 流式调用，逐 token 产出文本的同时检测工具调用（单次调用完成决策+流式回答）
stream:        纯流式文本（强制文本场景，tools=[]）
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from backend.config import AGENT_REASONING_EFFORT
from backend.ai.openai_provider import normalize_api_base_url
from backend.services.ai_settings import effective_settings
from .schemas import ToolCall

logger = logging.getLogger(__name__)

# 单次 LLM 调用超时（秒）。与 AGENT_TIMEOUT / 前端 stall 统一为 300s。
# 旧实现无超时 → SDK 默认 600s，上游卡死时 SSE 静默 10 分钟。
LLM_CALL_TIMEOUT = 300.0


def _parse_tool_args(raw: str) -> tuple[dict | None, str]:
    """解析 LLM 输出的工具参数 JSON。坏 JSON 时做轻量修复（LLM 常见：单引号/尾逗号/裸键）。

    Returns:
        (解析成功的 dict, "")——修复失败返回 (None, 原始字符串)，
        由执行器把原始串回喂给 LLM 明确报错（不静默降级为 {}）。
    """
    if not raw or not raw.strip():
        return {}, ""
    try:
        return json.loads(raw), ""
    except (json.JSONDecodeError, TypeError):
        pass
    candidate = raw.strip()
    # 1) 单引号 → 双引号（值内撇号可能被误伤，但值得先试）
    candidate = candidate.replace("'", '"')
    # 2) 去除尾逗号（{...} 或 [...] 末尾的逗号）
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    # 3) 裸键补引号（{keyword: "xxx"} → {"keyword": "xxx"}）
    candidate = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', candidate)
    try:
        return json.loads(candidate), ""
    except (json.JSONDecodeError, TypeError):
        return None, raw


@dataclass
class ToolDecision:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: Optional[str] = None
    needs_stream: bool = False
    reasoning_content: str = ""  # 思考模式下的 CoT，工具调用轮必须回传

    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def has_text(self) -> bool:
        return bool(self.text)


class AgentLLM:
    def __init__(self):
        # 每次构造都读一次生效配置：前端「API 配置」保存后无需重启后端
        settings = effective_settings()
        self._client = AsyncOpenAI(
            api_key=settings["api_key"],
            base_url=normalize_api_base_url(settings["api_url"]),
            timeout=LLM_CALL_TIMEOUT, max_retries=1,
        )
        # 卡片生成与 Agent 统一使用同一个模型
        self._model = settings["model"]

    async def decide_stream(
        self, messages: list[dict], tools: list[dict], max_tokens: int | None = None,
    ) -> AsyncIterator[tuple[ToolDecision | None, str | None]]:
        """流式 decide — 最终回答逐 token 产出。

        产出 (None, token) 表示文本增量；流结束时产出 (ToolDecision, None)。
        流式过程中同时累积 tool_calls（按 index 增量拼接 arguments），
        结束时不产出文本增量，只产出含工具调用的 ToolDecision。
        """
        tool_choice = "auto" if tools else "none"
        kwargs: dict = dict(
            model=self._model, messages=messages, tools=tools or None,
            tool_choice=tool_choice, stream=True,
            reasoning_effort=AGENT_REASONING_EFFORT,
            extra_body={"thinking": {"type": "enabled"}},
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        stream_resp = await self._client.chat.completions.create(**kwargs)
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict] = {}
        async for chunk in stream_resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning_chunk = getattr(delta, "reasoning_content", None)
            if reasoning_chunk:
                reasoning_parts.append(reasoning_chunk)
            if delta.content:
                text_parts.append(delta.content)
                yield (None, delta.content)
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    entry = tool_calls.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        entry["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            entry["name"] = tc.function.name
                        if tc.function.arguments:
                            entry["args"] += tc.function.arguments
        reasoning = "".join(reasoning_parts)
        if tool_calls:
            calls: list[ToolCall] = []
            for idx in sorted(tool_calls):
                e = tool_calls[idx]
                args, raw = _parse_tool_args(e["args"])
                if args is None:
                    # 坏 JSON 修复失败：把原始串透传给执行器，由执行器明确报错回喂，
                    # 而不是静默降级为 {} 导致必填参数 KeyError 泛化报错
                    logger.warning("[AgentLLM] Tool args JSON parse failed for %s: %r", e.get("name"), raw[:120])
                    args = {"_raw_args": raw}
                calls.append(ToolCall(
                    id=e["id"] or f"call_{idx}", name=e["name"] or "tool", args=args,
                ))
            logger.info("[AgentLLM] Tool calls: %s", [tc.name for tc in calls])
            # 引导语已流式展示给用户，需同步入会话，否则 Agent 下一轮会重复
            yield (ToolDecision(
                tool_calls=calls, text="".join(text_parts).strip() or None,
                reasoning_content=reasoning,
            ), None)
        elif text_parts:
            yield (ToolDecision(text="".join(text_parts).strip(), reasoning_content=reasoning), None)
        else:
            yield (ToolDecision(needs_stream=True, reasoning_content=reasoning), None)

    async def stream(
        self, messages: list[dict], tools: list[dict], max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        tool_choice = "auto" if tools else "none"
        kwargs: dict = dict(
            model=self._model, messages=messages, tools=tools or None,
            tool_choice=tool_choice, stream=True,
            reasoning_effort=AGENT_REASONING_EFFORT,
            extra_body={"thinking": {"type": "enabled"}},
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        stream_resp = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream_resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta.content:
                yield delta.content

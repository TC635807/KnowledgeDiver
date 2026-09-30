"""
质量改进策略模块 — 策略模式封装。

提供可插拔的改进策略抽象，支持运行时切换不同改进方案：
- SearchBasedStrategy: 硬编码阈值决策的自动化搜索改进
- AgentBasedStrategy: 委托 LLM Agent 自主决策改进策略
- 未来可扩展 HybridStrategy / HumanInTheLoopStrategy 等

Usage:
    from backend.quality.improver import ImprovementService

    svc = ImprovementService(strategy=AgentBasedStrategy())
    cards, task_id = await svc.improve(
        api=api,
        gap_threshold=0.5,
        max_iterations=3,
        external_task=task,
    )
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from backend.models.card import Card
from backend.models.task import TaskType, TaskProgress

logger = logging.getLogger(__name__)


class ImprovementStrategy(ABC):
    """改进策略抽象基类。

    子类必须实现 run()，接收薄弱卡片列表，返回生成的新卡片列表。
    通过 task.emit() 报告进度，供 SSE 流订阅。
    """

    @abstractmethod
    async def run(
        self,
        api: "PipelineAPI",
        weak_cards: List[dict],
        task,
        max_iterations: int = 3,
    ) -> List[Card]:
        ...


class SearchBasedStrategy(ImprovementStrategy):
    """基于搜索的自动化改进策略。

    对每张薄弱卡片，根据 structure_score 硬编码决策：
    - structure_score < 0.5 → 用卡片标题重新搜索，更新原卡片内容
    - structure_score >= 0.5 → 用卡片标题搜索生成补充卡片

    特点：快速、确定性、可控深度（无递归探索）。
    """

    async def run(
        self,
        api: "PipelineAPI",
        weak_cards: List[dict],
        task,
        max_iterations: int = 3,
    ) -> List[Card]:
        from backend.task import TaskService

        cards: List[Card] = []

        await task.emit("progress", TaskProgress(
            stage="analyzing",
            message=f"搜索策略：正在改进 {len(weak_cards)} 张薄弱卡片...",
            progress=0.05,
        ))

        for iteration in range(max_iterations):
            if not weak_cards:
                break

            progress_base = 0.15 + (0.8 * iteration / max_iterations)
            await task.emit("progress", TaskProgress(
                stage="analyzing",
                message=f"迭代 {iteration + 1}/{max_iterations}: 处理 {len(weak_cards)} 张卡片",
                progress=progress_base,
            ))

            next_weak: List[dict] = []

            for i, gap_info in enumerate(weak_cards):
                card_title = gap_info["title"]
                struct_score = gap_info["structure_score"]
                card_id = gap_info["card_id"]

                if struct_score < 0.5:
                    await task.emit("progress", TaskProgress(
                        stage="refreshing",
                        message=f"「{card_title}」内容不足（structure={struct_score:.2f}），正在刷新...",
                        progress=progress_base + (0.2 * (i + 0.5) / len(weak_cards)),
                        current_item=card_title,
                    ))

                    new_cards = await api.search_by_keyword(card_title, max_sources=2)
                    if new_cards:
                        best = max(new_cards, key=lambda c: (len(c.content or ""), len(c.sources or [])))
                        api.update_card(card_id=card_id, content=best.content, metadata=best.metadata)
                        await task.emit("progress", TaskProgress(
                            stage="refreshed",
                            message=f"已刷新「{card_title}」",
                            progress=progress_base + (0.2 * (i + 1) / len(weak_cards)),
                            current_item=card_title,
                        ))
                else:
                    await task.emit("progress", TaskProgress(
                        stage="searching",
                        message=f"「{card_title}」需要扩展（gap={gap_info.get('gap_score', '?')}），正在搜索...",
                        progress=progress_base + (0.2 * (i + 0.5) / len(weak_cards)),
                        current_item=card_title,
                    ))

                    # 挂载到被改进的薄弱卡下：source_card_id 自动建无向链接 + 设 parent_id
                    new_cards = await api.search_by_keyword(
                        card_title, max_sources=2, source_card_id=card_id,
                    )
                    existing_titles = {c.title for c in api.card_store.list_cards()}
                    new_cards = [c for c in new_cards if c.title not in existing_titles]
                    for c in new_cards:
                        await task.emit("card", c)

                cards.extend(new_cards)

            # 重新评估：检查是否还有薄弱卡片
            if iteration < max_iterations - 1:
                scores = api.score_cards_quality()
                next_weak = [s for s in scores if s["gap_score"] >= 0.5][:3]
                weak_cards = next_weak

        await task.emit("progress", TaskProgress(
            stage="complete",
            message=f"搜索策略完成，共处理 {len(cards)} 张卡片",
            progress=0.95,
        ))
        return cards


class AgentBasedStrategy(ImprovementStrategy):
    """委托 LLM Agent 自主决策的改进策略。

    构造结构化提示词，通过 Agent 的 ReAct 循环逐卡片评估并改进。
    Agent 可使用现有工具（assess_card_quality / search_by_keyword /
    expand_from_card / refresh_card）灵活决策。

    特点：灵活、智能、适合复杂知识库。
    """

    async def run(
        self,
        api: "PipelineAPI",
        weak_cards: List[dict],
        task,
        max_iterations: int = 3,
    ) -> List[Card]:
        await task.emit("progress", TaskProgress(
            stage="analyzing",
            message=f"Agent 策略：委托 AI 助手改进 {len(weak_cards)} 张卡片...",
            progress=0.1,
        ))

        lines = []
        for c in weak_cards:
            lines.append(
                f"- 「{c['title']}」"
                f"(gap={c.get('gap_score', 0):.2f}, "
                f"structure={c.get('structure_score', 0):.2f}, "
                f"semantic={c.get('semantic_score', 0):.2f})"
            )

        prompt = "\n".join([
            "请依次分析以下薄弱卡片的改进方案，使用合适的工具逐步改进：",
            *lines,
            "",
            "- structure_score 低 → 用 search_by_keyword 搜索补充内容",
            "- semantic_score 低 → 用 expand_from_card 生成子卡片扩展知识面",
            "每次改进后重新调用 assess_card_quality 确认效果。",
        ])

        from backend.agent.loop import run_agent_loop

        cards: List[Card] = []
        async for sse_line in run_agent_loop(
            username=api.username,
            session_id=api.session_id,
            user_message=prompt,
        ):
            import json
            try:
                if sse_line.startswith("data: "):
                    event = json.loads(sse_line[6:])
                    if event.get("type") == "card" and event.get("data"):
                        cards.append(event["data"])
                    elif event.get("type") == "progress":
                        await task.emit("progress", TaskProgress(
                            stage=event["data"].get("stage", "analyzing"),
                            message=event["data"].get("message", ""),
                            progress=min(event["data"].get("progress", 0), 0.95),
                        ))
            except (json.JSONDecodeError, KeyError):
                pass

        await task.emit("progress", TaskProgress(
            stage="complete",
            message=f"Agent 策略完成",
            progress=0.95,
        ))
        return cards


class ImprovementService:
    """质量改进服务 — 统一入口。

    封装"分析 → 找到薄弱卡片 → 执行改进策略"的完整流程。
    策略通过构造函数注入，默认使用 AgentBasedStrategy。

    Usage:
        svc = ImprovementService()
        cards, _ = await svc.improve(api=api, gap_threshold=0.5)
    """

    def __init__(self, strategy: ImprovementStrategy | None = None):
        self._strategy = strategy or AgentBasedStrategy()

    @property
    def strategy(self) -> ImprovementStrategy:
        return self._strategy

    async def improve(
        self,
        api: "PipelineAPI",
        gap_threshold: float = 0.5,
        max_iterations: int = 3,
        external_task=None,
    ) -> Tuple[List[Card], Optional[str]]:
        """分析质量并执行改进策略。

        Args:
            api: PipelineAPI 实例
            gap_threshold: 薄弱阈值
            max_iterations: 最大迭代次数
            external_task: 外部 Task 对象（用于 SSE 进度报告）

        Returns:
            (生成的卡片列表, 任务ID或None)
        """
        from backend.task import TaskService

        cards: List[Card] = []
        task = external_task
        task_id: str | None = None

        try:
            scores = api.score_cards_quality()
            weak_cards = [s for s in scores if s["gap_score"] >= gap_threshold][:5]

            if not weak_cards:
                if task:
                    await task.emit("progress", TaskProgress(
                        stage="complete",
                        message="所有卡片质量达标",
                        progress=1.0,
                    ))
                return [], task_id

            if task is None:
                display_names = ", ".join(c["title"] for c in weak_cards[:3])
                task = TaskService.get_instance().create(
                    task_type=TaskType.GAP_DRIVEN,
                    username=api.username,
                    session_id=api.session_id,
                    keyword=f"改进: {display_names}",
                )
            task_id = task.task_id

            cards = await self._strategy.run(api, weak_cards, task, max_iterations)

        except Exception as e:
            logger.error("ImprovementService.improve failed: %s", e)
            if task:
                await task.emit("error", {"message": str(e)})

        return cards, task_id


# Ensure PipelineAPI is available for type hints without circular import
from backend.pipeline.api import PipelineAPI  # noqa: E402

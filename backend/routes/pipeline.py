"""信息收集流水线路由。

提供 collect（搜索→抓取→总结→生成卡片）和 expand（从卡片延伸探索）的 SSE 流式接口，
以及任务管理（列表、查询、取消、重连流）接口。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
import json

from backend.pipeline.stages import PipelineProgress
from backend.pipeline.factory import create_pipeline
from backend.search import DEFAULT_SEARCH_PROVIDER
from backend.models import Card
from backend.models.task import TaskType, TaskStatus
from backend.models.user import User
from backend.routes.auth import get_current_user
from backend.config import DEFAULT_SESSION_ID, MAX_CONCURRENT_TASKS
from backend.task import TaskService

router = APIRouter()
logger = logging.getLogger(__name__)
task_service = TaskService.get_instance()


def _check_concurrency_limit(current_user: User) -> None:
    """检查用户并发任务上限。超出时抛出 HTTPException。"""
    running_count = len(task_service.list_running(current_user.username))
    if running_count >= MAX_CONCURRENT_TASKS:
        raise HTTPException(
            status_code=429,
            detail=f"并发任务已达上限，同时最多 {MAX_CONCURRENT_TASKS} 个搜索任务",
        )


async def _run_pipeline_task(task, generator, closeable=None, complete_message: str = "Pipeline complete"):
    try:
        async for item in generator:
            if isinstance(item, PipelineProgress):
                from backend.models.task import TaskProgress
                progress = TaskProgress(
                    stage=item.stage,
                    message=item.message,
                    progress=item.progress,
                    current_item=item.current_item,
                    ai_output=item.ai_output,
                )
                await task.emit("progress", progress)
            elif isinstance(item, Card):
                await task.emit("card", item)
        await task.emit("complete", {"message": complete_message})
    except asyncio.CancelledError:
        task.status = TaskStatus.CANCELLED
    except Exception as e:
        await task.emit("error", {"message": str(e)})
    finally:
        if closeable and hasattr(closeable, 'close'):
            await closeable.close()


@router.get("/api/pipeline/collect")
async def collect_pipeline(
    request: Request,
    keyword: str,
    max_sources: int = 2,
    source_card_id: str | None = None,
    session_id: str = DEFAULT_SESSION_ID,
    search_level: str = "default",
    search_provider: str = DEFAULT_SEARCH_PROVIDER,
    current_user: User = Depends(get_current_user),
):
    """搜索关键词并启动收集流水线，返回 SSE 事件流。"""
    logger.info(f"[Pipeline] Collect request: keyword='{keyword}', max_sources={max_sources}, session_id={session_id}, user={current_user.username}")

    _check_concurrency_limit(current_user)

    existing_tasks = task_service.list_running(current_user.username)
    for existing_task in existing_tasks:
        if (existing_task.keyword == keyword and 
            existing_task.session_id == session_id and
            existing_task.task_type == TaskType.COLLECT):
            logger.info(f"[Pipeline] Found existing running task for keyword '{keyword}', reusing task {existing_task.task_id}")
            return StreamingResponse(
                task_service.stream_events(existing_task),
                media_type="text/event-stream",
            )
    
    task = task_service.create(
        task_type=TaskType.COLLECT,
        username=current_user.username,
        session_id=session_id,
        keyword=keyword,
        params={"max_sources": max_sources, "source_card_id": source_card_id, "search_level": search_level},
    )

    # max_explore_depth=0：手动收集只生成目标卡片，不自动探索联想主题
    # （与 Agent 路径 explore=False 行为对齐，见 pipeline/api.py search_by_keyword_with_task；
    #   需要扩展时用户通过 /api/pipeline/expand 显式触发）
    pipeline = create_pipeline(current_user.username, session_id, search_level=search_level, search_provider=search_provider, max_explore_depth=0)

    async def run_in_background():
        await _run_pipeline_task(
            task,
            pipeline.run(keyword, max_sources=max_sources, source_card_id=source_card_id),
            closeable=pipeline,
        )
    
    events = task_service.launch(task, run_in_background())
    
    return StreamingResponse(
        events,
        media_type="text/event-stream",
    )


@router.get("/api/pipeline/expand")
async def expand_pipeline(
    request: Request,
    source_card_id: str,
    card_title: str,
    max_topics: int = 5,
    session_id: str = DEFAULT_SESSION_ID,
    search_level: str = "default",
    search_provider: str = DEFAULT_SEARCH_PROVIDER,
    current_user: User = Depends(get_current_user),
):
    """从指定卡片延伸探索，展开相关主题生成新卡片，返回 SSE 事件流。"""
    logger.info(f"[Pipeline] Expand request: source_card_id='{source_card_id}', card_title='{card_title}', session_id={session_id}, user={current_user.username}")

    _check_concurrency_limit(current_user)

    expand_keyword = f"延申: {card_title}"
    existing_tasks = task_service.list_running(current_user.username)
    for existing_task in existing_tasks:
        if (existing_task.keyword == expand_keyword and 
            existing_task.session_id == session_id and
            existing_task.task_type == TaskType.EXPAND):
            logger.info(f"[Pipeline] Found existing running expand task for '{card_title}', reusing task {existing_task.task_id}")
            return StreamingResponse(
                task_service.stream_events(existing_task),
                media_type="text/event-stream",
            )
    
    from backend.storage import SqliteCardStore
    card_store = SqliteCardStore(username=current_user.username, session_id=session_id)
    card = card_store.get_card(source_card_id)
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {source_card_id}")
    
    card_content = card.content
    
    task = task_service.create(
        task_type=TaskType.EXPAND,
        username=current_user.username,
        session_id=session_id,
        keyword=expand_keyword,
        params={"source_card_id": source_card_id, "max_topics": max_topics, "search_level": search_level},
    )
    
    pipeline = create_pipeline(
        current_user.username, session_id,
        search_level=search_level, max_topics=max_topics,
        search_provider=search_provider,
    )

    async def run_in_background():
        await _run_pipeline_task(
            task,
            pipeline.run_expand(
                card_content,
                source_card_id=source_card_id,
                max_sources=2,
                max_topics=max_topics,
                search_level=search_level,
            ),
            closeable=pipeline,
            complete_message="Expand complete",
        )
    
    events = task_service.launch(task, run_in_background())
    
    return StreamingResponse(
        events,
        media_type="text/event-stream",
    )


@router.get("/api/pipeline/gap-analysis")
async def gap_analysis(
    session_id: str = DEFAULT_SESSION_ID,
    gap_threshold: float = 0.5,
    current_user: User = Depends(get_current_user),
):
    """获取当前会话的卡片质量分析报告。

    返回：
    - total: 卡片总数
    - avg_gap_score: 平均 gap_score
    - avg_structure_score: 平均 structure_score
    - avg_semantic_score: 平均 semantic_score
    - weakest_count: 未过目标值的卡片数量（gap_score >= threshold）
    - top_weakest: gap_score 最高的 N 张卡片
    - top_best: gap_score 最低的 N 张卡片
    """
    from backend.pipeline.api import PipelineAPI
    from backend.ai.embedder import Embedder
    from backend.quality.scorer import score_all_cards
    from backend.storage.raw_store import RawPageStore

    api = PipelineAPI(username=current_user.username, session_id=session_id)
    cards = api.card_store.list_cards()
    raw_store = RawPageStore(username=current_user.username, session_id=session_id)

    if not cards:
        return {
            "total": 0,
            "avg_gap_score": 0,
            "avg_structure_score": 0,
            "avg_semantic_score": 0,
            "avg_confidence_score": 0,
            "weakest_count": 0,
            "top_weakest": [],
            "top_best": [],
        }

    # 快通道：在 async 上下文中直接 await 嵌入模型，绕过 _run_async 桥接
    # 嵌入模型加载失败（未下载 / 网络不通）必须给可读错误，而不是不透明的 500
    try:
        embedder = Embedder.get()
        texts = [f"{c.title}\n{c.content}" for c in cards]
        embeddings = await embedder.encode(texts)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[GapAnalysis] 嵌入模型不可用: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="嵌入模型不可用（可能未下载或加载失败）：" + str(exc)[:200],
        ) from exc
    emb_map = {cards[i].id: embeddings[i] for i in range(len(cards))}

    all_scores = score_all_cards(cards, emb_map, raw_store)

    if not all_scores:
        return {
            "total": 0,
            "avg_gap_score": 0,
            "avg_structure_score": 0,
            "avg_semantic_score": 0,
            "avg_graph_score": 0,
            "avg_confidence_score": 0,
            "weakest_count": 0,
            "top_weakest": [],
            "top_best": [],
        }

    total = len(all_scores)
    avg_gap = sum(s["gap_score"] for s in all_scores) / total
    avg_struct = sum(s["structure_score"] for s in all_scores) / total
    avg_semantic = sum(s["semantic_score"] for s in all_scores) / total
    avg_graph = sum(s.get("graph_score", 0.5) for s in all_scores) / total
    avg_conf = sum(s.get("confidence_score", 0.5) for s in all_scores) / total
    weakest_count = sum(1 for s in all_scores if s["gap_score"] >= gap_threshold)

    sorted_by_gap = sorted(all_scores, key=lambda x: x["gap_score"], reverse=True)

    return {
        "total": total,
        "avg_gap_score": round(avg_gap, 4),
        "avg_structure_score": round(avg_struct, 4),
        "avg_semantic_score": round(avg_semantic, 4),
        "avg_graph_score": round(avg_graph, 4),
        "avg_confidence_score": round(avg_conf, 4),
        "weakest_count": weakest_count,
        "weakest_ratio": round(weakest_count / total, 4) if total > 0 else 0,
        "top_weakest": sorted_by_gap[:5],
        "top_best": sorted_by_gap[-5:][::-1] if total >= 5 else sorted_by_gap[::-1],
        "scores": sorted_by_gap,
    }


@router.get("/api/pipeline/cluster-report")
async def cluster_report(
    session_id: str = DEFAULT_SESSION_ID,
    current_user: User = Depends(get_current_user),
):
    """获取当前会话的簇级健康度报告（语义聚类 + 簇级 gap 聚合）。"""
    from backend.pipeline.api import PipelineAPI
    from backend.quality.provider import QualityProvider

    api = PipelineAPI(username=current_user.username, session_id=session_id)
    provider = QualityProvider(api.card_store, raw_store=api.raw_store)
    return provider.get_cluster_report()


@router.post("/api/pipeline/plan-gaps")
async def plan_gaps(
    session_id: str = DEFAULT_SESSION_ID,
    cluster_id: int | None = None,
    current_user: User = Depends(get_current_user),
):
    """对薄弱簇做 LLM 盘点，返回缺失子主题 + 搜索关键词（复用 agent 工具逻辑）。"""
    from backend.pipeline.api import PipelineAPI
    from backend.agent.tools import ToolExecutor

    api = PipelineAPI(username=current_user.username, session_id=session_id)
    executor = ToolExecutor(api)
    result = await executor.execute(
        "plan_knowledge_gaps",
        {"cluster_id": cluster_id} if cluster_id is not None else {},
    )
    if not result.success:
        raise HTTPException(status_code=422, detail=result.summary)
    return result.data


@router.get("/api/pipeline/gap-driven")
async def gap_driven_pipeline(
    request: Request,
    keyword: str,
    max_sources: int = 2,
    gap_threshold: float = 0.5,
    structure_threshold: float = 0.5,
    max_iterations: int = 3,
    session_id: str = DEFAULT_SESSION_ID,
    search_level: str = "default",
    search_provider: str = DEFAULT_SEARCH_PROVIDER,
    current_user: User = Depends(get_current_user),
):
    """Gap-Driven 探索循环，返回 SSE 事件流。

    决策逻辑：
    - structure_score 低 → refresh_card（补充内容）
    - structure_score 尚可 → expand_from_card（扩展子主题）
    """
    logger.info(
        f"[Pipeline] Gap-Driven request: keyword='{keyword}', "
        f"gap_threshold={gap_threshold}, structure_threshold={structure_threshold}, "
        f"max_iterations={max_iterations}, session_id={session_id}, user={current_user.username}"
    )

    _check_concurrency_limit(current_user)

    gap_keyword = f"Gap-Driven: {keyword}"
    existing_tasks = task_service.list_running(current_user.username)
    for existing_task in existing_tasks:
        if (existing_task.keyword == gap_keyword and
            existing_task.session_id == session_id and
            existing_task.task_type == TaskType.GAP_DRIVEN):
            logger.info(f"[Pipeline] Found existing running gap-driven task for '{keyword}', reusing task {existing_task.task_id}")
            return StreamingResponse(
                task_service.stream_events(existing_task),
                media_type="text/event-stream",
            )

    task = task_service.create(
        task_type=TaskType.GAP_DRIVEN,
        username=current_user.username,
        session_id=session_id,
        keyword=gap_keyword,
        params={
            "keyword": keyword,
            "gap_threshold": gap_threshold,
            "structure_threshold": structure_threshold,
            "max_iterations": max_iterations,
        },
    )

    from backend.pipeline.api import PipelineAPI
    api = PipelineAPI(username=current_user.username, session_id=session_id)

    async def run_in_background():
        try:
            await api.gap_driven_exploration_with_task(
                keyword=keyword,
                max_sources=max_sources,
                gap_threshold=gap_threshold,
                structure_threshold=structure_threshold,
                max_iterations=max_iterations,
                search_level=search_level,
                search_provider=search_provider,
                external_task=task,
            )
        except Exception as e:
            logger.error(f"[Pipeline] Gap-Driven background task failed: {e}")
            await task.emit("error", {"message": str(e)})

    events = task_service.launch(task, run_in_background())

    return StreamingResponse(
        events,
        media_type="text/event-stream",
    )


@router.get("/api/tasks")
async def list_tasks(
    session_id: str | None = None,
    current_user: User = Depends(get_current_user),
):
    """列出当前用户的任务（可按 session_id 过滤）。"""
    tasks = task_service.list_user_tasks(current_user.username, session_id)
    return [t.to_dict() for t in tasks]


@router.get("/api/tasks/running")
async def list_running_tasks(
    current_user: User = Depends(get_current_user),
):
    """列出当前用户正在运行的任务。"""
    tasks = task_service.list_running(current_user.username)
    return [t.to_dict() for t in tasks]


@router.get("/api/tasks/{task_id}")
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """获取单个任务的详细状态。"""
    task = task_service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")
    return task.to_dict()


@router.get("/api/tasks/{task_id}/stream")
async def reconnect_task_stream(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """重连已完成或进行中任务的 SSE 事件流。已完成的回放历史事件，进行中的回放后继续推送。"""
    task = task_service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if task.status in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED):
        return StreamingResponse(
            task_service.replay_events(task),
            media_type="text/event-stream",
        )

    # Running task: replay current state first, then continue with live events
    return StreamingResponse(
        task_service.replay_events(task),
        media_type="text/event-stream",
    )


@router.delete("/api/tasks/{task_id}")
async def cancel_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """取消正在运行的任务。"""
    task = task_service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    task_service.cancel(task_id)

    return {"status": "cancelled", "task_id": task_id}


@router.delete("/api/tasks/{task_id}/remove")
async def remove_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """移除任务记录（如果正在运行先取消）。"""
    task = task_service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.username != current_user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    task_service.remove(task_id)
    return {"status": "removed", "task_id": task_id}

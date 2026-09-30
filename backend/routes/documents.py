"""文档分析路由。

流程：上传文件 → 解析文本 → AI 分析结构 → 流水线生成卡牌树（SSE 流式进度）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request

from backend.models.user import User
from backend.models.task import TaskType, TaskStatus
from backend.routes.auth import get_current_user
from backend.task import TaskService
from backend.documents.parser import parse_file, ParseError
from backend.ai import OpenAIProvider
from backend.ai.config import load_config
from backend.ai.openai_provider import _extract_json_from_response
from backend.pipeline.factory import create_document_pipeline
from backend.config import DEFAULT_SESSION_ID, MAX_CONCURRENT_TASKS

router = APIRouter()
logger = logging.getLogger(__name__)
task_service = TaskService.get_instance()


@router.post("/api/documents/upload")
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(DEFAULT_SESSION_ID),
    current_user: User = Depends(get_current_user),
):
    """上传文档文件并启动 AI 分析。

    解析文件后通过 TaskManager 注册任务，客户端连接 GET /api/tasks/{task_id}/stream
    接收 SSE 流式进度和生成的卡片。
    """
    filename = file.filename or "unknown"

    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("txt", "md", "markdown", "pdf", "docx"):
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 .{ext}（支持: txt, md, pdf, docx）",
        )

    try:
        content = await file.read()
    except Exception as e:
        raise HTTPException(status_code=400, detail="文件读取失败")
    if not content:
        raise HTTPException(status_code=400, detail="文件为空")

    try:
        text = parse_file(filename, content)
    except ParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    logger.info(
        f"[Documents] Parsed '{filename}': {len(text)} chars, "
        f"user={current_user.username}, session={session_id}"
    )

    running_count = len(task_service.list_running(current_user.username))
    if running_count >= MAX_CONCURRENT_TASKS:
        raise HTTPException(
            status_code=429,
            detail=f"并发任务已达上限，同时最多 {MAX_CONCURRENT_TASKS} 个搜索任务",
        )

    existing_tasks = task_service.list_running(current_user.username)
    for existing_task in existing_tasks:
        if (existing_task.keyword == filename and
                existing_task.session_id == session_id and
                existing_task.task_type == TaskType.DOCUMENT):
            logger.info(
                f"[Documents] Reusing existing task {existing_task.task_id}"
            )
            return {"task_id": existing_task.task_id, "keyword": filename}

    task = task_service.create(
        task_type=TaskType.DOCUMENT,
        username=current_user.username,
        session_id=session_id,
        keyword=filename,
        params={"filename": filename, "chars": len(text)},
    )

    from backend.pipeline.pipeline import Pipeline

    async def run_in_background():
        await _run_document_task(task=task, filename=filename, text=text)

    task_service.start(task, run_in_background())

    logger.info(f"[Documents] Created task {task.task_id} for '{filename}'")
    return {"task_id": task.task_id, "keyword": filename}


async def _run_document_task(*, task, filename: str, text: str):
    """后台执行文档分析：AI 提取结构 → 流水线生成卡牌。"""
    from backend.models.task import TaskProgress

    try:
        await task.emit(
            "progress",
            TaskProgress(
                stage="analyzing",
                message="AI 正在分析文档结构...",
                progress=0.05,
                current_item=filename,
            ),
        )

        # 使用 AI 提取文档结构（保留现有的 analyze_document_stream 调用）
        ai_config = load_config()
        ai_provider = OpenAIProvider(ai_config)

        ai_buffer = ""
        async for chunk in ai_provider.analyze_document_stream(
            text, filename, existing_titles
        ):
            ai_buffer += chunk
            if len(ai_buffer) % 200 < len(chunk) + 1:
                await task.emit(
                    "progress",
                    TaskProgress(
                        stage="analyzing",
                        message="AI 正在分析文档结构...",
                        progress=0.05 + min(len(ai_buffer) / 8000, 0.25),
                        current_item=filename,
                        ai_output=ai_buffer,
                    ),
                )

        # 解析 AI 返回的结构化数据
        try:
            cleaned = _extract_json_from_response(ai_buffer)
            data = json.loads(cleaned)
        except (json.JSONDecodeError, ValueError) as e:
            await task.emit("error", {"message": f"AI 响应解析失败: {e}"})
            return

        if isinstance(data, list):
            await task.emit("error", {"message": "AI 返回了非预期的数组格式"})
            return

        main_data = data.get("main", {})
        sections_data = data.get("sections", [])

        # 构建 sections 列表：第一条为根结构，后续为各章节
        sections = [{
            "title": main_data.get("title", filename),
            "summary": main_data.get("content", main_data.get("summary", "")),
        }]

        for sec in sections_data:
            key_points = [
                {
                    "title": kp.get("title", ""),
                    "summary": kp.get("content", kp.get("summary", "")),
                }
                for kp in sec.get("key_points", [])
                if kp.get("title") and (kp.get("content") or kp.get("summary"))
            ]
            sections.append({
                "title": sec.get("title", ""),
                "summary": sec.get("content", sec.get("summary", "")),
                "key_points": key_points,
            })

        # 创建文档流水线并运行
        pipeline = create_document_pipeline(
            task.username,
            task.session_id,
        )

        async for item in pipeline.run_document(text, filename, sections):
            from backend.models import Card as CardModel
            from backend.pipeline.stages import PipelineProgress as PP

            if isinstance(item, PP):
                await task.emit(
                    "progress",
                    TaskProgress(
                        stage=item.stage,
                        message=item.message,
                        progress=item.progress,
                        current_item=item.current_item,
                        ai_output=item.ai_output,
                    ),
                )
            elif isinstance(item, CardModel):
                await task.emit("card", item)

        await pipeline.close()

        await task.emit(
            "complete", {"message": f"文档分析完成: {filename}"}
        )

    except asyncio.CancelledError:
        logger.info(f"[Documents] Task {task.task_id} cancelled")
        task.status = TaskStatus.CANCELLED
    except Exception as e:
        logger.error(f"[Documents] Analysis failed for '{filename}': {e}", exc_info=True)
        await task.emit("error", {"message": str(e)})

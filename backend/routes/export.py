"""卡片导出路由。

提供 Markdown 导出和下载接口。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.export import MarkdownExporter, ExportOptions
from backend.storage import InMemoryCardStore
from backend.models import Card

from typing import List, Optional

from fastapi.responses import FileResponse

router = APIRouter()


class ExportRequest(BaseModel):
    """导出请求体。"""
    root_id: Optional[str] = None
    options: Optional[ExportOptions] = None


def _get_store(request: Request) -> InMemoryCardStore:
    """从 app.state 获取卡片存储，用于测试兼容。"""
    # Expect app.state.card_store to be set by the application or tests
    store = getattr(request.app.state, "card_store", None)
    if store is None:
        # Fallback to an empty in-memory store to keep app running in tests
        store = InMemoryCardStore(cards=[])
        request.app.state.card_store = store
    return store


@router.post("/api/export")
async def export_tree(request: Request, payload: ExportRequest):
    """导出卡片树为 Markdown 字符串。"""
    store = _get_store(request)
    exporter = MarkdownExporter(store)
    markdown, count = await exporter.export_tree_with_count(root_id=payload.root_id, options=payload.options)
    return {"markdown": markdown, "card_count": count}


@router.get("/api/export/download")
async def download_export(request: Request, root_id: Optional[str] = None, filename: str = "export.md"):
    """导出卡片树并下载为 Markdown 文件。"""
    store = _get_store(request)
    exporter = MarkdownExporter(store)
    markdown, _ = await exporter.export_tree_with_count(root_id=root_id, options=None)
    # Write to a temporary file and serve as download
    path = filename
    with open(path, "w", encoding="utf-8") as f:
        f.write(markdown)
    return FileResponse(path, media_type="text/markdown", filename=filename)

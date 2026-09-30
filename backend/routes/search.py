"""搜索路由。

提供关键词搜索接口（通过 Exa 搜索客户端）。
"""

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from backend.search import SearchClient, SearchResult

router = APIRouter()


class SearchRequest(BaseModel):
    """搜索请求体。"""
    query: str
    max_results: int = 10


class SearchResponse(BaseModel):
    """搜索响应体。"""
    results: List[SearchResult]


@router.post("/api/search", response_model=SearchResponse)
async def search_endpoint(req: SearchRequest):
    """搜索关键词并返回结果列表。"""
    client = SearchClient()
    results = await client.search(req.query, req.max_results)
    return {"results": results}

"""网页抓取路由。

提供单 URL 和批量 URL 的网页内容抓取接口。
"""

from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..scraper.fetcher import ScrapedContent, WebFetcher

router = APIRouter()


class ScrapeRequest(BaseModel):
    """抓取请求体。"""
    url: str


_fetcher = WebFetcher()


@router.post("/api/scrape", response_model=ScrapedContent)
async def scrape(req: ScrapeRequest):
    """抓取单个 URL 的网页内容。"""
    try:
        content = await _fetcher.fetch(req.url)
        return content
    except Exception as e:
        raise HTTPException(status_code=400, detail="抓取失败")


@router.post("/api/scrape/batch", response_model=List[ScrapedContent])
async def scrape_batch(reqs: List[ScrapeRequest]):
    """批量抓取多个 URL 的网页内容。"""
    results: List[ScrapedContent] = []
    for r in reqs:
        try:
            results.append(await _fetcher.fetch(r.url))
        except Exception as e:
            # On batch error, skip or you could append an error object; here we raise for simplicity
            raise HTTPException(status_code=400, detail="批量抓取失败")
    return results

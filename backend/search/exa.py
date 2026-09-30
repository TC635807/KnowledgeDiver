"""Exa 搜索客户端模块。

基于 Exa MCP API，继承 BaseSearchClient。
只保留 MCP 协议调用 + 多格式响应解析，重试/限流/缓存由基类处理。
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

import httpx
from pydantic import BaseModel

from backend.config import EXA_TIMEOUT, EXA_MAX_RETRIES
from backend.search.base import BaseSearchClient, RateLimitError

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    """搜索结果条目。"""
    url: str
    title: str
    snippet: str = ""


class SearchClient(BaseSearchClient):
    """Exa 搜索客户端。

    通过 Exa MCP API 执行搜索，自动重试。
    """

    EXA_MCP_URL = "https://mcp.exa.ai/mcp"

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(
            max_concurrent=5,
            rate_per_sec=2.0,
            max_retries=EXA_MAX_RETRIES,
            base_delay=1.0,
        )
        self._http_client = http_client
        self._owns_client = False

    async def _do_search(self, query: str, max_results: int) -> List[SearchResult]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "web_search_exa",
                "arguments": {
                    "query": query,
                    "numResults": max_results,
                    "type": "auto",
                    "livecrawl": "fallback"
                }
            }
        }

        client = await self._get_client()
        response = await client.post(
            self.EXA_MCP_URL,
            json=payload,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json"
            }
        )

        if response.status_code == 429:
            raise RateLimitError("Exa API rate limit exceeded")

        response_text = response.text
        return self._parse_exa_response(response_text)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client:
            return self._http_client
        self._http_client = httpx.AsyncClient(timeout=EXA_TIMEOUT, trust_env=False)
        self._owns_client = True
        return self._http_client

    def _parse_exa_response(self, response_text: str) -> List[SearchResult]:
        """解析 Exa MCP 响应（SSE 或 JSON）。"""
        content = self._extract_content(response_text)
        if content is None:
            return self._parse_direct_json(response_text)

        if isinstance(content, list):
            return self._extract_results(content)
        return []

    def _extract_content(self, text: str) -> Optional[list]:
        """从 SSE data: 行中提取 content 字段。"""
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            json_str = line[5:].strip()
            try:
                parsed = json.loads(json_str)
                result = parsed.get("result")
                if result and "content" in result:
                    return result["content"]
            except (json.JSONDecodeError, KeyError):
                continue
        return None

    def _parse_direct_json(self, text: str) -> List[SearchResult]:
        """尝试直接解析 JSON 响应。"""
        try:
            parsed = json.loads(text)
            result = parsed.get("result", {})
            content = result.get("content", [])
            return self._extract_results(content)
        except (json.JSONDecodeError, KeyError):
            return []

    def _extract_results(self, content: list) -> List[SearchResult]:
        results: List[SearchResult] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text", "")
            parsed = self._parse_text_results(text)
            results.extend(parsed)
        return results

    def _parse_text_results(self, text: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        if not text:
            return results

        if "Title:" in text or "URL:" in text:
            return self._parse_exa_format(text)
        else:
            return self._parse_simple_format(text)

    def _parse_exa_format(self, text: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        lines = text.strip().split("\n")
        current_title = ""
        current_url = ""
        current_snippet = ""
        in_highlights = False

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("Title: "):
                if current_url:
                    results.append(SearchResult(
                        url=current_url,
                        title=current_title or "Untitled",
                        snippet=current_snippet.strip()
                    ))
                current_title = line[7:].strip()
                current_url = ""
                current_snippet = ""
                in_highlights = False
                continue

            if line.startswith("URL: "):
                current_url = line[5:].strip()
                continue

            if line == "Highlights:":
                in_highlights = True
                continue

            if line.startswith(("Published:", "Author:", "ID:", "Score:")):
                continue

            if in_highlights and current_url:
                if current_snippet:
                    current_snippet += " "
                current_snippet += line

        if current_url:
            results.append(SearchResult(
                url=current_url,
                title=current_title or "Untitled",
                snippet=current_snippet.strip()
            ))

        return results

    def _parse_simple_format(self, text: str) -> List[SearchResult]:
        results: List[SearchResult] = []
        blocks = text.strip().split("\n\n")

        for block in blocks:
            lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
            if len(lines) >= 2 and lines[1].startswith("http"):
                results.append(SearchResult(
                    url=lines[1],
                    title=lines[0],
                    snippet=" ".join(lines[2:]) if len(lines) > 2 else ""
                ))

        return results

    async def close(self) -> None:
        if self._owns_client and self._http_client:
            await self._http_client.aclose()
            self._http_client = None

"""MCP stdio 传输：换行分隔 JSON-RPC（零新增依赖）。

约定（与 MCP stdio 传输一致）：
  * 一行一条 JSON-RPC 消息；stdout **只写协议消息**，日志/异常一律走 stderr；
  * 空行忽略；解析失败回 -32700（id=null）；
  * 通知（无 id）不写响应。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any, TextIO

from backend.mcp.server import McpServer

logger = logging.getLogger(__name__)


async def serve_async(server: McpServer, stdin: TextIO, stdout: TextIO) -> int:
    """逐行读取并处理；返回处理过的请求数（便于测试）。"""
    handled = 0
    while True:
        line = await asyncio.to_thread(stdin.readline)
        if line == "":
            break
        line = line.strip()
        if not line:
            continue
        try:
            message: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            response: dict | None = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"JSON 解析失败: {exc.msg}"},
            }
        else:
            response = await server.handle_message(message)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
            handled += 1
    return handled


def serve_stdio(server: McpServer, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """同步入口：阻塞式服务一个 stdio 会话。"""
    return asyncio.run(serve_async(server, stdin or sys.stdin, stdout or sys.stdout))

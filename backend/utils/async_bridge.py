"""
async/sync 桥接工具。

在同步上下文中安全调用 async 协程。
仅用于纯 I/O 操作（数据库读写），不涉及 PyTorch/GPU 推理。
嵌入编码请使用 Embedder.encode_sync() + ThreadPoolExecutor 直接调用。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import TypeVar

from backend.config import PIPELINE_TIMEOUT_SYNC

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async_in_thread(coro, timeout: int = PIPELINE_TIMEOUT_SYNC) -> T | None:
    """在线程池中运行 async 协程。

    检测运行环境：
    1. 无事件循环 → 直接 asyncio.run(安全，纯 I/O)
    2. 有事件循环但未运行 → 直接 asyncio.run
    3. 事件循环正在运行(FastAPI) → ThreadPoolExecutor 中 asyncio.run

    注意：不适用于涉及 PyTorch 的协程（SentenceTransformer 等），
    因为 asyncio.run() 在非主线程中清理 PyTorch 线程池时会卡死。
    这类场景请使用 Embedder.encode_sync() + ThreadPoolExecutor 直接调用。

    Args:
        coro: 协程对象
        timeout: 超时秒数（默认从 config 读取，30s）

    Returns:
        协程返回值，超时返回 None
    """
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return asyncio.run(coro)

    if not loop.is_running():
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            logger.error("run_async_in_thread timed out after %ds", timeout)
            return None

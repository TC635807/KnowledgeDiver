"""
嵌入模型封装，全局单例。

使用 bge-small-zh-v1.5 将文本转为 512 维向量。
首次调用时加载模型到内存（~500ms），后续调用 ~10ms/条。
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "bge-small-zh-v1.5"
_MODEL_NAME = str(_MODEL_DIR) if _MODEL_DIR.exists() else "BAAI/bge-small-zh-v1.5"

# 模型推理锁：encode()（事件循环线程）与 encode_sync()（worker 线程）可能并发调用
# 同一模型实例，torch 内部线程池竞争会偶发死锁。串行化模型调用消除竞争。
_model_lock = threading.Lock()


class Embedder:
    """嵌入模型单例。

    Usage::

        embedder = Embedder.get()
        vec = await embedder.encode_one("什么是机器学习")
        vecs = await embedder.encode(["文本1", "文本2"])
    """

    _instance: Optional["Embedder"] = None
    _model: "SentenceTransformer | None" = None

    @classmethod
    def get(cls) -> "Embedder":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_model(self) -> "SentenceTransformer":
        if self._model is not None:
            return self._model
        # 加载与推理共用同一把锁：多个线程并发首次加载会各载一遍模型
        # （浪费 + 内存翻倍），且 transformers 加载竞态会触发 meta tensor 错误。
        with _model_lock:
            if self._model is not None:  # 双重检查：等待锁期间可能已被其他线程加载
                return self._model
            from sentence_transformers import SentenceTransformer

            logger.info("正在加载嵌入模型: %s", _MODEL_NAME)
            self._model = SentenceTransformer(_MODEL_NAME)
            logger.info("嵌入模型加载完成")
            return self._model

    def _encode_blocking(self, texts: list[str]) -> list[list[float]]:
        model = self._load_model()
        with _model_lock:
            return model.encode(
                texts,
                normalize_embeddings=True,
                batch_size=32,
                show_progress_bar=False,
            ).tolist()

    async def encode(self, texts: list[str]) -> list[list[float]]:
        start = time.perf_counter()
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            None,
            lambda: self._encode_blocking(texts),
        )
        elapsed = time.perf_counter() - start
        logger.info("编码 %d 条文本, 耗时 %.1fms (%.1fms/条)", len(texts), elapsed * 1000, elapsed * 1000 / len(texts))
        return embeddings

    async def encode_one(self, text: str) -> list[float]:
        results = await self.encode([text])
        return results[0]

    def encode_sync(self, texts: list[str]) -> list[list[float]]:
        """同步编码，供 ThreadPoolExecutor 直接调用。

        与 encode() 功能相同，但不经过 asyncio 事件循环。
        避免 asyncio.run() + PyTorch 在非主线程中清理线程池时卡死。

        Usage::

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(embedder.encode_sync, texts)
                embeddings = future.result(timeout=30)
        """
        return self._encode_blocking(texts)

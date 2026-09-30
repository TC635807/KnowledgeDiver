from __future__ import annotations

import concurrent.futures
import logging
import math
from typing import Dict, List, Optional

from backend.config import PIPELINE_TIMEOUT_SYNC
from backend.models.card import Card

logger = logging.getLogger(__name__)


class QualityProvider:
    def __init__(self, card_store, raw_store=None):
        self._store = card_store
        self._raw_store = raw_store
        self._scores: Dict[str, dict] = {}
        self._embeddings: Dict[str, List[float]] = {}
        self._known_ids: set[str] = set()
        self._loaded: bool = False
        self._cluster_report: Optional[dict] = None

    def _ensure_fresh(self) -> None:
        try:
            current_cards = self._store.list_cards()
        except Exception as e:
            logger.warning("QualityProvider: list_cards failed: %s", e)
            return

        current_ids = {c.id for c in current_cards}

        if self._loaded and current_ids == self._known_ids:
            return

        new_cards = [c for c in current_cards if c.id not in self._embeddings]
        if new_cards:
            logger.info(
                "QualityProvider: encoding %d new cards (cached=%d)",
                len(new_cards), len(self._embeddings),
            )
            new_embs = _load_embeddings(new_cards)
            if new_embs:
                self._embeddings.update(new_embs)

        deleted = self._known_ids - current_ids
        for cid in deleted:
            self._embeddings.pop(cid, None)
            self._scores.pop(cid, None)

        self._known_ids = current_ids
        self._loaded = True
        self._cluster_report = None  # 卡片集合变化 → 簇报告失效

        if self._embeddings:
            self._scores = _compute_scores(current_cards, self._embeddings, raw_store=self._raw_store)

    def enrich(self, card: Card) -> dict:
        self._ensure_fresh()
        d = card.model_dump(mode="json")
        d["quality"] = self._scores.get(card.id, {})
        return d

    def enrich_all(self, cards: List[Card]) -> List[dict]:
        return [self.enrich(c) for c in cards]

    def get_all_scores(self) -> List[dict]:
        self._ensure_fresh()
        scored = list(self._scores.values())
        scored.sort(key=lambda s: s.get("gap_score", 0), reverse=True)
        return scored

    def get_score(self, card_id: str) -> Optional[dict]:
        """返回单张卡片的评分数据，未缓存时返回 None。"""
        self._ensure_fresh()
        return self._scores.get(card_id)

    def get_cluster_report(self) -> dict:
        """返回全库簇级评估报告（语义聚类 + 簇级 gap 聚合），惰性计算并缓存。

        卡片集合变化时由 _ensure_fresh 自动失效。
        """
        self._ensure_fresh()
        if self._cluster_report is not None:
            return self._cluster_report

        empty = {
            "n_cards": 0, "n_clusters": 0, "n_undercovered": 0,
            "median_avg_gap": None, "clusters": [], "undercovered": [],
        }
        try:
            cards = self._store.list_cards()
        except Exception as e:
            logger.warning("QualityProvider.get_cluster_report: list_cards failed: %s", e)
            return empty
        if not cards or not self._embeddings:
            return empty

        from backend.quality.cluster import cluster_cards

        self._cluster_report = cluster_cards(cards, self._embeddings, self._scores)
        return self._cluster_report

    def invalidate(self) -> None:
        self._loaded = False
        self._known_ids = set()
        self._cluster_report = None

    def invalidate_card(self, card_id: str) -> None:
        self._embeddings.pop(card_id, None)
        self._scores.pop(card_id, None)
        self._known_ids.discard(card_id)

    def refresh(self) -> None:
        self._ensure_fresh()

    def get_centroid(self) -> Optional[List[float]]:
        self._ensure_fresh()
        if not self._embeddings:
            return None
        dim = len(next(iter(self._embeddings.values())))
        centroid = [0.0] * dim
        for emb in self._embeddings.values():
            for i in range(dim):
                centroid[i] += emb[i]
        n = len(self._embeddings)
        return [round(x / n, 6) for x in centroid]

    def get_spread(self) -> Optional[float]:
        self._ensure_fresh()
        if len(self._embeddings) < 2:
            return None
        centroid = self.get_centroid()
        if centroid is None:
            return None
        total = 0.0
        for emb in self._embeddings.values():
            d2 = sum((emb[i] - centroid[i]) ** 2 for i in range(len(centroid)))
            total += d2
        return round(math.sqrt(total / len(self._embeddings)), 4)

    def distance_to_centroid(self, card_id: str) -> Optional[float]:
        self._ensure_fresh()
        emb = self._embeddings.get(card_id)
        centroid = self.get_centroid()
        if emb is None or centroid is None:
            return None
        d2 = sum((emb[i] - centroid[i]) ** 2 for i in range(len(centroid)))
        return round(math.sqrt(d2), 4)


def _compute_scores(
    cards: List[Card],
    embeddings: Dict[str, List[float]],
    raw_store: Any = None,
) -> Dict[str, dict]:
    from backend.quality.scorer import score_all_cards

    scored = score_all_cards(cards, embeddings, raw_store=raw_store)
    return {s["card_id"]: s for s in scored}


def _load_embeddings(cards: List[Card]) -> Optional[Dict[str, List[float]]]:
    """增量加载嵌入向量。

    使用 Embedder.encode_sync() + ThreadPoolExecutor 直接编码，
    绕过 asyncio.run() 避免 PyTorch 在非主线程事件循环清理时卡死。
    """
    try:
        from backend.ai.embedder import Embedder

        embedder = Embedder.get()
        texts = [f"{c.title}\n{c.content}" for c in cards]

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(embedder.encode_sync, texts)
            try:
                embeddings = future.result(timeout=PIPELINE_TIMEOUT_SYNC)
            except concurrent.futures.TimeoutError:
                logger.error(
                    "QualityProvider._load_embeddings timed out after %ds",
                    PIPELINE_TIMEOUT_SYNC,
                )
                future.cancel()
                return None
        finally:
            # wait=False：worker 线程若卡死（torch 偶发死锁），shutdown(wait=True)
            # 会永久阻塞事件循环（_search_keyword 同步调用 refresh 的路径）。
            # 不等待线程，最坏情况泄漏一个 worker，主流程不卡死。
            pool.shutdown(wait=False)

        return {cards[i].id: embeddings[i] for i in range(len(cards))}
    except Exception as e:
        logger.warning("QualityProvider._load_embeddings failed: %s", e)
        return None

"""簇级知识库评估 — 语义聚类 + 簇级 gap 聚合（整体评估第 1 层）。

把全库卡片按嵌入语义聚成主题簇，输出每簇的聚合质量指标
（size / avg_gap / worst_gap / compactness / 链接密度 / 维度画像），
以及"覆盖不足"卡清单（未能成簇的孤立卡与微簇卡）。

单卡评分回答"每张卡好不好"；本模块回答"知识库整体覆盖得全不全"——
这是两个正交的质量维度（Razniewski et al. 综述：completeness 与 correctness 正交）。

算法：HDBSCAN（密度聚类，metric=cosine）。
- min_cluster_size=4 ⇔ "簇是至少 4 张相关卡的主题域"，<4 张卡不成簇 → 进 undercovered
- 噪声点概念天然对应"孤立卡 = 疑似未覆盖主题"
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
from sklearn.cluster import HDBSCAN

from backend.models.card import Card
# 簇级阈值统一来源：backend/quality/thresholds.py（原先在本文件硬编码）。
from backend.quality.thresholds import (
    CLUSTER_FRAGMENTED_DIST as _FRAGMENTED_DIST,
    CLUSTER_LOW_LINK_DENSITY as _LOW_LINK_DENSITY,
    CLUSTER_MIN_SIZE as _MIN_CLUSTER_SIZE,
    CLUSTER_STATUS_WEIGHT as _STATUS_WEIGHT,
    CLUSTER_WEAK_GAP as _WEAK_GAP,
)


def _distance_matrix(embeddings: Dict[str, List[float]]) -> Tuple[List[str], np.ndarray]:
    ids = list(embeddings.keys())
    if not ids:
        return [], np.zeros((0, 0), dtype=np.float32)
    mat = np.asarray([embeddings[i] for i in ids], dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    sim = np.clip(mat @ mat.T, -1.0, 1.0)
    dist = 1.0 - sim
    np.fill_diagonal(dist, 0.0)
    return ids, dist


def _classify(avg_gap: Optional[float], compactness: Optional[float]) -> str:
    flags = []
    if avg_gap is not None and avg_gap >= _WEAK_GAP:
        flags.append("weak")
    if compactness is not None and compactness > _FRAGMENTED_DIST:
        flags.append("fragmented")
    return "+".join(flags) if flags else "healthy"


def _priority(status: str) -> int:
    return sum(_STATUS_WEIGHT.get(part, 0) for part in status.split("+"))


def cluster_cards(
    cards: List[Card],
    embeddings: Dict[str, List[float]],
    scores: Optional[Dict[str, dict]] = None,
) -> dict:
    """对全库卡片做语义聚类并返回簇级评估报告。

    scores: card_id -> 单卡评分 dict（含 gap_score / dimensions），
            来自 QualityProvider，缺失时按 0.5 退化。
    """
    scores = scores or {}
    cards_by_id = {c.id: c for c in cards}
    ids, dist = _distance_matrix(embeddings)
    n = len(ids)

    empty = {
        "n_cards": n,
        "n_clusters": 0,
        "n_undercovered": 0,
        "median_avg_gap": None,
        "clusters": [],
        "undercovered": [],
    }
    if n < _MIN_CLUSTER_SIZE:
        empty["n_undercovered"] = n
        empty["undercovered"] = [
            {
                "card_id": cid,
                "title": cards_by_id[cid].title if cid in cards_by_id else cid,
                "gap_score": scores.get(cid, {}).get("gap_score", 0.5),
                "cluster_size": 1,
            }
            for cid in ids
        ]
        return empty

    model = HDBSCAN(
        min_cluster_size=_MIN_CLUSTER_SIZE, min_samples=2,
        metric="precomputed", copy=False,
    )
    labels = model.fit_predict(dist)

    groups: Dict[int, List[str]] = {}
    for i, cid in enumerate(ids):
        groups.setdefault(int(labels[i]), []).append(cid)

    clusters: List[dict] = []
    undercovered: List[dict] = []
    avg_gaps: List[float] = []

    for cids in groups.values():
        if len(cids) < _MIN_CLUSTER_SIZE:
            for cid in sorted(cids, key=lambda c: scores.get(c, {}).get("gap_score", 0.5), reverse=True):
                undercovered.append({
                    "card_id": cid,
                    "title": cards_by_id[cid].title if cid in cards_by_id else cid,
                    "gap_score": scores.get(cid, {}).get("gap_score", 0.5),
                    "cluster_size": len(cids),
                })
            continue

        idx = [i for i, cid in enumerate(ids) if cid in set(cids)]
        size = len(cids)
        sub = dist[np.ix_(idx, idx)]

        gaps = [scores.get(c, {}).get("gap_score", 0.5) for c in cids]
        avg_gap = float(np.mean(gaps))
        worst_gap = float(np.max(gaps))
        avg_gaps.append(avg_gap)

        compactness = float(sub.sum() / (size * size - size)) if size > 1 else None

        link_pairs = set()
        for c in cids:
            card = cards_by_id.get(c)
            for lid in (card.links or []) if card else []:
                if lid in cids and lid != c:
                    link_pairs.add(tuple(sorted((c, lid))))
        max_pairs = size * (size - 1) / 2.0
        link_density = len(link_pairs) / max_pairs if max_pairs > 0 else None

        dims = {"content": 0.5, "sources": 0.5, "links": 0.5}
        dim_vals = [scores.get(c, {}).get("dimensions", {}) for c in cids]
        for key in dims:
            vals = [d.get(key) for d in dim_vals if isinstance(d, dict) and d.get(key) is not None]
            if vals:
                dims[key] = float(np.mean(vals))

        status = _classify(avg_gap, compactness)
        clusters.append({
            "cluster_id": len(clusters),
            "size": size,
            "card_ids": cids,
            "titles": [cards_by_id[c].title if c in cards_by_id else c for c in cids],
            "avg_gap": round(avg_gap, 4),
            "worst_gap": round(worst_gap, 4),
            "compactness": round(compactness, 4) if compactness is not None else None,
            "link_density": round(link_density, 4) if link_density is not None else None,
            "link_density_low": link_density is not None and link_density < _LOW_LINK_DENSITY,
            "dims": {k: round(v, 4) for k, v in dims.items()},
            "status": status,
            "priority": _priority(status),
        })

    clusters.sort(key=lambda c: (-c["priority"], c["size"]))
    undercovered.sort(key=lambda u: u["gap_score"], reverse=True)

    return {
        "n_cards": n,
        "n_clusters": len(clusters),
        "n_undercovered": len(undercovered),
        "median_avg_gap": round(float(np.median(avg_gaps)), 4) if avg_gaps else None,
        "clusters": clusters,
        "undercovered": undercovered,
    }


def _self_check() -> None:
    import uuid
    from datetime import datetime

    base = datetime(2026, 1, 1)

    def make(angle_deg: float, jitter: float, count: int, gap: float) -> Tuple[List[Card], List[float]]:
        rng = np.random.default_rng(42)
        cards = []
        angles = []
        for k in range(count):
            ang = angle_deg + rng.uniform(-jitter, jitter)
            cards.append(Card(
                id=str(uuid.uuid4()), title=f"t-{angle_deg:.0f}-{k}",
                content="x" * 800, metadata={}, links=[], backlinks=[],
                created_at=base, updated_at=base, sources=["s1"], confidence=0.5,
            ))
            angles.append(ang)
        return cards, angles

    groups = [
        (make(0, 1.5, 8, 0.2), 0.2),    # 健康簇 A
        (make(40, 1.5, 8, 0.2), 0.2),   # 健康簇 B
        (make(80, 1.5, 8, 0.2), 0.2),   # 健康簇 C
        (make(140, 1.5, 6, 0.6), 0.6),  # weak 簇
        (make(200, 0.8, 2, 0.2), 0.2),  # 微簇：仅 2 卡，远离其他主题
        (make(260, 0.5, 1, 0.15), 0.15),  # 孤立卡
    ]

    all_cards: List[Card] = []
    embeddings = {}
    scores = {}
    for (cards, angles), gap in groups:
        for c, ang in zip(cards, angles):
            all_cards.append(c)
            embeddings[c.id] = [float(np.cos(np.deg2rad(ang))), float(np.sin(np.deg2rad(ang))), 0.5]
            dims_val = 0.8 if gap < 0.5 else 0.4
            scores[c.id] = {
                "gap_score": gap,
                "dimensions": {"content": dims_val, "sources": dims_val, "links": dims_val},
            }

    report = cluster_cards(all_cards, embeddings, scores)

    assert report["n_cards"] == 33, f"n_cards={report['n_cards']}"
    assert report["n_undercovered"] == 3, f"undercovered={report['n_undercovered']}（应为微簇 2 + 孤立 1）"

    statuses = {c["status"] for c in report["clusters"]}
    assert "healthy" in statuses, f"应有 healthy 簇: {statuses}"
    weak_cluster = next(c for c in report["clusters"] if "weak" in c["status"])
    assert weak_cluster["size"] == 6 and weak_cluster["avg_gap"] >= 0.5, \
        f"weak 簇识别失败: {weak_cluster}"

    u_titles = {u["title"] for u in report["undercovered"]}
    assert any(u_titles), f"undercovered 应有微簇/孤立卡: {u_titles}"

    print(f"cluster self-check OK — {report['n_clusters']} 簇, "
          f"{report['n_undercovered']} 张覆盖不足卡, "
          f"median_avg_gap={report['median_avg_gap']}")


if __name__ == "__main__":
    _self_check()

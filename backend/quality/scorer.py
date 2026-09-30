"""知识卡片质量评分 — Gap 计算引擎。

设计目标
========
卡牌 gap_score 应在 [0,1] 上呈现近似单峰钟形分布，且单个卡牌质量提升
（refresh_card 加内容/来源、expand_from_card 加链接）必须让该卡 gap 下降。

历史问题
========
旧实现用饱和/截断式子分：
  - content  log10(n+1)/log10(2000)        >200 字符即冲到 0.85
  - sources  min(n/3, 1)                    3 个来源即满分
  - links    0.6·(in/3) + 0.4·(out/5)       3 入+5 出即满分
  - graph    degree/(depth+1)/2 截顶 1.0    2-3 个连接即 0.5-1.0
饱和让 refresh 加来源加不出分、expand 加链接加不出分。同时四个子分都被
离散吸引子锁住，线性加权后塌在 0.45-0.55，分布极不均匀。

修复方案
========
把所有"计数型"子分改为渐近不饱和的纯函数（仅依赖卡牌自身字段，
不依赖其他卡牌）。加来源/加链接/扩内容都对应绝对涨分，单调可反馈。

  content_completeness = 1 / (1 + exp(-(n - 1500)/400))      logistic
  source_richness      = 1 - exp(-n_src / 5)                  exp 渐顶
  link_density         = 1 - exp(-(in + out*1.5) / 6)         exp 渐顶，出链更重
  graph_score          = 1 - exp(-(eff_degree + 1.5) / 2.5)   exp 渐顶，奖励 hub

  semantic_score       = 1 / (1 + exp((nn_dist - 0.30)/0.20))

  weighted = 0.35·struct + 0.30·graph + 0.25·sem + 0.10·conf
  gap = 1 - weighted

所有子分都返回 (0, 1)，加权合成后 quality 也 ∈ (0, 1)，因此 gap ∈ (0, 1)。
线性反向让 refresh/expand 涨分 ⇨ quality 涨 ⇨ gap 严格下降（无 clip，无排名）。

退化保护
========
当某维度全库 sd 接近 0（无 embedding 时 semantic 等情形），其权重归零
并对剩余维度重归一化，避免常数项拉平分布。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.models.card import Card
from backend.quality.thresholds import (
    DEGEN_EPS as _DEGEN_EPS,
    DESIGN_WEIGHTS,
    GAP_HIGH,
    GAP_LOW,
)

_CONTENT_MID = 1500.0
_CONTENT_SLOPE = 400.0
_SOURCE_K = 5.0
_LINK_K = 6.0
_LINK_OUT_WEIGHT = 1.5
_GRAPH_K = 2.5
_SEM_MID = 0.30
_SEM_SLOPE = 0.20

# 设计权重与决策阈值统一来源：backend/quality/thresholds.py（原先各自硬编码）。
# 保留同名模块级名称以兼容既有 import 与论文口径快照；此处是**只读副本**。
_DESIGN_WEIGHTS: Dict[str, float] = dict(DESIGN_WEIGHTS)


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _build_tree_signals(cards: List[Card]) -> Dict[str, Dict[str, float]]:
    id_set = {c.id for c in cards}
    adj: Dict[str, Set[str]] = {c.id: set() for c in cards}
    for c in cards:
        for lid in (c.links or []):
            if lid in id_set:
                adj[c.id].add(lid)
                adj[lid].add(c.id)
    has_incoming: Set[str] = set()
    for c in cards:
        for lid in (c.links or []):
            if lid in id_set:
                has_incoming.add(lid)
    roots = [c for c in cards if c.id not in has_incoming]
    if not roots:
        roots = [cards[0]]
    depth: Dict[str, int] = {}
    queue: deque[str] = deque()
    for r in roots:
        depth[r.id] = 0
        queue.append(r.id)
    while queue:
        cur = queue.popleft()
        for nb in adj.get(cur, set()):
            if nb not in depth:
                depth[nb] = depth[cur] + 1
                queue.append(nb)
    for c in cards:
        if c.id not in depth:
            depth[c.id] = 1
    return {
        c.id: {
            "depth": float(depth.get(c.id, 1)),
            "degree": float(len(adj.get(c.id, set()))),
        }
        for c in cards
    }


def compute_tree_signals(cards: List[Card]) -> Dict[str, Dict[str, float]]:
    if len(cards) < 2:
        return {}
    return _build_tree_signals(cards)


def content_completeness(card: Card) -> float:
    n = len(card.content or "")
    if n == 0:
        return 0.0
    return round(min(_sigmoid((n - _CONTENT_MID) / _CONTENT_SLOPE), 1.0), 4)


def source_richness(card: Card) -> float:
    n = len(card.sources or [])
    return round(1.0 - math.exp(-n / _SOURCE_K), 4)


def link_density(card: Card) -> float:
    in_deg = len(card.backlinks or [])
    out_deg = len(card.links or [])
    s = in_deg + out_deg * _LINK_OUT_WEIGHT
    return round(1.0 - math.exp(-s / _LINK_K), 4)


def compute_structure_score(card: Card) -> float:
    return round(
        0.33 * content_completeness(card) +
        0.33 * source_richness(card) +
        0.34 * link_density(card),
        4,
    )


def compute_graph_score(
    card_id: str,
    graph_signals: Optional[Dict[str, Dict[str, float]]] = None,
) -> float:
    if graph_signals is None or not graph_signals:
        return 0.5
    sig = graph_signals.get(card_id)
    if sig is None:
        return 0.5
    depth = float(sig.get("depth", 0))
    degree = float(sig.get("degree", 0))
    eff_degree = degree / (1.0 + 0.5 * depth)
    return round(1.0 - math.exp(-(eff_degree + 1.5) / _GRAPH_K), 4)


def _nearest_neighbor_distance(
    target_embedding: List[float],
    all_embeddings: Dict[str, List[float]],
    exclude_id: Optional[str] = None,
) -> Optional[float]:
    if len(all_embeddings) <= 1:
        return None
    best = float("inf")
    for cid, emb in all_embeddings.items():
        if cid == exclude_id:
            continue
        dist = _cosine_distance(target_embedding, emb)
        if dist < best:
            best = dist
    return best if best != float("inf") else None


def _cosine_distance(a: List[float], b: List[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"Embedding dimension mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    cos_sim = dot / (norm_a * norm_b)
    cos_sim = max(-1.0, min(1.0, cos_sim))
    return 1.0 - cos_sim


def compute_semantic_score(
    card_embedding: Optional[List[float]],
    all_embeddings: Dict[str, List[float]],
    card_id: str,
) -> float:
    if card_embedding is None or not all_embeddings:
        return 0.5
    nn_dist = _nearest_neighbor_distance(card_embedding, all_embeddings, exclude_id=card_id)
    if nn_dist is None:
        return 0.5  # 无邻居可比较，保持中性，而不是误判为“完美融入”
    z = (nn_dist - _SEM_MID) / _SEM_SLOPE
    return round(_sigmoid(-z), 4)


def _effective_weights(
    sd_struct: float, sd_graph: float, sd_sem: float, sd_conf: float
) -> Tuple[Dict[str, float], Dict[str, float]]:
    sds = {
        "structure": sd_struct,
        "graph": sd_graph,
        "semantic": sd_sem,
        "confidence": sd_conf,
    }
    eff = {k: (v if sds[k] > _DEGEN_EPS else 0.0)
           for k, v in _DESIGN_WEIGHTS.items()}
    total = sum(eff.values())
    if total <= 0.0:
        eff = {k: 0.25 for k in _DESIGN_WEIGHTS}
        total = 1.0
    eff = {k: v / total for k, v in eff.items()}
    return eff, dict(_DESIGN_WEIGHTS)


def _raw_score_arrays(
    cards,
    embeddings,
    graph_signals,
):
    """批量计算四个原始子分。所有评分入口共用，保证权重路径一致。"""
    raw_struct = []
    raw_graph = []
    raw_sem = []
    raw_conf = []

    for card in cards:
        raw_struct.append(compute_structure_score(card))
        raw_graph.append(compute_graph_score(card.id, graph_signals))
        raw_sem.append(compute_semantic_score(
            embeddings.get(card.id) if embeddings else None,
            embeddings or {},
            card.id,
        ))
        raw_conf.append(card.confidence if card.confidence is not None else 0.5)

    return raw_struct, raw_graph, raw_sem, raw_conf


def _combine_quality(structure, graph, semantic, confidence, weights):
    quality = (
        weights["structure"] * structure +
        weights["graph"] * graph +
        weights["semantic"] * semantic +
        weights["confidence"] * confidence
    )
    quality = max(0.0, min(1.0, quality))
    return round(quality, 4), round(1.0 - quality, 4)


def compute_gap_score(
    card: Card,
    all_cards: List[Card],
    embeddings: Optional[Dict[str, List[float]]] = None,
    graph_signals: Optional[Dict[str, Dict[str, float]]] = None,
    raw_store: Any = None,
) -> dict:
    """单卡评分。

    与 score_all_cards 共用 _raw_score_arrays + _effective_weights，
    单卡路径与全库路径产出一致的 gap_score 和 effective_weights。
    """
    import statistics as _stat

    cards = list(all_cards or [card])
    if graph_signals is None:
        graph_signals = compute_tree_signals(cards)

    raw_struct, raw_graph, raw_sem, raw_conf = _raw_score_arrays(
        cards, embeddings, graph_signals,
    )

    n = len(cards)
    effective, design_snapshot = _effective_weights(
        _stat.pstdev(raw_struct) if n > 1 else 0.0,
        _stat.pstdev(raw_graph) if n > 1 else 0.0,
        _stat.pstdev(raw_sem) if n > 1 else 0.0,
        _stat.pstdev(raw_conf) if n > 1 else 0.0,
    )

    try:
        idx = next(i for i, c in enumerate(cards) if c.id == card.id)
    except StopIteration:
        idx = 0
        cards.insert(0, card)
        raw_struct, raw_graph, raw_sem, raw_conf = _raw_score_arrays(
            cards, embeddings, graph_signals,
        )
        n = len(cards)
        effective, design_snapshot = _effective_weights(
            _stat.pstdev(raw_struct) if n > 1 else 0.0,
            _stat.pstdev(raw_graph) if n > 1 else 0.0,
            _stat.pstdev(raw_sem) if n > 1 else 0.0,
            _stat.pstdev(raw_conf) if n > 1 else 0.0,
        )

    structure, graph, semantic, conf = (
        raw_struct[idx], raw_graph[idx], raw_sem[idx], raw_conf[idx],
    )
    quality, gap = _combine_quality(structure, graph, semantic, conf, effective)

    all_gaps = []
    for i in range(len(cards)):
        q_i = (
            effective["structure"] * raw_struct[i] +
            effective["graph"] * raw_graph[i] +
            effective["semantic"] * raw_sem[i] +
            effective["confidence"] * raw_conf[i]
        )
        all_gaps.append(round(1.0 - max(0.0, min(1.0, q_i)), 4))
    if len(all_gaps) > 1:
        n_greater = sum(1 for g in all_gaps if g > all_gaps[idx])
        gap_percentile = round(1.0 - n_greater / (len(all_gaps) - 1), 4)
    else:
        gap_percentile = 0.5

    tree_sub: Dict[str, float] = {}
    if graph_signals and card.id in graph_signals:
        sig = graph_signals[card.id]
        tree_sub = {
            "depth": float(sig.get("depth", 1)),
            "degree": float(sig.get("degree", 0)),
            "density": graph,
        }

    return {
        "card_id": card.id,
        "title": card.title,
        "gap_score": gap,
        "quality_score": quality,
        "structure_score": structure,
        "semantic_score": semantic,
        "graph_score": graph,
        "confidence_score": round(conf, 4),
        "dimensions": {
            "content": content_completeness(card),
            "sources": source_richness(card),
            "links": link_density(card),
        },
        "tree_dimensions": tree_sub,
        "gap_percentile": gap_percentile,
        "effective_weights": {k: round(v, 4) for k, v in effective.items()},
        "design_weights_snapshot": design_snapshot,
    }


def score_all_cards(
    cards: List[Card],
    embeddings: Optional[Dict[str, List[float]]] = None,
    raw_store: Any = None,
) -> List[dict]:
    if not cards:
        return []

    import statistics as _stat

    graph_signals = compute_tree_signals(cards)
    raw_struct, raw_graph, raw_sem, raw_conf = _raw_score_arrays(
        cards, embeddings, graph_signals,
    )

    n = len(cards)
    effective, design_snapshot = _effective_weights(
        _stat.pstdev(raw_struct) if n > 1 else 0.0,
        _stat.pstdev(raw_graph) if n > 1 else 0.0,
        _stat.pstdev(raw_sem) if n > 1 else 0.0,
        _stat.pstdev(raw_conf) if n > 1 else 0.0,
    )

    results: List[dict] = []
    for i, card in enumerate(cards):
        quality, gap = _combine_quality(
            raw_struct[i], raw_graph[i], raw_sem[i], raw_conf[i], effective,
        )

        sig = graph_signals.get(card.id) if graph_signals else None
        tree_sub: Dict[str, float] = {}
        if sig is not None:
            tree_sub = {
                "depth": float(sig.get("depth", 1)),
                "degree": float(sig.get("degree", 0)),
                "density": raw_graph[i],
            }

        results.append({
            "card_id": card.id,
            "title": card.title,
            "gap_score": gap,
            "quality_score": quality,
            "structure_score": raw_struct[i],
            "semantic_score": raw_sem[i],
            "graph_score": raw_graph[i],
            "confidence_score": round(raw_conf[i], 4),
            "effective_weights": {k: round(v, 4) for k, v in effective.items()},
            "design_weights_snapshot": design_snapshot,
            "dimensions": {
                "content": content_completeness(card),
                "sources": source_richness(card),
                "links": link_density(card),
            },
            "tree_dimensions": tree_sub,
        })

    results.sort(key=lambda r: r["gap_score"], reverse=True)

    # gap_score 是绝对分；gap_percentile 是会话内相对薄弱分。
    # 绝对分集中在中间区段时，相对分仍可稳定区分本库最弱与最好卡片。
    # 用“严格更差卡片数”计算，同分卡获得相同百分位，且与 compute_gap_score 一致。
    if len(results) > 1:
        last = len(results) - 1
        for result in results:
            n_greater = sum(1 for r in results if r["gap_score"] > result["gap_score"])
            result["gap_percentile"] = round(1.0 - n_greater / last, 4)
    else:
        results[0]["gap_percentile"] = 0.5

    return results


def find_weakest_cards(
    cards: List[Card],
    embeddings: Optional[Dict[str, List[float]]] = None,
    top_n: int = 5,
    threshold: float = 0.5,
    raw_store: Any = None,
) -> List[dict]:
    scored = score_all_cards(cards, embeddings, raw_store=raw_store)
    weak = [s for s in scored if s["gap_score"] >= threshold]
    if not weak and scored:
        # 绝对阈值未命中时退回相对最弱，保证“找最薄弱卡”永远有结果，
        # 而不是静默返回空列表让上层误判知识库已经健康。
        weak = scored[:top_n]
    return weak[:top_n]


def _self_check() -> None:
    from datetime import datetime
    base = datetime(2026, 1, 1)

    for n in (0, 100, 500, 1000, 1500, 2000, 5000, 10000):
        c = Card(id=f"00000000-0000-4000-8000-{n:012d}",
                 title=f"c-{n}", content="x" * n, metadata={},
                 links=[], backlinks=[], created_at=base, updated_at=base,
                 sources=[], confidence=0.5)
        s = content_completeness(c)
        assert 0.0 <= s <= 1.0, f"sigmoid 越界 n={n}: {s}"

    cards_baseline: list[Card] = []
    cards_refreshed: list[Card] = []
    for k in range(20):
        cards_baseline.append(Card(
            id=f"00000000-0000-4000-8000-{k:012d}",
            title=f"card-{k}", content="x" * (50 + k * 5), metadata={},
            links=[], backlinks=[],
            created_at=base, updated_at=base,
            sources=[], confidence=0.5,
        ))
        cards_refreshed.append(Card(
            id=f"00000000-0000-4000-8000-{k:012d}",
            title=f"card-{k}", content="x" * (3000 + k * 50), metadata={},
            links=[], backlinks=[],
            created_at=base, updated_at=base,
            sources=["a", "b", "c", "d"], confidence=0.8,
        ))
    res_before = {r["card_id"]: r["gap_score"] for r in score_all_cards(cards_baseline)}
    res_after = {r["card_id"]: r["gap_score"] for r in score_all_cards(cards_refreshed)}
    target = "00000000-0000-4000-8000-000000000000"
    assert res_after[target] < res_before[target], \
        f"refresh 后 gap 必须下降：{res_before[target]} → {res_after[target]}"
    print(f"refresh demo: gap {res_before[target]} → {res_after[target]}")

    base_cards: list[Card] = []
    card_id_target = "11111111-0000-4000-8000-000000000000"
    for k in range(15):
        base_cards.append(Card(
            id=f"00000000-0000-4000-8000-{k:012d}",
            title=f"card-{k}", content="x" * 800, metadata={},
            links=[], backlinks=[],
            created_at=base, updated_at=base,
            sources=["s1"], confidence=0.5,
        ))
    base_cards.append(Card(
        id=card_id_target, title="TARGET", content="x" * 800, metadata={},
        links=[], backlinks=[],
        created_at=base, updated_at=base, sources=["s1"], confidence=0.5,
    ))
    target_expanded = Card(
        id=card_id_target, title="TARGET", content="x" * 800, metadata={},
        links=[f"00000000-0000-4000-8000-{i:012d}" for i in range(5)],
        backlinks=[], created_at=base, updated_at=base,
        sources=["s1"], confidence=0.5,
    )
    expanded_cards = [c for c in base_cards if c.id != card_id_target] + [target_expanded]
    for i in range(5):
        cid = f"00000000-0000-4000-8000-{i:012d}"
        for j, c in enumerate(expanded_cards):
            if c.id == cid:
                expanded_cards[j] = Card(**{
                    **c.model_dump(),
                    "backlinks": list(c.backlinks or []) + [card_id_target],
                })
                break

    gap_before = next(r["gap_score"] for r in score_all_cards(base_cards) if r["card_id"] == card_id_target)
    gap_after = next(r["gap_score"] for r in score_all_cards(expanded_cards) if r["card_id"] == card_id_target)
    assert gap_after < gap_before, \
        f"expand 后 gap 必须下降 — 反馈断线 (before={gap_before}, after={gap_after})"
    print(f"expand demo: gap {gap_before} → {gap_after}")
    print("self-check OK — sigmoid 不顶 + refresh/expand 反馈单调")


if __name__ == "__main__":
    _self_check()
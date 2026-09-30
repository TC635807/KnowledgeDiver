"""质量评分系统一致性回归测试。

锁定：
- compute_gap_score（单卡路径）与 score_all_cards（全库路径）完全一致
- gap_percentile 在 [0,1]，最弱为 1.0，最好为 0.0，与 gap 排名单调
- 单卡无邻居时 semantic_score 为 0.5（无法评估），不误判为满分
- find_weakest_cards 在绝对阈值未命中时仍返回相对最弱 top_n
"""

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.models.card import Card  # noqa: E402
from backend.quality.scorer import (  # noqa: E402
    compute_gap_score,
    find_weakest_cards,
    score_all_cards,
)


def _card(i: int, content_len: int, n_sources: int, n_links: int, confidence: float) -> Card:
    t = datetime(2026, 8, 15, 10, 0, 0, tzinfo=timezone.utc)
    return Card(
        id=f"00000000-0000-4000-8000-{i:012d}",
        title=f"卡片{i}",
        content="x" * content_len,
        metadata={},
        links=[f"00000000-0000-4000-8000-{(i + j) % 6:012d}" for j in range(n_links)],
        backlinks=[],
        sources=[f"https://example.com/{i}/{j}" for j in range(n_sources)],
        created_at=t,
        updated_at=t,
        confidence=confidence,
        tags=[],
        parent_id=None,
    )


def _cards():
    return [
        _card(0, 300, 0, 0, 0.2),
        _card(1, 800, 1, 0, 0.5),
        _card(2, 1500, 2, 1, 0.7),
        _card(3, 3000, 3, 2, 0.9),
        _card(4, 6000, 5, 3, 1.0),
    ]


def _embeddings():
    # 4 维假向量，保证语义维度有方差且数值稳定
    return {
        c.id: [0.1 * i, 0.2 * i, 0.3 * i, 0.4 * i]
        for i, c in enumerate(_cards())
    }


def test_single_card_path_matches_batch_path():
    cards = _cards()
    embeddings = _embeddings()
    batch = {r["card_id"]: r for r in score_all_cards(cards, embeddings)}
    for card in cards:
        single = compute_gap_score(card, cards, embeddings)
        batched = batch[card.id]
        for key in [
            "gap_score", "quality_score", "structure_score", "semantic_score",
            "graph_score", "confidence_score", "gap_percentile",
        ]:
            assert single[key] == batched[key], f"{card.title} {key}: {single[key]} != {batched[key]}"


def test_gap_percentile_is_rank_monotonic():
    cards = _cards()
    results = score_all_cards(cards, _embeddings())
    assert results[0]["gap_percentile"] == 1.0
    assert results[-1]["gap_percentile"] == 0.0
    gaps = [r["gap_score"] for r in results]
    percentiles = [r["gap_percentile"] for r in results]
    assert gaps == sorted(gaps, reverse=True)
    assert percentiles == sorted(percentiles, reverse=True)
    assert all(0.0 <= p <= 1.0 for p in percentiles)


def test_single_card_semantic_is_neutral():
    card = _card(0, 1000, 2, 0, 0.5)
    result = compute_gap_score(card, [card], {card.id: [0.1, 0.2, 0.3, 0.4]})
    assert result["semantic_score"] == 0.5
    assert result["gap_percentile"] == 0.5


def test_find_weakest_falls_back_when_threshold_unmet():
    cards = _cards()
    # 所有卡 gap < 0.7 时，0.7 绝对阈值不应导致空结果
    weak = find_weakest_cards(cards, _embeddings(), top_n=2, threshold=0.95)
    assert len(weak) == 2
    assert weak[0]["gap_score"] >= weak[1]["gap_score"]

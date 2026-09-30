"""
Card quality scoring module.

Three-layer architecture:
  Layer 1 — Structural quality (content depth, source diversity, link density)
  Layer 2 — Semantic integration (nearest-neighbor closeness, reduced weight)
  Layer 3 — Tree signals       (BFS depth, cross-branch reference count, density)

All data from existing Card fields + sqlite-vec embeddings + card link graph.
No database schema changes needed.

Quality improvement is handled by pluggable strategies in improver.py.
"""

from backend.quality.scorer import (
    GAP_LOW,
    GAP_HIGH,
    compute_structure_score,
    compute_semantic_score,
    compute_tree_signals,
    compute_graph_score,
    compute_gap_score,
    score_all_cards,
    find_weakest_cards,
)
from backend.quality.provider import QualityProvider
from backend.quality.improver import (
    ImprovementStrategy,
    SearchBasedStrategy,
    AgentBasedStrategy,
    ImprovementService,
)

__all__ = [
    "GAP_LOW",
    "GAP_HIGH",
    "compute_structure_score",
    "compute_semantic_score",
    "compute_tree_signals",
    "compute_graph_score",
    "compute_gap_score",
    "score_all_cards",
    "find_weakest_cards",
    "QualityProvider",
    "ImprovementStrategy",
    "SearchBasedStrategy",
    "AgentBasedStrategy",
    "ImprovementService",
]

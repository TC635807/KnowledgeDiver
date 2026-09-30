"""决策阈值单一来源回归测试（T14 重构④ / T13 tech_debt D1-D2）。

锁定：
- 阈值数值与重构前逐一致（实验口径不变）；
- scorer / cluster / topic_guard / tools / registry 引用的是同一份定义；
- 绝对档失效的实测结论被记录在单一来源模块里（防止再次新增绝对门槛）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent import tool_registry  # noqa: E402
from backend.agent import tools as tools_mod  # noqa: E402
from backend.quality import cluster, scorer, thresholds, topic_guard  # noqa: E402


def test_values_match_pre_refactor_literals():
    assert thresholds.DESIGN_WEIGHTS == {
        "structure": 0.35, "graph": 0.30, "semantic": 0.25, "confidence": 0.10,
    }
    assert thresholds.GAP_LOW == 0.3 and thresholds.GAP_HIGH == 0.7
    assert thresholds.DEGEN_EPS == 1e-3
    assert thresholds.REFRESH_GAP_ABS == 0.65
    assert thresholds.REFRESH_PERCENTILE_MIN == 0.90
    assert thresholds.REFRESH_STRUCTURE_MAX == 0.35
    assert thresholds.LOCAL_SUFFICIENT_PERCENTILE == 0.30
    assert thresholds.SIMILAR_RECALL_THRESHOLD == 0.25
    assert thresholds.SIMILAR_LOCAL_HIT == 0.65
    assert thresholds.SIMILAR_AUTO_LINK == 0.70
    assert thresholds.CLUSTER_MIN_SIZE == 4
    assert thresholds.CLUSTER_WEAK_GAP == 0.5
    assert thresholds.CLUSTER_FRAGMENTED_DIST == 0.35
    assert thresholds.CLUSTER_LOW_LINK_DENSITY == 0.3
    assert thresholds.CLUSTER_STATUS_WEIGHT == {"weak": 2, "fragmented": 1}
    assert thresholds.SATURATED_AVG_GAP_MAX == 0.35
    assert thresholds.SATURATION_QUANTILE == 0.5
    assert thresholds.SATURATION_MIN_CLUSTERS == 2
    assert thresholds.UNDERCOVERED_TOPIC_GAP == 1.0


def test_scorer_references_single_source():
    assert scorer.GAP_LOW == thresholds.GAP_LOW
    assert scorer.GAP_HIGH == thresholds.GAP_HIGH
    assert scorer._DEGEN_EPS == thresholds.DEGEN_EPS
    # 只读副本：值相等但不是同一对象（防止调用方写穿全局配置）
    assert scorer._DESIGN_WEIGHTS == thresholds.DESIGN_WEIGHTS
    assert scorer._DESIGN_WEIGHTS is not thresholds.DESIGN_WEIGHTS


def test_cluster_references_single_source():
    assert cluster._MIN_CLUSTER_SIZE == thresholds.CLUSTER_MIN_SIZE
    assert cluster._WEAK_GAP == thresholds.CLUSTER_WEAK_GAP
    assert cluster._FRAGMENTED_DIST == thresholds.CLUSTER_FRAGMENTED_DIST
    assert cluster._LOW_LINK_DENSITY == thresholds.CLUSTER_LOW_LINK_DENSITY
    assert cluster._STATUS_WEIGHT == thresholds.CLUSTER_STATUS_WEIGHT


def test_topic_guard_references_single_source():
    assert topic_guard.CLUSTER_MIN_SIZE == thresholds.CLUSTER_MIN_SIZE
    assert topic_guard.SATURATED_AVG_GAP_MAX == thresholds.SATURATED_AVG_GAP_MAX
    assert topic_guard.SATURATION_QUANTILE == thresholds.SATURATION_QUANTILE
    assert topic_guard.SATURATION_MIN_CLUSTERS == thresholds.SATURATION_MIN_CLUSTERS
    assert topic_guard.UNDERCOVERED_TOPIC_GAP == thresholds.UNDERCOVERED_TOPIC_GAP


def test_tools_reexport_same_values():
    """外部历史 import（from backend.agent.tools import REFRESH_GAP_ABS）继续可用。"""
    assert tools_mod.REFRESH_GAP_ABS == thresholds.REFRESH_GAP_ABS
    assert tools_mod.REFRESH_PERCENTILE_MIN == thresholds.REFRESH_PERCENTILE_MIN
    assert tools_mod.REFRESH_STRUCTURE_MAX == thresholds.REFRESH_STRUCTURE_MAX
    assert tools_mod.LOCAL_SUFFICIENT_PERCENTILE == thresholds.LOCAL_SUFFICIENT_PERCENTILE
    assert tools_mod.SIMILAR_RECALL_THRESHOLD == thresholds.SIMILAR_RECALL_THRESHOLD
    assert tools_mod.SIMILAR_LOCAL_HIT == thresholds.SIMILAR_LOCAL_HIT
    assert tools_mod.SIMILAR_AUTO_LINK == thresholds.SIMILAR_AUTO_LINK


def test_permission_constants_shared():
    assert tool_registry.TOOL_PERMISSION_MODES == thresholds.TOOL_PERMISSION_MODES
    assert "legacy" in thresholds.TOOL_PERMISSION_MODES
    assert thresholds.UNTRUSTED_WRAP_MODES == ("legacy", "on")


def test_absolute_threshold_failure_is_documented():
    """单一来源模块必须写明绝对档失效，避免后人再加绝对门槛。"""
    doc = thresholds.__doc__ or ""
    assert "0 命中" in doc and "0 可达" in doc
    src = Path(thresholds.__file__).read_text(encoding="utf-8")
    assert "736" in src and "64" in src

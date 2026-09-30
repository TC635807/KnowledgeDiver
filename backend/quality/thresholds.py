"""决策阈值单一来源（single source of truth）。

为什么需要（T13 tech_debt D1/D2）
=================================
重构前同一语义散落在 4 个文件 + 2 处提示词里，改一个阈值要同时改代码、提示词
与路由默认值；且无法区分「论文已披露的评分口径」与「工程启发式阈值」。

绝对档已实测失效（写进注释是为了阻止再次新增绝对门槛）
====================================================
  * refresh 三条件 REFRESH_GAP_ABS=0.65：29 库 736 卡 **0 命中**（实验产物
    refresh_threshold_stats.json，只读复现脚本见 scripts/ 目录）
  * 主题饱和绝对档 SATURATED_AVG_GAP_MAX=0.35：32 run / 64 个决策状态 **0 可达**
    （簇 avg_gap 分布 [0.4316, 0.5509]，复现脚本见 research 目录下的只读回放脚本）
  * 簇状态 healthy 的绝对档 CLUSTER_WEAK_GAP=0.5 仍在使用，但它属于簇分类口径，
    不是"可达性判据"。
结论：新代码需要区分强弱时，优先用**会话内相对秩（分位数）**；不要新增绝对门槛。
需要绝对门槛时必须同时输出可达性统计（参考 agent/tools.py 的 _reachability_report）。

兼容性约定
==========
本文件所有数值与重构前**逐字节一致**（tests/backend/test_thresholds.py 断言）。
改这些值属于实验口径变更，必须单独评审并同步论文与提示词。
"""

from __future__ import annotations

from typing import Dict

# ═══════════════════════════════════════════════════════════════
# 1. 质量评分口径（论文已披露，禁止随手改）
# ═══════════════════════════════════════════════════════════════

# 四维设计权重：structure / graph / semantic / confidence
DESIGN_WEIGHTS: Dict[str, float] = {
    "structure": 0.35,
    "graph": 0.30,
    "semantic": 0.25,
    "confidence": 0.10,
}

# gap 决策分档
GAP_LOW = 0.3
GAP_HIGH = 0.7

# 维度退化保护：全库某维标准差 <= 此值时该维权重置零并重归一化
DEGEN_EPS = 1e-3

# ═══════════════════════════════════════════════════════════════
# 2. Agent 决策阈值（绝对档已失效，见模块 docstring）
# ═══════════════════════════════════════════════════════════════

# refresh 三条件（legacy 口径）：绝对 gap + 会话内分位 + 结构分
# 注意：REFRESH_GAP_ABS 在真实库上 0 命中；KD_REFRESH_RULE=percentile 可改走分位口径。
REFRESH_GAP_ABS = 0.65
REFRESH_PERCENTILE_MIN = 0.90
REFRESH_STRUCTURE_MAX = 0.35

# 会话内相对最好 30% 视为"本地足够"
LOCAL_SUFFICIENT_PERCENTILE = 0.30

# 语义召回阈值（召回层从宽，最终判断交给 Agent 读内容）
SIMILAR_RECALL_THRESHOLD = 0.25
SIMILAR_LOCAL_HIT = 0.65
SIMILAR_AUTO_LINK = 0.70

# ═══════════════════════════════════════════════════════════════
# 3. 簇级诊断阈值（quality/cluster.py）
# ═══════════════════════════════════════════════════════════════

CLUSTER_MIN_SIZE = 4          # < 此值视为微簇/覆盖不足
CLUSTER_WEAK_GAP = 0.5        # 簇内平均 gap 达到此值视为内容薄弱
CLUSTER_FRAGMENTED_DIST = 0.35  # pairwise 平均余弦距离达到此值视为碎片化
CLUSTER_LOW_LINK_DENSITY = 0.3  # 链接密度低阈值
CLUSTER_STATUS_WEIGHT = {"weak": 2, "fragmented": 1}

# ═══════════════════════════════════════════════════════════════
# 4. 主题饱和守卫阈值（quality/topic_guard.py）
# ═══════════════════════════════════════════════════════════════

# 绝对饱和档：0/64 可达（见 docstring），仅作"可用簇 < 2 个"时的保守回退
SATURATED_AVG_GAP_MAX = 0.35
# 相对档：与非碎片化成规模簇的库内 avg_gap 上中位数比较
SATURATION_QUANTILE = 0.5
SATURATION_MIN_CLUSTERS = 2
# 微簇/孤立卡：主题视为完全未覆盖
UNDERCOVERED_TOPIC_GAP = 1.0

# ═══════════════════════════════════════════════════════════════
# 5. 工具权限与审计（T12 P0 安全；开关默认保持旧行为）
# ═══════════════════════════════════════════════════════════════

# legacy（默认，不拦截）| no_write（禁写层）| readonly（只允许读层）
TOOL_PERMISSION_LEGACY = "legacy"
TOOL_PERMISSION_NO_WRITE = "no_write"
TOOL_PERMISSION_READONLY = "readonly"
TOOL_PERMISSION_MODES = (
    TOOL_PERMISSION_LEGACY,
    TOOL_PERMISSION_NO_WRITE,
    TOOL_PERMISSION_READONLY,
)

# 不可信内容包裹（间接提示注入防护）：legacy（默认，原样）| on（包裹边界+声明）
UNTRUSTED_WRAP_LEGACY = "legacy"
UNTRUSTED_WRAP_ON = "on"
UNTRUSTED_WRAP_MODES = (UNTRUSTED_WRAP_LEGACY, UNTRUSTED_WRAP_ON)

__all__ = [
    "DESIGN_WEIGHTS",
    "GAP_LOW",
    "GAP_HIGH",
    "DEGEN_EPS",
    "REFRESH_GAP_ABS",
    "REFRESH_PERCENTILE_MIN",
    "REFRESH_STRUCTURE_MAX",
    "LOCAL_SUFFICIENT_PERCENTILE",
    "SIMILAR_RECALL_THRESHOLD",
    "SIMILAR_LOCAL_HIT",
    "SIMILAR_AUTO_LINK",
    "CLUSTER_MIN_SIZE",
    "CLUSTER_WEAK_GAP",
    "CLUSTER_FRAGMENTED_DIST",
    "CLUSTER_LOW_LINK_DENSITY",
    "CLUSTER_STATUS_WEIGHT",
    "SATURATED_AVG_GAP_MAX",
    "SATURATION_QUANTILE",
    "SATURATION_MIN_CLUSTERS",
    "UNDERCOVERED_TOPIC_GAP",
    "TOOL_PERMISSION_LEGACY",
    "TOOL_PERMISSION_NO_WRITE",
    "TOOL_PERMISSION_READONLY",
    "TOOL_PERMISSION_MODES",
    "UNTRUSTED_WRAP_LEGACY",
    "UNTRUSTED_WRAP_ON",
    "UNTRUSTED_WRAP_MODES",
]

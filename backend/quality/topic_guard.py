"""主题级饱和守卫 —— 把卡级 gap 索引与主题级覆盖目标对齐（implementation_plan.md P0-2）。

问题（E0.2 实测，32 run / 64 决策状态）
=====================================
  * 库内 Spearman(gap, 边际覆盖增益) 均值 −0.10（决策状态 −0.16）；
  * argmax-gap 动作 78.1% 零覆盖增益、U_gap / U_oracle = 0.108；
  * 76.7% 的卡其主导主题已被其他卡完全覆盖。
单卡 gap 度量的是"这张卡自己写得好不好"，看不到"这个主题在库里是否已经饱和"。
于是评估工具会在已饱和主题上继续推荐 expand_from_card，产出近零覆盖增益的新卡。
这是卡级信号 ↔ 主题级目标的错配，不是阈值调参能解决的问题。

本模块把它变成决策前的确定性检查
================================
  1. 用 backend.quality.cluster 的簇报告判定该卡所属主题是否饱和；
  2. 饱和 ⇒ 不把 expand 推荐指向本卡，改指向库内覆盖不足（undercovered）主题的代表卡；
  3. 库内无覆盖不足主题 ⇒ 直接判定 local_sufficient（复用本地，不再联网）。

诚实边界（必须写进论文）
========================
  * 主题单元是库内密度簇（HDBSCAN），不是评测大纲主题——运行时没有主题真值；
    "饱和"是 avg_gap 低 + 非碎片化的代理（有偏），其效度由 E1/E2 用探针 oracle 检验。
  * 本模块是纯函数、零 IO，只消费簇报告（库内结构信号）。
    运行时禁止读取任何评测专用资产（探针题库 / 专家大纲 / 人工评分文件，
    见 implementation_plan.md 第 0 节铁律）；tests/backend/test_no_eval_leakage.py 守卫。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# 阈值统一来源：backend/quality/thresholds.py（其 docstring 记录了
# "主题饱和绝对档 0/64 可达、refresh 三条件 0/736 命中"的实测结论）。
from backend.quality.thresholds import (
    CLUSTER_MIN_SIZE,
    SATURATED_AVG_GAP_MAX,
    SATURATION_MIN_CLUSTERS,
    SATURATION_QUANTILE,
    UNDERCOVERED_TOPIC_GAP,
)


def _cluster_index(report: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """card_id -> cluster dict。"""
    index: Dict[str, Dict[str, Any]] = {}
    for cluster in (report or {}).get("clusters", []) or []:
        for card_id in cluster.get("card_ids", []) or []:
            index[card_id] = cluster
    return index


def library_saturation_cutoff(report: Optional[Dict[str, Any]]) -> float:
    """库内饱和门槛：非碎片化、成规模簇的 avg_gap 上中位数。

    用"库内相对秩"而不是"绝对 avg_gap 门槛"，理由与 refresh 可达性同源：
    绝对门槛在分布漂移下会永久失效（实测 0/64）。可用簇不足 2 个时，相对判据退化，
    回退到绝对档（保守：不触发守卫，保留原路径）。
    """
    pool: List[float] = []
    for cluster in (report or {}).get("clusters", []) or []:
        status = str(cluster.get("status", ""))
        if "fragmented" in status:
            continue  # 碎片化主题不参与"覆盖良好"判据
        if int(cluster.get("size", 0) or 0) < CLUSTER_MIN_SIZE:
            continue
        raw_avg = cluster.get("avg_gap")
        if raw_avg is None:
            continue
        pool.append(float(raw_avg))
    if len(pool) < SATURATION_MIN_CLUSTERS:
        return SATURATED_AVG_GAP_MAX
    pool.sort()
    return round(pool[int(len(pool) * SATURATION_QUANTILE)] if SATURATION_QUANTILE < 1 else pool[-1], 4)


def cluster_saturated(cluster: Optional[Dict[str, Any]], *, cutoff: Optional[float] = None) -> bool:
    """簇级饱和判定（卡级判定与离线回放共用同一规则，避免常量漂移）。

    饱和 = 簇状态 healthy（avg_gap < 0.5 且非碎片化）
         + 簇规模达到主题单元下限
         + avg_gap 不超过 cutoff（默认绝对档；调用方可传入库内相对档）。
    """
    if not cluster:
        return False
    if str(cluster.get("status", "")) != "healthy":
        return False
    if int(cluster.get("size", 0) or 0) < CLUSTER_MIN_SIZE:
        return False
    raw_avg = cluster.get("avg_gap")
    if raw_avg is None:
        return False
    avg_gap = float(raw_avg)
    limit = SATURATED_AVG_GAP_MAX if cutoff is None else cutoff
    return avg_gap <= limit


def card_topic_verdict(report: Optional[Dict[str, Any]], card_id: str) -> Dict[str, Any]:
    """该卡所属主题的覆盖判定。

    report 为空 / 卡不在任何簇 -> status="unknown"、saturated=False（保守：不改变原路径）。
    """
    report = report or {}
    cluster = _cluster_index(report).get(card_id)
    if cluster is not None:
        status = str(cluster.get("status", "unknown"))
        raw_avg = cluster.get("avg_gap")
        avg_gap = float(raw_avg) if raw_avg is not None else 0.5
        size = int(cluster.get("size", 0) or 0)
        cutoff = library_saturation_cutoff(report)
        saturated = cluster_saturated(cluster, cutoff=cutoff)
        return {
            "card_id": card_id,
            "cluster_id": cluster.get("cluster_id"),
            "topic_status": status,
            "cluster_size": size,
            "topic_gap": round(avg_gap, 4),
            "topic_saturated": bool(saturated),
            "saturation_cutoff": cutoff,
            "verdict_source": "cluster",
        }

    for unit in report.get("undercovered", []) or []:
        if unit.get("card_id") == card_id:
            return {
                "card_id": card_id,
                "cluster_id": None,
                "topic_status": "undercovered",
                "cluster_size": int(unit.get("cluster_size", 0) or 0),
                "topic_gap": UNDERCOVERED_TOPIC_GAP,
                "topic_saturated": False,
                "verdict_source": "undercovered",
            }

    return {
        "card_id": card_id,
        "cluster_id": None,
        "topic_status": "unknown",
        "cluster_size": 0,
        "topic_gap": 0.5,
        "topic_saturated": False,
        "verdict_source": "none",
    }


def best_undercovered(
    report: Optional[Dict[str, Any]], *, exclude_card_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """覆盖不足主题的代表卡：gap 最大者。

    cluster.py 已按 gap 降序，这里显式再排序以对输入顺序不敏感；
    平手时优先更大的微簇（更能代表一个真实缺口），最后按 card_id 字典序保证确定性。
    """
    units: List[Dict[str, Any]] = [
        unit for unit in (report or {}).get("undercovered", []) or []
        if unit.get("card_id") and unit.get("card_id") != exclude_card_id
    ]
    if not units:
        return None
    best = max(
        units,
        key=lambda u: (
            float(u.get("gap_score", 0.0) or 0.0),
            int(u.get("cluster_size", 0) or 0),
            str(u.get("card_id")),
        ),
    )
    return {
        "card_id": best.get("card_id"),
        "title": best.get("title"),
        "gap_score": round(float(best.get("gap_score", 0.0) or 0.0), 4),
        "cluster_size": int(best.get("cluster_size", 0) or 0),
    }


def topic_aware_decision(report: Optional[Dict[str, Any]], card_id: str) -> Dict[str, Any]:
    """决策前的主题饱和检查（纯函数，可直接单测）。

    返回字段：
      topic_status / topic_gap / topic_saturated / cluster_id / cluster_size / verdict_source
      guard_applied  —— 是否真的改写了推荐（仅当主题饱和时为 True）
      decision       —— 仅 guard_applied 时有效：needs_expand（改向代表卡）/ local_sufficient
      reason         —— 面向 LLM 的推荐语（含"饱和"与改向目标）
      redirect_card_id / redirect_title / redirect_gap —— 改向目标（无则 None）

    调用方只在 topic_saturated 为真时覆盖旧判定；否则必须保留原路径。
    """
    verdict = card_topic_verdict(report, card_id)
    out: Dict[str, Any] = {
        **verdict,
        "guard_applied": bool(verdict["topic_saturated"]),
        "decision": None,
        "reason": "",
        "redirect_card_id": None,
        "redirect_title": None,
        "redirect_gap": None,
    }
    if not verdict["topic_saturated"]:
        return out

    cluster_id = verdict["cluster_id"]
    size = verdict["cluster_size"]
    topic_gap = verdict["topic_gap"]
    redirect = best_undercovered(report, exclude_card_id=card_id)
    if redirect is not None:
        out["decision"] = "needs_expand"
        out["redirect_card_id"] = redirect["card_id"]
        out["redirect_title"] = redirect["title"]
        out["redirect_gap"] = redirect["gap_score"]
        out["reason"] = (
            f"该主题已饱和（簇{cluster_id}，{size}卡，avg_gap={topic_gap:.2f}）："
            f"再搜索/扩展本主题的期望覆盖增益≈0，不要在本主题上继续 expand 或联网。"
            f"库内覆盖不足主题的代表卡是「{redirect['title']}」(gap={redirect['gap_score']:.2f})，"
            f"确有补充需要时请优先扩展它。"
        )
    else:
        out["decision"] = "local_sufficient"
        out["reason"] = (
            f"该主题已饱和（簇{cluster_id}，{size}卡，avg_gap={topic_gap:.2f}），"
            f"且库内无覆盖不足主题：再搜索本主题的期望覆盖增益≈0，"
            f"建议直接复用本地已有内容，不再联网。"
        )
    return out


__all__ = [
    "CLUSTER_MIN_SIZE",
    "SATURATED_AVG_GAP_MAX",
    "SATURATION_QUANTILE",
    "SATURATION_MIN_CLUSTERS",
    "UNDERCOVERED_TOPIC_GAP",
    "library_saturation_cutoff",
    "cluster_saturated",
    "card_topic_verdict",
    "best_undercovered",
    "topic_aware_decision",
]

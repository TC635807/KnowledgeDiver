"""标题归一化与精确查重工具（方案 B：搜索前标题预检）。

规则：归一化精确匹配，零子串、零模糊、零启发式。
- 归一化：去空白（含全角）、统一全/半角括号、小写
- 判定：normalize(topic) == normalize(已有卡标题) 才算命中
- 「梯度（深度学习）」vs「梯度」→ 不命中（限定词不同允许建卡）
- 「PID控制」vs「PID 控制」→ 命中（归一化后相等）
- 「二阶振荡环节」vs「二阶过阻尼系统」→ 不命中（语义近似交给向量层）
"""

import re


def normalize_title(s: str) -> str:
    """标题归一化：去空白（含全角）、统一全半角括号、小写。用于标题变体精确去重。"""
    s = re.sub(r"[\s\u3000]+", "", s)
    return s.replace("（", "(").replace("）", ")").lower()


def title_containment(a: str, b: str) -> float:
    """归一化标题的包含度（0~1），用于「向量像但未必是同一主题」的词面确认。

    规则：
    - 完全相等 → 1.0（通常已被标题预检拦下，这里只是兜底）；
    - 一方是另一方子串（「免疫器官」⊂「中枢免疫器官」）→ 2*短/长，4 vs 6 字 = 0.80；
    - 互不包含 → 0.0。

    **为什么不用 difflib 字符相似度**：中文「带领域限定词的兄弟主题」相似度很高
    但语义不同——实测「馕（印度烤饼）」vs「罗提（印度烤饼）」=0.80、
    「坦都里（印度烤炉烹饪）」vs「坦都里鸡（印度菜）」=0.70，用 0.7 的相似度门槛会
    把两个不同主题误合并。字符**包含**关系才是可靠的「同概念变体」信号。
    """
    na, nb = normalize_title(a or ""), normalize_title(b or "")
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 2 * min(len(na), len(nb)) / (len(na) + len(nb))
    return 0.0


def find_card_by_normalized_title(cards, title: str, exclude_id=None):
    """在卡片列表中按归一化精确标题查找，返回命中卡片或 None。

    Args:
        cards: 可迭代的卡片对象列表（需有 .id / .title 属性）
        title: 待查主题词
        exclude_id: 排除的卡片 ID（源卡自身，防自链误拦）
    """
    norm = normalize_title(title)
    for c in cards:
        if exclude_id and getattr(c, "id", None) == exclude_id:
            continue
        if normalize_title(getattr(c, "title", "") or "") == norm:
            return c
    return None

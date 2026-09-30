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

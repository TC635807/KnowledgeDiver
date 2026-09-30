"""抓取内容质量门 — 轻量启发式，拦截无价值页面（简化自 groktocrawl ADR-0016）。

三道启发式门：最小长度 / 阻断页特征 / 链接密度（导航列表页）。
全部 stdlib、零 LLM 调用，在 fetch 之后、summarize（LLM）之前执行——
避免为垃圾内容支付 LLM 成本。

对照 groktocrawl 原版（boilerplate 0.3 + completeness 0.3 + block 0.4 复合分）：
简化为一票否决制——任一门槛不满足即拒绝，原因可审计。
"""

_MIN_CHARS = 200
_BLOCK_HITS = 2
# 阻断/错误页特征（中英混合；匹配的是"页面渲染成了文本"的静默失败）
_BLOCK_MARKERS = (
    "access denied", "you have been blocked", "enable javascript",
    "not a robot", "rate limit", "too many requests",
    "not available in your country", "subscribe to continue",
    "accept cookies", "验证码", "人机验证", "访问过于频繁",
    "403 forbidden", "404 not found",
)
# 导航/列表页特征：短文本内大量链接
_NAV_LINK_THRESHOLD = 30
_NAV_MAX_CHARS = 2000


def gate_content(text: str) -> tuple[bool, str]:
    """返回 (是否通过, 未通过原因)。通过时原因为空字符串。"""
    t = (text or "").strip()
    if len(t) < _MIN_CHARS:
        return False, f"内容过短（{len(t)} 字符 < {_MIN_CHARS}）"
    low = t.lower()
    hits = sum(1 for m in _BLOCK_MARKERS if m in low)
    if hits >= _BLOCK_HITS:
        return False, f"疑似阻断/错误页（{hits} 个特征命中）"
    n_links = low.count("http")
    if n_links > _NAV_LINK_THRESHOLD and len(t) < _NAV_MAX_CHARS:
        return False, f"疑似导航/列表页（{n_links} 个链接但仅 {len(t)} 字符）"
    return True, ""


def _self_check() -> None:
    ok, reason = gate_content("x" * 500)
    assert ok, f"正常长文应通过: {reason}"
    ok, reason = gate_content("short")
    assert not ok and "过短" in reason, f"短文应拒绝: {reason}"
    ok, reason = gate_content(
        "access denied you have been blocked please enable javascript "
        "and try again later. 验证码。人机验证。" + "x" * 300
    )
    assert not ok and "阻断" in reason, f"阻断页应拒绝: {reason}"
    links = " ".join(f"https://example.com/link{i}" for i in range(50))
    ok, reason = gate_content(links + "x" * 100)
    assert not ok and "导航" in reason, f"导航页应拒绝: {reason}"
    print("quality_gate self-check OK")


if __name__ == "__main__":
    _self_check()

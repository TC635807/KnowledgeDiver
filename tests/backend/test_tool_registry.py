"""工具注册表回归测试（T14 重构①）。

锁定：
- 13 个工具 schema 与重构前**逐字段一致**（sha256 冻结签名）；
- 层级映射覆盖全部工具且取值合法；
- tools.py 仍可从 registry 取到 TOOL_SCHEMAS（向后兼容）；
- 读层集合由注册表层级派生，与重构前 9 个工具一致。
"""

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent import tool_registry, tools as tools_mod  # noqa: E402

# 开源版基线（从内部仓库剥离、移除商业化层后）实测的 TOOL_SCHEMAS 签名。
# 任何 schema 改动都会让本断言失败——这是"默认行为逐字段等价"的第一道锁。
# 相对内部仓库的变动仅一处：assess_knowledge_base 的工具描述里
# "（0 积分）" 改为 "（纯本地计算）"，因为开源版已移除积分系统。
FROZEN_SIGNATURE = "5f6681913f5a9f6e6e08c2076bb37c2d395954a4ae8e987462351b2d84bb9a69"

EXPECTED_TOOLS = {
    "search_similar_cards", "get_card_info", "get_linked_cards", "list_cards",
    "get_card_tree", "search_by_keyword", "expand_from_card", "refresh_card",
    "assess_card_quality", "assess_knowledge_base", "plan_knowledge_gaps",
    "assess_exploration_need", "link_card",
}


def test_schema_signature_frozen():
    payload = json.dumps(tool_registry.TOOL_SCHEMAS, sort_keys=True, ensure_ascii=False)
    assert hashlib.sha256(payload.encode()).hexdigest() == FROZEN_SIGNATURE


def test_tool_names_unchanged():
    names = {s["function"]["name"] for s in tool_registry.TOOL_SCHEMAS}
    assert names == EXPECTED_TOOLS
    assert tool_registry._TOOL_NAMES == frozenset(EXPECTED_TOOLS)


def test_tier_map_covers_all_tools_and_values_valid():
    assert set(tool_registry.TOOL_TIER_MAP) == EXPECTED_TOOLS
    assert set(tool_registry.TOOL_TIER_MAP.values()) <= set(tool_registry.TOOL_TIERS)
    assert tool_registry.tools_by_tier("read") == (
        "assess_card_quality", "assess_exploration_need", "assess_knowledge_base",
        "get_card_info", "get_card_tree", "get_linked_cards", "list_cards",
        "search_similar_cards",
    )
    assert tool_registry.tools_by_tier("prescribe") == ("plan_knowledge_gaps",)
    assert tool_registry.tools_by_tier("write") == (
        "expand_from_card", "link_card", "refresh_card", "search_by_keyword",
    )


def test_tool_tier_defaults_to_read_for_unknown():
    assert tool_registry.tool_tier("no_such_tool") == tool_registry.TIER_READ


def test_schemas_for_tiers_filters_readonly():
    read_only = tool_registry.schemas_for_tiers(("read", "prescribe"))
    names = {s["function"]["name"] for s in read_only}
    assert "search_by_keyword" not in names
    assert "expand_from_card" not in names
    assert "link_card" not in names
    assert len(read_only) == 9


def test_tools_module_reexports_registry():
    """向后兼容：既有 from backend.agent.tools import TOOL_SCHEMAS 继续可用。"""
    assert tools_mod.TOOL_SCHEMAS is tool_registry.TOOL_SCHEMAS
    assert tools_mod._TOOL_NAMES == tool_registry._TOOL_NAMES
    assert tools_mod._TOOL_ALIASES == tool_registry._TOOL_ALIASES


def test_read_tools_derived_from_registry_matches_legacy():
    """重构前 _READ_TOOLS 是 9 个手写名字（8 read + plan_knowledge_gaps）。"""
    assert len(tools_mod.ToolExecutor._READ_TOOLS) == 9
    assert "plan_knowledge_gaps" in tools_mod.ToolExecutor._READ_TOOLS
    assert tools_mod.ToolExecutor._READ_TOOLS == frozenset(
        tool_registry.tools_by_tier("read") + tool_registry.tools_by_tier("prescribe")
    )
    # 搜索熔断名单不含 link_card（与重构前一致）
    assert tools_mod.ToolExecutor._WRITE_TOOLS == frozenset(
        {"search_by_keyword", "expand_from_card", "refresh_card"}
    )


def test_aliases_preserved():
    assert tool_registry._TOOL_ALIASES == {
        "get_card": "get_card_info",
        "search_keyword": "search_by_keyword",
        "link_cards": "link_card",
        "assess_exploration": "assess_exploration_need",
        "search_similar": "search_similar_cards",
    }

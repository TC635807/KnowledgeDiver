"""无向链接 + 显式 parent_id 树方向 回归测试。

锁定行为：
- create_link 对称：a↔b 双方 links/backlinks 互含，create_link(a,b)==create_link(b,a)
- link_cards 参数顺序无关（无向）
- get_children/get_root_cards 完全由 parent_id 驱动，无创建时间启发式
- re-root 场景（后建父卡挂载先建子卡）树完整显示
- update_parent 设置 parent_id 并保持无向链接
"""

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.links.manager import LinkManager  # noqa: E402
from backend.models.card import Card  # noqa: E402


def _card(title: str, created, links=(), backlinks=(), parent_id=None) -> Card:
    return Card(
        id=str(uuid.uuid4()), title=title, content="内容" + title,
        metadata={}, created_at=created, updated_at=created, confidence=0.8,
        sources=[], tags=[], links=list(links), backlinks=list(backlinks),
        parent_id=parent_id,
    )


def _store(tmp_path):
    from backend.storage.sqlite_card_store import SqliteCardStore
    return SqliteCardStore(base_dir=str(tmp_path), username="u", session_id="s")


def run(coro):
    return asyncio.run(coro)


# ── 无向链接对称性 ──────────────────────────────────────────────────

def test_create_link_symmetric(tmp_path):
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    a = _card("甲", t0)
    b = _card("乙", t0 + timedelta(minutes=1))
    store = _store(tmp_path)
    for c in (a, b):
        store.create_card(c)

    ok, warnings = run(LinkManager(store).create_link(a.id, b.id))
    assert ok and not warnings
    a2 = store.read_card(a.id)
    b2 = store.read_card(b.id)
    assert b.id in a2.links and a.id in b2.links, "links 对称"
    assert b.id in a2.backlinks and a.id in b2.backlinks, "backlinks 对称"
    # 反向调用幂等且结果一致（无向）
    ok2, _ = run(LinkManager(store).create_link(b.id, a.id))
    assert ok2
    assert store.read_card(a.id).links == a2.links


def test_create_link_remove_symmetric(tmp_path):
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    a = _card("甲", t0)
    b = _card("乙", t0 + timedelta(minutes=1))
    store = _store(tmp_path)
    for c in (a, b):
        store.create_card(c)
    run(LinkManager(store).create_link(a.id, b.id))
    ok = run(LinkManager(store).remove_link(b.id, a.id))
    assert ok
    a2 = store.read_card(a.id)
    b2 = store.read_card(b.id)
    assert a2.links == [] and a2.backlinks == []
    assert b2.links == [] and b2.backlinks == []


# ── parent_id 树方向（无启发式） ────────────────────────────────────

def test_tree_parent_id_based(tmp_path):
    """树完全由 parent_id 驱动：无创建时间启发式，re-root 子卡完整显示。"""
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    child1 = _card("先建子卡A", t0)
    child2 = _card("先建子卡B", t0 + timedelta(minutes=1))
    root = _card("后建根卡", t0 + timedelta(minutes=2))   # 后建父卡
    root.links = [child1.id, child2.id]                    # 无向链接（对称写）
    child1.links = [root.id]
    child2.links = [root.id]
    store = _store(tmp_path)
    for c in (child1, child2, root):
        store.create_card(c)

    # 旧实现：created_at 启发式会丢弃先建子卡（e2e 实证 5 卡只显示 3）
    # 新实现：parent_id 显式方向
    store.update_parent(child1.id, root.id)
    store.update_parent(child2.id, root.id)

    assert [r.title for r in store.get_root_cards()] == ["后建根卡"]
    children = sorted(x.title for x in store.get_children(root.id))
    assert children == ["先建子卡A", "先建子卡B"], f"re-root 子卡不丢失: {children}"
    assert store.get_children(child1.id) == []
    assert store.read_card(root.id).parent_id is None


def test_update_parent_keeps_undirected_link(tmp_path):
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    a = _card("父", t0)
    b = _card("子", t0 + timedelta(minutes=1))
    store = _store(tmp_path)
    for c in (a, b):
        store.create_card(c)
    store.update_parent(b.id, a.id)
    a2 = store.read_card(a.id)
    b2 = store.read_card(b.id)
    assert b2.parent_id == a.id
    assert b.id in a2.links and a.id in b2.links, "挂载同时建立无向链接"


def test_update_parent_rejects_direct_cycle(tmp_path):
    """直接环：A 已挂 B 下，再把 B 挂 A 下必须拒绝（e2e 实证两卡互父 → 树全空）。"""
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    a = _card("甲", t0)
    b = _card("乙", t0 + timedelta(minutes=1))
    store = _store(tmp_path)
    for c in (a, b):
        store.create_card(c)
    store.update_parent(b.id, a.id)
    import pytest as _pytest
    with _pytest.raises(ValueError, match="循环父卡"):
        store.update_parent(a.id, b.id)
    # 拒绝后原方向不变
    assert store.read_card(b.id).parent_id == a.id
    assert store.read_card(a.id).parent_id is None


def test_update_parent_rejects_indirect_cycle(tmp_path):
    """间接环：C→B→A，再把 A 挂 C 下必须拒绝。"""
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    a, b, c = _card("甲", t0), _card("乙", t0 + timedelta(minutes=1)), _card("丙", t0 + timedelta(minutes=2))
    store = _store(tmp_path)
    for x in (a, b, c):
        store.create_card(x)
    store.update_parent(b.id, a.id)
    store.update_parent(c.id, b.id)
    import pytest as _pytest
    with _pytest.raises(ValueError, match="循环父卡"):
        store.update_parent(a.id, c.id)
    assert store.read_card(c.id).parent_id == b.id


# ── SqliteCardStore 新列读写 ────────────────────────────────────────

def test_parent_id_roundtrip(tmp_path):
    t0 = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    a = _card("父", t0)
    b = _card("子", t0 + timedelta(minutes=1), parent_id=None)
    store = _store(tmp_path)
    for c in (a, b):
        store.create_card(c)
    store.update_parent(b.id, a.id)
    reloaded = store.read_card(b.id)
    assert reloaded.parent_id == a.id
    # 更新不丢 parent_id
    reloaded.content = "更新后的内容"
    store.update_card(reloaded)
    assert store.read_card(b.id).parent_id == a.id

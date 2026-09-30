"""Agent e2e 事后验证 — 检查建库结果：卡片、树结构、链接、向量、语义搜索、质量评估。

用法:
    .venv/Scripts/python.exe scripts/agent_e2e_verify.py <username> <session_id>
"""

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from backend.pipeline.api import PipelineAPI

    username = sys.argv[1]
    session_id = sys.argv[2]
    api = PipelineAPI(username=username, session_id=session_id)

    cards = api.list_cards()
    print(f"== {username}/{session_id} 卡片总数: {len(cards)} ==")
    for c in cards:
        print(f"  - [{c.id[:8]}] {c.title} | links={len(c.links)} backlinks={len(c.backlinks)} "
              f"| content={len(c.content or '')}字 | sources={len(c.sources)}")

    tree = api.get_card_tree()
    print(f"\n== 树结构（根数 {len(tree)}）==")

    def walk(nodes, depth):
        for node in nodes:
            card = node.get("card") or {}
            print(f"{'  ' * depth}- {card.get('title', '?')} ({card.get('id', '')[:8]})")
            walk(node.get("children") or [], depth + 1)

    walk(tree, 0)

    async def semantic() -> None:
        print("\n== 语义搜索测试 ==")
        queries = sys.argv[3:] or ["敏捷开发", "持续集成", "容器化部署"]
        for q in queries:
            hits = await api.search_similar_cards(q, limit=3, threshold=0.3)
            top = [(h["card"].title, round(h["score"], 3)) for h in hits]
            print(f"  「{q}」→ {top}")

    asyncio.run(semantic())


if __name__ == "__main__":
    main()

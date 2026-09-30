"""为所有存量会话执行 parent_id 回填迁移（幂等，可重复运行）。

用法:
    .venv/Scripts/python.exe scripts/migrate_parent_ids.py            # 实际写入
    .venv/Scripts/python.exe scripts/migrate_parent_ids.py --dry-run  # 只统计

迁移规则见 backend/storage/parent_migration.py：
- 非互指边（P.links∋C 且 C.links∌P）→ C.parent = P（links 方向权威，re-root 场景可恢复）
- 互指边（双向）→ 创建更晚者为子（旧 keyword-merge 污染容错）
- 无候选 → 保持根
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.storage.parent_migration import migrate_session  # noqa: E402

DRY_RUN = "--dry-run" in sys.argv


def main() -> None:
    cards_root = ROOT / "cards"
    total = 0
    sessions = 0
    for db in sorted(cards_root.rglob("session.db")):
        parts = db.relative_to(cards_root).parts
        if len(parts) != 3:  # cards/{username}/{session_id}/session.db
            continue
        username, session_id, _ = parts
        try:
            n = migrate_session(username, session_id, dry_run=DRY_RUN)
        except Exception as e:
            print(f"跳过 {username}/{session_id}: {e}")
            continue
        if n:
            sessions += 1
            total += n
            print(f"{username}/{session_id}: {n} 张卡片")
    mode = "dry-run（未写入）" if DRY_RUN else "已写入"
    print(f"共处理 {sessions} 个会话 / {total} 张卡片（{mode}）")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
会话迁移脚本。

将旧的卡片目录结构迁移到新的基于会话的结构：
Before: cards/{username}/*.md
After:  cards/{username}/default/*.md

同时处理 cards/*.md 中的无主卡片，将其移动到
cards/unknown/default/*.md 并发出警告。
"""

import os
import sys
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CARDS_DIR = PROJECT_ROOT / "cards"

def migrate_user_cards(username: str) -> int:
    """迁移指定用户的卡片到 default 子目录。"""
    user_dir = CARDS_DIR / username
    default_dir = user_dir / "default"

    if not user_dir.exists():
        print(f"  User directory {user_dir} does not exist, skipping")
        return 0

    md_files = list(user_dir.glob("*.md"))
    if not md_files:
        print(f"  No .md files found in {user_dir}")
        return 0

    default_dir.mkdir(exist_ok=True)

    moved_count = 0
    for md_file in md_files:
        if md_file.is_file():
            dest = default_dir / md_file.name
            if dest.exists():
                print(f"  Warning: {dest} already exists, skipping {md_file}")
                continue
            shutil.move(str(md_file), str(dest))
            moved_count += 1

    print(f"  Moved {moved_count} cards from {user_dir} to {default_dir}")
    return moved_count

def migrate_orphaned_cards() -> int:
    """迁移 cards 根目录下的无主卡片。"""
    md_files = list(CARDS_DIR.glob("*.md"))
    if not md_files:
        return 0

    default_dir = CARDS_DIR / "unknown" / "default"
    default_dir.mkdir(parents=True, exist_ok=True)

    moved_count = 0
    for md_file in md_files:
        if md_file.is_file():
            dest = default_dir / md_file.name
            if dest.exists():
                print(f"  Warning: {dest} already exists, skipping {md_file}")
                continue
            shutil.move(str(md_file), str(dest))
            moved_count += 1

    print(f"  Moved {moved_count} orphaned cards to {default_dir}")
    print(f"  Note: These cards are not associated with any user.")
    print(f"  You may need to manually assign them to a user.")
    return moved_count

def main():
    """主入口：遍历所有用户目录并执行迁移。"""
    print("Starting session migration...")
    print(f"Cards directory: {CARDS_DIR}")

    if not CARDS_DIR.exists():
        print(f"Error: Cards directory {CARDS_DIR} does not exist.")
        sys.exit(1)

    user_dirs = []
    for entry in CARDS_DIR.iterdir():
        if entry.is_dir() and not entry.name.startswith("."):
            user_dirs.append(entry.name)

    print(f"Found {len(user_dirs)} user directories: {user_dirs}")

    total_moved = 0
    for username in user_dirs:
        print(f"\nMigrating user: {username}")
        moved = migrate_user_cards(username)
        total_moved += moved

    print(f"\nChecking for orphaned cards in root...")
    orphaned_moved = migrate_orphaned_cards()
    total_moved += orphaned_moved

    print(f"\nMigration complete!")
    print(f"Total cards moved: {total_moved}")

    if orphaned_moved > 0:
        print(f"\n⚠️  Warning: {orphaned_moved} cards were moved to cards/unknown/default/")
        print("  These cards are not associated with any user account.")
        print("  You may need to manually move them to the correct user directory.")

    print("\nNext steps:")
    print("1. Start the backend server - it will create sessions.json for each user")
    print("2. Verify cards are accessible through the API")
    print("3. Test session functionality in the frontend")

if __name__ == "__main__":
    main()
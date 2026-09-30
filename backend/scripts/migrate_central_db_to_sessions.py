"""
一次性迁移：将 knowledgediver.db 中 cards 表的现有数据迁移到 per-session .db 文件。

从 data/knowledgediver.db 的 cards 表读取所有卡片，
按 (username, session_id) 分组写入 cards/{username}/{session_id}/session.db。
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def migrate():
    central_db_path = PROJECT_ROOT / "data" / "knowledgediver.db"
    if not central_db_path.exists():
        print("knowledgediver.db 不存在，跳过")
        return

    central_conn = sqlite3.connect(str(central_db_path))
    central_conn.row_factory = sqlite3.Row

    rows = central_conn.execute(
        "SELECT username, session_id FROM cards GROUP BY username, session_id"
    ).fetchall()

    total_migrated = 0
    for row in rows:
        username = row["username"]
        session_id = row["session_id"]

        session_db_dir = PROJECT_ROOT / "cards" / username / session_id
        session_db_dir.mkdir(parents=True, exist_ok=True)
        session_db_path = session_db_dir / "session.db"

        cards = central_conn.execute(
            "SELECT * FROM cards WHERE username = ? AND session_id = ?",
            (username, session_id),
        ).fetchall()

        session_conn = sqlite3.connect(str(session_db_path))
        session_conn.row_factory = sqlite3.Row
        session_conn.execute("PRAGMA journal_mode=WAL;")
        session_conn.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                content     TEXT NOT NULL DEFAULT '',
                links       TEXT DEFAULT '[]',
                backlinks   TEXT DEFAULT '[]',
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                sources     TEXT DEFAULT '[]',
                confidence  REAL DEFAULT 0.0,
                tags        TEXT DEFAULT '[]',
                metadata    TEXT DEFAULT '{}'
            );
        """)

        count = 0
        for card in cards:
            existing = session_conn.execute(
                "SELECT 1 FROM cards WHERE id = ?", (card["id"],)
            ).fetchone()
            if existing:
                continue
            session_conn.execute(
                """INSERT INTO cards
                   (id, title, content, links, backlinks, created_at, updated_at,
                    sources, confidence, tags, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    card["id"],
                    card["title"],
                    card["content"],
                    card["links"],
                    card["backlinks"],
                    card["created_at"],
                    card["updated_at"],
                    card["sources"],
                    card["confidence"],
                    card["tags"],
                    card["metadata"],
                ),
            )
            count += 1
        session_conn.commit()
        session_conn.close()

        total_migrated += count
        print(f"  {username}/{session_id}: {count} cards → {session_db_path}")

    central_conn.close()
    print(f"\nTotal: {total_migrated} cards migrated to per-session .db files")


def verify():
    cards_root = PROJECT_ROOT / "cards"
    total = 0
    for session_db_path in sorted(cards_root.glob("*/*/session.db")):
        conn = sqlite3.connect(str(session_db_path))
        cnt = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        conn.close()
        rel = session_db_path.relative_to(PROJECT_ROOT)
        print(f"  {rel}: {cnt} cards")
        total += cnt
    print(f"\nTotal in per-session DBs: {total}")


def sync_session_counts():
    cards_root = PROJECT_ROOT / "cards"
    synced = 0
    for user_dir in cards_root.iterdir():
        if not user_dir.is_dir():
            continue
        sf = user_dir / "sessions.json"
        sessions = {}
        if sf.exists():
            with open(sf, "r", encoding="utf-8") as f:
                sessions = json.load(f)

        changed = False
        for sid_dir in user_dir.iterdir():
            if not sid_dir.is_dir():
                continue
            sid = sid_dir.name
            sdb = sid_dir / "session.db"
            if not sdb.exists():
                continue

            db_count = 0
            try:
                conn = sqlite3.connect(str(sdb))
                db_count = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
                conn.close()
            except Exception:
                continue

            if db_count == 0:
                continue

            if sid not in sessions:
                sessions[sid] = {
                    "id": sid,
                    "name": sid,
                    "created_at": "2026-05-25T00:00:00",
                    "updated_at": "2026-05-25T00:00:00",
                    "card_count": db_count,
                }
                changed = True
                synced += 1
                print(f"  + {user_dir.name}/{sid}: new session ({db_count} cards)")
            else:
                current = sessions[sid].get("card_count", 0)
                if current != db_count:
                    sessions[sid]["card_count"] = db_count
                    changed = True
                    synced += 1
                    print(f"  {user_dir.name}/{sid}: {current} -> {db_count}")

        if changed:
            with open(sf, "w", encoding="utf-8") as f:
                json.dump(sessions, f, ensure_ascii=False, indent=2)

    print(f"Synced {synced} sessions")


if __name__ == "__main__":
    print("=" * 50)
    print("Central DB → Per-session DB migration")
    print("=" * 50)
    migrate()
    print()
    print("=" * 50)
    print("Syncing sessions.json card counts")
    print("=" * 50)
    sync_session_counts()
    print()
    print("=" * 50)
    print("Verification")
    print("=" * 50)
    verify()

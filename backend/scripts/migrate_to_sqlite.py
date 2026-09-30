"""
一次性迁移脚本：将文件系统中的现有数据导入存储。

从以下位置读取数据：
  - data/users.json           → users 表 (knowledgediver.db)
  - cards/{user}/{session}/*.md → per-session session.db
  - Hub/{creator}/{name}/     → hub_sessions 表 (knowledgediver.db)
  - backend/share/tokens.json → share_tokens 表 (knowledgediver.db)

卡片的 knowledgediver.db → session.db 迁移通过读取现有 .md 文件或中央 DB 完成。
幂等（可重复执行，已导入的记录会跳过）。
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_json(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [WARN] 无法读取 {path}: {e}")
        return None


def parse_frontmatter_raw(content):
    """简易 frontmatter 解析（不依赖 python-frontmatter 库）。"""
    content = content.strip()
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 3 :].strip()
    meta = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            meta[key] = val
    return meta, body


def parse_yaml_value(raw: str):
    """将 YAML frontmatter 的字符串值解析为 Python 类型。"""
    raw = raw.strip()
    if raw == "[]" or raw == "":
        return []
    if raw == "{}":
        return {}
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.lower() == "null" or raw == "~":
        return None
    # 列表 [a, b, c]
    if raw.startswith("[") and raw.endswith("]"):
        items = []
        for item in raw[1:-1].split(","):
            item = item.strip().strip("'").strip('"')
            if item:
                items.append(item)
        return items
    # 数字
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    # 去掉引号
    return raw.strip("'").strip('"').strip()


def parse_frontmatter_proper(content):
    """从卡片的 Markdown 文本中正确解析 YAML frontmatter。"""
    content = content.strip()
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end].strip()
    body = content[end + 3 :].strip()
    meta = {}
    current_key = None
    current_list = None
    for line in fm_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            # 列表项
            item = parse_yaml_value(stripped[2:])
            if current_key and isinstance(current_list, list):
                current_list.append(item)
            continue
        if ":" in stripped:
            # 保存上一个列表
            if current_key and current_list is not None:
                meta[current_key] = current_list
                current_list = None
            key, _, val = stripped.partition(":")
            current_key = key.strip()
            val = val.strip()
            if val == "":
                # 可能是一个列表开始
                current_list = []
            else:
                meta[current_key] = parse_yaml_value(val)
        else:
            if current_key and current_list is not None:
                current_list.append(parse_yaml_value(stripped))
    if current_key and current_list is not None:
        meta[current_key] = current_list
    return meta, body


def fmt(val):
    """将 Python 值转为适合 SQLite TEXT 的字符串。"""
    if val is None:
        return None
    if isinstance(val, (datetime,)):
        return val.isoformat()
    if isinstance(val, (list, dict)):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


def migrate_users(db, log):
    path = PROJECT_ROOT / "data" / "users.json"
    data = load_json(path)
    if not data:
        log("users.json 不存在或为空，跳过")
        return

    count = 0
    for username, user_data in data.items():
        existing = db.execute(
            "SELECT 1 FROM users WHERE username = ?", (username,)
        ).fetchone()
        if existing:
            log(f"  用户 {username} 已存在，跳过")
            continue
        db.execute(
            """INSERT INTO users
               (username, hashed_password, created_at, avatar_url)
               VALUES (?, ?, ?, ?)""",
            (
                username,
                user_data.get("hashed_password", ""),
                fmt(user_data.get("created_at", datetime.now(timezone.utc))),
                user_data.get("avatar_url"),
            ),
        )
        count += 1
    db.commit()
    log(f"  [OK] 导入 {count} 个用户")


def migrate_sessions_and_cards(db, log):
    """迁移会话元数据（仅记录已存在）和卡片到 per-session .db 文件。"""
    from backend.storage.session_database import SessionDatabaseManager

    cards_dir = PROJECT_ROOT / "cards"
    if not cards_dir.exists():
        log("cards/ 目录不存在，跳过")
        return

    session_count = 0
    card_count = 0
    for user_dir in cards_dir.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith("."):
            continue
        username = user_dir.name

        # card *.md files per session dir
        for session_dir in user_dir.iterdir():
            if not session_dir.is_dir() or session_dir.name.startswith("."):
                continue
            session_id = session_dir.name

            # Skip if session already has a session.db
            if (session_dir / "session.db").exists():
                log(f"  会话 {username}/{session_id} 已有 session.db，跳过 .md 读取")
                sdb = SessionDatabaseManager(username=username, session_id=session_id)
                db_count = sdb.card_count
                sdb.close()
                session_count += 1
                card_count += db_count
                continue

            # Initialize per-session DB
            sdb = SessionDatabaseManager(username=username, session_id=session_id)
            session_cards = 0
            for md_file in sorted(session_dir.glob("*.md")):
                card_id = md_file.stem
                if sdb.execute("SELECT 1 FROM cards WHERE id = ?", (card_id,)).fetchone():
                    continue
                try:
                    raw = md_file.read_text(encoding="utf-8")
                except Exception:
                    log(f"    无法读取 {md_file}，跳过")
                    continue
                meta, body = parse_frontmatter_proper(raw)

                title = meta.get("title", card_id)
                if isinstance(title, (list, dict)):
                    title = str(title)
                created_at = meta.get("created_at", datetime.now(timezone.utc).isoformat())
                if isinstance(created_at, (datetime,)):
                    created_at = created_at.isoformat()
                updated_at = meta.get("updated_at", created_at)
                if isinstance(updated_at, (datetime,)):
                    updated_at = updated_at.isoformat()

                body = body or ""
                leading = f"# {title}"
                if body.lstrip().startswith(leading):
                    lines = body.splitlines()
                    if len(lines) >= 3 and lines[0].strip() == leading and lines[1].strip() == "":
                        body = "\n".join(lines[2:]).strip()

                sdb.execute(
                    """INSERT INTO cards
                       (id, title, content,
                        links, backlinks, created_at, updated_at,
                        sources, confidence, tags, metadata)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        card_id,
                        title,
                        body,
                        fmt(meta.get("links", [])),
                        fmt(meta.get("backlinks", [])),
                        str(created_at),
                        str(updated_at),
                        fmt(meta.get("sources", [])),
                        meta.get("confidence", 0.0),
                        fmt(meta.get("tags", [])),
                        fmt(meta.get("metadata", {})),
                    ),
                )
                session_cards += 1
                card_count += 1

            sdb.commit()
            sdb.close()
            session_count += 1
            log(f"  会话 {username}/{session_id}: 导入 {session_cards} 张卡片到 session.db")

    log(f"  [OK] 处理 {session_count} 个会话")
    log(f"  [OK] 导入 {card_count} 张卡片")


def migrate_hub(db, log):
    hub_dir = PROJECT_ROOT / "Hub"
    if not hub_dir.exists():
        log("Hub/ 目录不存在，跳过")
        return

    total_manifests = 0
    for creator_dir in hub_dir.iterdir():
        if not creator_dir.is_dir() or creator_dir.name.startswith("."):
            continue
        for session_dir in creator_dir.iterdir():
            if not session_dir.is_dir():
                continue
            mf = session_dir / "manifest.json"
            if not mf.exists():
                continue
            creator = creator_dir.name
            name = session_dir.name

            existing = db.execute(
                "SELECT 1 FROM hub_sessions WHERE creator = ? AND name = ?",
                (creator, name),
            ).fetchone()
            if existing:
                log(f"  Hub {creator}/{name} 已存在，跳过")
                continue

            manifest = load_json(mf)
            if not manifest:
                continue

            db.execute(
                """INSERT INTO hub_sessions
                   (creator, name, description, created_at, updated_at,
                    card_count, topics, likes, dislikes,
                    liked_by, disliked_by, comments, graph)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    creator,
                    name,
                    manifest.get("description", ""),
                    manifest.get("created_at", datetime.now(timezone.utc).isoformat()),
                    manifest.get("updated_at", datetime.now(timezone.utc).isoformat()),
                    manifest.get("card_count", 0),
                    json.dumps(manifest.get("topics", []), ensure_ascii=False),
                    manifest.get("likes", 0),
                    manifest.get("dislikes", 0),
                    json.dumps(manifest.get("liked_by", []), ensure_ascii=False),
                    json.dumps(manifest.get("disliked_by", []), ensure_ascii=False),
                    json.dumps(manifest.get("comments", []), ensure_ascii=False, default=str),
                    json.dumps(manifest.get("graph", {}), ensure_ascii=False),
                ),
            )
            total_manifests += 1
    db.commit()
    log(f"  [OK] 导入 {total_manifests} 个 Hub session")


def migrate_share_tokens(db, log):
    path = PROJECT_ROOT / "backend" / "share" / "tokens.json"
    data = load_json(path)
    if not data:
        log("share/tokens.json 不存在或为空，跳过")
        return

    count = 0
    for token, info in data.items():
        existing = db.execute(
            "SELECT 1 FROM share_tokens WHERE token = ?", (token,)
        ).fetchone()
        if existing:
            continue
        db.execute(
            "INSERT INTO share_tokens (token, username, session_id, created_at) VALUES (?, ?, ?, ?)",
            (
                token,
                info.get("username", ""),
                info.get("session_id", ""),
                info.get("created_at", datetime.now(timezone.utc).isoformat()),
            ),
        )
        count += 1
    db.commit()
    log(f"  [OK] 导入 {count} 个分享令牌")


def main():
    from backend.storage.database import DatabaseManager

    db_path = os.path.join(os.getcwd(), "data", "knowledgediver.db")
    # 强制重新初始化 DatabaseManager（确保 schema 是最新的）
    if DatabaseManager._instance is not None:
        DatabaseManager._instance.close()
    db = DatabaseManager(db_path)

    def log(msg):
        print(msg)

    print("=" * 50)
    print("文件系统 → SQLite 数据迁移")
    print("=" * 50)
    print(f"数据库: {db.db_path}")
    print()

    print("[1/4] 用户数据")
    migrate_users(db, log)
    print()
    print("[2/4] 会话和卡片（迁移到 per-session .db）")
    migrate_sessions_and_cards(db, log)
    print()
    print("[3/4] Hub 论坛")
    migrate_hub(db, log)
    print()
    print("[4/4] 分享令牌")
    migrate_share_tokens(db, log)
    print()

    print("=" * 50)
    print("迁移完成，检查结果：")
    for table in ["users", "hub_sessions", "share_tokens"]:
        row = db.execute(f"SELECT COUNT(*) AS cnt FROM {table}").fetchone()
        print(f"  {table}: {row['cnt']} 条记录")
    print("=" * 50)


if __name__ == "__main__":
    main()

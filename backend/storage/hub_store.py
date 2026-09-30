"""Hub storage — manages shared sessions in Hub/{username}/{session_name}/."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

from backend.models.hub import HubManifest, HubSessionSummary, HubComment
from backend.storage.base import BaseHubStore
from backend.storage.frontmatter_utils import generate_frontmatter


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HUB_DIR = PROJECT_ROOT / "Hub"


class _LockContext:
    """File-based mutual exclusion lock.

    Uses O_CREAT | O_EXCL for atomic lock file creation.
    Busy-waits with 10ms sleep on collision.

    NOTE: NOT RE-ENTRANT. Do not nest calls that acquire the same lock.
    `_locked_manifest` wraps entire read-modify-write cycles — callers
    should use it rather than acquiring the lock directly.
    """
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._acquired = False

    def __enter__(self):
        while True:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self._acquired = True
                break
            except FileExistsError:
                time.sleep(0.01)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._acquired and os.path.exists(self.lock_path):
            try:
                os.remove(self.lock_path)
            except FileNotFoundError:
                pass
        return False


class HubStore(BaseHubStore):
    def __init__(self):
        HUB_DIR.mkdir(parents=True, exist_ok=True)

    # ── path helpers ──────────────────────────────────────────────

    def _user_dir(self, username: str) -> Path:
        return HUB_DIR / username

    def _validate_session_name(self, name: str) -> None:
        """Validate session name to prevent path traversal attacks."""
        if not name or not isinstance(name, str):
            raise ValueError("会话名称不能为空")
        if name.strip() != name:
            raise ValueError("会话名称不能包含前后空格")
        if ".." in name or "/" in name or "\\" in name:
            raise ValueError("会话名称不能包含路径分隔符")
        if not re.match(r'^[\w\-. \u4e00-\u9fff\u3400-\u4dbf]+$', name):
            raise ValueError("会话名称包含无效字符")

    def _session_dir(self, username: str, session_name: str) -> Path:
        self._validate_session_name(session_name)
        return self._user_dir(username) / session_name

    def _manifest_path(self, username: str, session_name: str) -> Path:
        return self._session_dir(username, session_name) / "manifest.json"

    def _manifest_lock_path(self, username: str, session_name: str) -> str:
        return str(self._manifest_path(username, session_name)) + ".lock"

    # ── manifest read / write ─────────────────────────────────────

    def _read_manifest(self, username: str, session_name: str) -> Optional[HubManifest]:
        path = self._manifest_path(username, session_name)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        comments = []
        for c in data.get("comments", []):
            try:
                created_at = datetime.fromisoformat(c["created_at"])
            except (KeyError, ValueError, TypeError):
                created_at = datetime.utcnow()
            comments.append(HubComment(
                username=c["username"],
                content=c["content"],
                created_at=created_at,
            ))
        return HubManifest(
            name=data["name"],
            description=data.get("description", ""),
            creator=data["creator"],
            created_at=datetime.fromisoformat(data["created_at"]),
            updated_at=datetime.fromisoformat(data["updated_at"]),
            card_count=data.get("card_count", 0),
            topics=data.get("topics", []),
            graph=data.get("graph", {}),
            likes=data.get("likes", 0),
            dislikes=data.get("dislikes", 0),
            liked_by=data.get("liked_by", []),
            disliked_by=data.get("disliked_by", []),
            comments=comments,
        )

    def _write_manifest(self, manifest: HubManifest):
        username = manifest.creator
        session_name = manifest.name
        self._session_dir(username, session_name).mkdir(parents=True, exist_ok=True)
        path = self._manifest_path(username, session_name)
        data = {
            "name": manifest.name,
            "description": manifest.description,
            "creator": manifest.creator,
            "created_at": manifest.created_at.isoformat(),
            "updated_at": manifest.updated_at.isoformat(),
            "card_count": manifest.card_count,
            "topics": manifest.topics,
            "graph": manifest.graph,
            "likes": manifest.likes,
            "dislikes": manifest.dislikes,
            "liked_by": manifest.liked_by,
            "disliked_by": manifest.disliked_by,
            "comments": [
                {"username": c.username, "content": c.content,
                 "created_at": c.created_at.isoformat() if c.created_at else datetime.utcnow().isoformat()}
                for c in manifest.comments
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _locked_manifest(self, username: str, session_name: str):
        """Context manager: acquire manifest lock for atomic read-modify-write."""
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            lock_path = self._manifest_lock_path(username, session_name)
            with _LockContext(lock_path):
                yield

        return _ctx()

    def _build_graph(self, cards: list) -> dict:
        """Build nodes/edges graph from card list (mirrors frontend cardsToGraph logic)."""
        nodes = []
        edges = []
        card_ids = set()
        for card in cards:
            cid = getattr(card, "id", "")
            title = getattr(card, "title", "")
            card_ids.add(cid)
            nodes.append({"id": cid, "label": title})
        edge_set = set()
        for card in cards:
            cid = getattr(card, "id", "")
            links = getattr(card, "links", []) or []
            for link_id in links:
                if link_id in card_ids:
                    key = tuple(sorted([cid, link_id]))
                    if key not in edge_set:
                        edge_set.add(key)
                        edges.append({"from": cid, "to": link_id})
        return {"nodes": nodes, "edges": edges}

    # ── public methods ────────────────────────────────────────────

    def session_exists(self, username: str, session_name: str) -> bool:
        return self._manifest_path(username, session_name).exists()

    def share_session(self, username: str, session_name: str, cards: list,
                      description: str = "", topics: List[str] = None,
                      source_dir: str = "") -> HubManifest:
        """Copy cards from source_dir to Hub directory and create/update manifest.json."""
        topics = topics or []
        sd = self._session_dir(username, session_name)
        if sd.exists():
            shutil.rmtree(sd)
        sd.mkdir(parents=True, exist_ok=True)

        for card in cards:
            cid = getattr(card, "id", "")
            if not cid:
                continue
            frontmatter = generate_frontmatter(card)
            title = getattr(card, "title", "")
            content = getattr(card, "content", "")
            md_content = f"{frontmatter}\n\n# {title}\n\n{content}"
            md_path = sd / f"{cid}.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)

        graph = self._build_graph(cards)
        now = datetime.utcnow()
        manifest = HubManifest(
            name=session_name,
            description=description,
            creator=username,
            created_at=now,
            updated_at=now,
            card_count=len(cards),
            topics=topics,
            graph=graph,
        )
        with self._locked_manifest(username, session_name):
            self._write_manifest(manifest)
        return manifest

    def unshare_session(self, username: str, session_name: str):
        sd = self._session_dir(username, session_name)
        if sd.exists():
            shutil.rmtree(sd)

    def list_sessions(self, query: str = "", sort_by: str = "newest",
                      page: int = 1, page_size: int = 20,
                      creator: str = "") -> List[HubSessionSummary]:
        """Scan Hub directory for all manifest.json files, filter and sort."""
        summaries = []
        if not HUB_DIR.exists():
            return summaries

        user_dirs = [HUB_DIR / creator] if creator else list(HUB_DIR.iterdir())
        for user_dir in user_dirs:
            if not user_dir.is_dir():
                continue
            for session_dir in user_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                try:
                    manifest = self._read_manifest(user_dir.name, session_dir.name)
                except ValueError as e:
                    logger.warning("Hub list: skipping invalid session dir '%s/%s': %s",
                                   user_dir.name, session_dir.name, e)
                    continue
                if manifest is None:
                    continue
                if query:
                    q = query.lower()
                    text = f"{manifest.name} {manifest.description} {' '.join(manifest.topics)}".lower()
                    if q not in text:
                        continue
                summaries.append(HubSessionSummary(
                    name=manifest.name,
                    description=manifest.description,
                    creator=manifest.creator,
                    created_at=manifest.created_at,
                    updated_at=manifest.updated_at,
                    card_count=manifest.card_count,
                    topics=manifest.topics,
                    likes=manifest.likes,
                    dislikes=manifest.dislikes,
                    comment_count=len(manifest.comments),
                ))

        now = datetime.utcnow()
        if sort_by == "most_likes":
            summaries.sort(key=lambda s: s.likes, reverse=True)
        elif sort_by == "trending":
            def trending_score(s: HubSessionSummary) -> float:
                age_hours = max((now - s.created_at).total_seconds() / 3600, 1)
                return s.likes / age_hours
            summaries.sort(key=trending_score, reverse=True)
        else:  # newest
            summaries.sort(key=lambda s: s.created_at, reverse=True)

        start = (page - 1) * page_size
        return summaries[start:start + page_size]

    def get_user_stats(self, username: str) -> dict:
        """Return aggregate stats for a user's Hub sessions."""
        sessions = self.list_sessions(creator=username, page=1, page_size=1000)
        total_likes = sum(s.likes for s in sessions)
        total_sessions = len(sessions)
        return {
            "username": username,
            "total_sessions": total_sessions,
            "total_likes": total_likes,
        }

    def get_user_sessions(self, username: str, sort_by: str = "newest",
                          page: int = 1, page_size: int = 20) -> List[HubSessionSummary]:
        """List sessions created by a specific user."""
        return self.list_sessions(creator=username, sort_by=sort_by, page=page, page_size=page_size)

    def get_session_detail(self, username: str, session_name: str) -> Optional[dict]:
        """Return manifest + cards list for a Hub session."""
        manifest = self._read_manifest(username, session_name)
        if manifest is None:
            return None
        sd = self._session_dir(username, session_name)
        cards = []
        for md_file in sorted(sd.glob("*.md")):
            from backend.storage.frontmatter_utils import parse_frontmatter
            with open(md_file, "r", encoding="utf-8") as f:
                content = f.read()
            metadata, body = parse_frontmatter(content)
            # include the body content for full card rendering
            metadata["content"] = body.strip()
            cards.append(metadata)
        return {"manifest": manifest, "cards": cards}

    def like_session(self, username: str, session_name: str, voter: str) -> Optional[HubManifest]:
        with self._locked_manifest(username, session_name):
            manifest = self._read_manifest(username, session_name)
            if manifest is None:
                return None
            if voter in manifest.liked_by:
                return manifest  # already liked, no-op
            # remove from disliked_by if present
            if voter in manifest.disliked_by:
                manifest.disliked_by.remove(voter)
                manifest.dislikes = max(0, manifest.dislikes - 1)
            manifest.liked_by.append(voter)
            manifest.likes += 1
            self._write_manifest(manifest)
            return manifest

    def dislike_session(self, username: str, session_name: str, voter: str) -> Optional[HubManifest]:
        with self._locked_manifest(username, session_name):
            manifest = self._read_manifest(username, session_name)
            if manifest is None:
                return None
            if voter in manifest.disliked_by:
                return manifest  # already disliked, no-op
            # remove from liked_by if present
            if voter in manifest.liked_by:
                manifest.liked_by.remove(voter)
                manifest.likes = max(0, manifest.likes - 1)
            manifest.disliked_by.append(voter)
            manifest.dislikes += 1
            self._write_manifest(manifest)
            return manifest

    def add_comment(self, username: str, session_name: str, commenter: str, content: str) -> Optional[HubManifest]:
        with self._locked_manifest(username, session_name):
            manifest = self._read_manifest(username, session_name)
            if manifest is None:
                return None
            manifest.comments.append(HubComment(username=commenter, content=content))
            self._write_manifest(manifest)
            return manifest

    def delete_comment(self, username: str, session_name: str, index: int,
                       author: str = "") -> Optional[HubManifest]:
        with self._locked_manifest(username, session_name):
            manifest = self._read_manifest(username, session_name)
            if manifest is None:
                return None
            if index < 0 or index >= len(manifest.comments):
                raise IndexError("Comment not found")
            if author and manifest.comments[index].username != author:
                raise PermissionError("You can only delete your own comments")
            if 0 <= index < len(manifest.comments):
                manifest.comments.pop(index)
            self._write_manifest(manifest)
            return manifest

    def import_session(self, creator_username: str, session_name: str,
                       target_username: str) -> Optional[str]:
        """Import a Hub session into target user's workspace. Returns new session_id.
        Checks for duplicate session names and appends suffix if needed.
        Cards are parsed from Hub .md files and written to the per-session SQLite DB."""
        sd = self._session_dir(creator_username, session_name)
        if not sd.exists():
            return None

        # check for duplicate session name in target user's workspace
        from backend.storage.session_store import SessionStore
        store = SessionStore(username=target_username)
        sessions = store._load_sessions()

        # determine unique session name
        final_name = session_name
        existing_names = {s.get("name", "") for s in sessions.values()}
        if final_name in existing_names:
            suffix = 1
            while f"{session_name}_{suffix}" in existing_names:
                suffix += 1
            final_name = f"{session_name}_{suffix}"

        import uuid as _uuid
        new_session_id = str(_uuid.uuid4())

        # Write session metadata (SqliteCardStore will create the directory + session.db)
        now = datetime.utcnow()
        sessions[new_session_id] = {
            "id": new_session_id,
            "name": final_name,
            "created_at": now,
            "updated_at": now,
            "card_count": 0,
        }
        store._save_sessions(sessions)

        from backend.storage.sqlite_card_store import SqliteCardStore
        from backend.storage.frontmatter_utils import parse_frontmatter
        from backend.models.card import Card

        card_store = SqliteCardStore(username=target_username, session_id=new_session_id)

        # Collect existing UUIDs across all user sessions to avoid conflicts
        existing_ids = set()
        for s in store.list_sessions():
            cs = SqliteCardStore(username=target_username, session_id=s.id)
            for c in cs.list_cards():
                existing_ids.add(c.id)

        for md_file in sorted(sd.glob("*.md")):
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    raw = f.read()
            except Exception as e:
                logger.warning("Import card file read skipped: %s", e)
                continue

            try:
                meta, body = parse_frontmatter(raw)
            except Exception as e:
                logger.warning("Import frontmatter parse skipped: %s", e)
                continue

            original_id = meta.get("id", "")
            if not original_id or original_id in existing_ids:
                original_id = str(_uuid.uuid4())

            title = meta.get("title", md_file.stem)
            content = body or ""
            leading_title = f"# {title}"
            if content.lstrip().startswith(leading_title):
                lines = content.splitlines()
                if len(lines) >= 3 and lines[0].strip() == leading_title and lines[1].strip() == "":
                    content = "\n".join(lines[2:]).strip()

            card = Card(
                id=original_id,
                title=title,
                content=content,
                metadata=meta.get("metadata", {}) if isinstance(meta.get("metadata"), dict) else {},
                links=meta.get("links", []) if isinstance(meta.get("links"), list) else [],
                backlinks=meta.get("backlinks", []) if isinstance(meta.get("backlinks"), list) else [],
                created_at=now,
                updated_at=now,
                sources=meta.get("sources", []) if isinstance(meta.get("sources"), list) else [],
                confidence=float(meta.get("confidence", 1.0)) if meta.get("confidence") else 1.0,
                tags=meta.get("tags", []) if isinstance(meta.get("tags"), list) else [],
            )
            card_store.create_card(card)
            existing_ids.add(original_id)

        sessions[new_session_id]["card_count"] = card_store.db.card_count
        store._save_sessions(sessions)

        return new_session_id

"""Card storage operations using filesystem-backed Markdown files."""

from __future__ import annotations

import io
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

from backend.config import DEFAULT_SESSION_ID
from backend.models.card import Card
from backend.storage.base import BaseCardStore
from backend.storage.session_store import SessionStore
from .frontmatter_utils import parse_frontmatter, generate_frontmatter


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CARDS_DIR = PROJECT_ROOT / "cards"


class _LockContext:
    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._acquired = False

    def __enter__(self):
        # Simple, cross-platform coarse-grained lock using a lock file.
        while True:
            try:
                # O_CREAT|O_EXCL ensures we only create if not exists
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


class CardStore(BaseCardStore):
    def __init__(self, base_dir: Optional[str] = None, username: Optional[str] = None, session_id: Optional[str] = None):
        if base_dir:
            self.base_dir = Path(base_dir)
        elif username:
            self.base_dir = CARDS_DIR / username
            # If session_id is provided, append it to the path
            if session_id:
                self.base_dir = self.base_dir / session_id
            else:
                # Default to "default" session for backward compatibility
                self.base_dir = self.base_dir / "default"
        else:
            self.base_dir = CARDS_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.username = username
        self.session_id = session_id or DEFAULT_SESSION_ID

    def _update_session_card_count(self):
        if self.username:
            session_store = SessionStore(username=self.username)
            session_store._update_session_card_count(self.session_id)

    def _file_path(self, card_id: str) -> str:
        return str(self.base_dir / f"{card_id}.md")

    def _lock_path(self, file_path: str) -> str:
        return f"{file_path}.lock"

    def create_card(self, card: Card) -> Card:
        path = self._file_path(card.id)
        if os.path.exists(path):
            raise FileExistsError(f"Card {card.id} already exists")
        frontmatter_text = generate_frontmatter(card)
        body = f"\n\n# {card.title}\n\n{card.content}"
        lock_path = self._lock_path(path)
        with _LockContext(lock_path):
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(frontmatter_text)
                f.write(body)
        self._update_session_card_count()
        return card

    def read_card(self, card_id: str) -> Optional[Card]:
        path = self._file_path(card_id)
        if not os.path.exists(path):
            return None
        with io.open(path, "r", encoding="utf-8") as f:
            content = f.read()
        metadata, body = parse_frontmatter(content)
        # Reconstruct Card from metadata and body
        def _parse_dt(dt: Optional[str]) -> Optional[datetime]:
            if dt is None:
                return None
            if isinstance(dt, datetime):
                return dt
            # Try ISO formats with/without Z
            try:
                return datetime.fromisoformat(dt)
            except Exception:
                try:
                    return datetime.strptime(dt, "%Y-%m-%dT%H:%M:%SZ")
                except Exception:
                    return None

        title = metadata.get("title")
        # Extract content by removing potential leading title heading
        content = body or ""
        leading_title = f"# {title}" if title else None
        if leading_title and content.lstrip().startswith(leading_title):
            lines = content.splitlines()
            if len(lines) >= 3 and lines[0].strip() == leading_title and lines[1].strip() == "":
                content = "\n".join(lines[2:]).strip()
        card = Card(
            id=metadata.get("id") or card_id,
            title=title or "",
            content=content,
            metadata=metadata.get("metadata", {}) if isinstance(metadata.get("metadata"), dict) else {},
            links=metadata.get("links") or [],
            backlinks=metadata.get("backlinks") or [],
            created_at=_parse_dt(metadata.get("created_at")) or datetime.utcnow(),
            updated_at=_parse_dt(metadata.get("updated_at")) or datetime.utcnow(),
            sources=metadata.get("sources") or [],
            confidence=float(metadata.get("confidence", 0.0)) if metadata.get("confidence") is not None else 0.0,
            tags=metadata.get("tags") or [],
        )
        return card

    def update_card(self, card: Card) -> Card:
        path = self._file_path(card.id)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Card {card.id} does not exist")
        frontmatter_text = generate_frontmatter(card)
        body = f"\n\n# {card.title}\n\n{card.content}"
        lock_path = self._lock_path(path)
        with _LockContext(lock_path):
            with io.open(path, "w", encoding="utf-8") as f:
                f.write(frontmatter_text)
                f.write(body)
        return card

    def delete_card(self, card_id: str) -> bool:
        path = self._file_path(card_id)
        if not os.path.exists(path):
            return False
        try:
            os.remove(path)
            self._update_session_card_count()
            return True
        except OSError:
            return False

    def list_cards(self) -> List[Card]:
        cards: List[Card] = []
        for entry in self.base_dir.glob("*.md"):
            card = self.read_card(entry.stem)
            if card:
                cards.append(card)
        return cards

    def card_exists(self, card_id: str) -> bool:
        return os.path.exists(self._file_path(card_id))

    def get_card(self, card_id: str) -> Optional[Card]:
        """Alias for read_card for compatibility with LinkManager."""
        return self.read_card(card_id)

    def save_cards(self, cards: List[Card]) -> List[Card]:
        """Save multiple cards. Creates new cards or updates existing ones."""
        saved = []
        for card in cards:
            path = self._file_path(card.id)
            frontmatter_text = generate_frontmatter(card)
            body = f"\n\n# {card.title}\n\n{card.content}"
            lock_path = self._lock_path(path)
            with _LockContext(lock_path):
                with io.open(path, "w", encoding="utf-8") as f:
                    f.write(frontmatter_text)
                    f.write(body)
            saved.append(card)
        return saved

    def find_by_title(self, title: str) -> Optional[Card]:
        """遍历所有卡片文件查找标题匹配（文件系统模式，较慢）。"""
        for card in self.list_cards():
            if card.title == title:
                return card
        return None

    def find_similar(self, embedding, threshold=0.85, limit=3):
        """文件系统模式不支持向量搜索。"""
        return []


class InMemoryCardStore(BaseCardStore):
    """In-memory card store for testing purposes."""
    
    def __init__(self, cards: Optional[Iterable[Card]] = None):
        self._cards: dict = {}
        if cards:
            for card in cards:
                self._cards[card.id] = card
    
    def create_card(self, card: Card) -> Card:
        if card.id in self._cards:
            raise FileExistsError(f"Card {card.id} already exists")
        self._cards[card.id] = card
        return card
    
    def read_card(self, card_id: str) -> Optional[Card]:
        return self._cards.get(card_id)
    
    def get_card(self, card_id: str) -> Optional[Card]:
        return self.read_card(card_id)
    
    def update_card(self, card: Card) -> Card:
        if card.id not in self._cards:
            raise FileNotFoundError(f"Card {card.id} does not exist")
        self._cards[card.id] = card
        return card
    
    def delete_card(self, card_id: str) -> bool:
        if card_id in self._cards:
            del self._cards[card_id]
            return True
        return False
    
    def list_cards(self) -> List[Card]:
        return list(self._cards.values())
    
    def card_exists(self, card_id: str) -> bool:
        return card_id in self._cards

    def save_cards(self, cards: List[Card]) -> List[Card]:
        """Save multiple cards. Creates new cards or updates existing ones."""
        for card in cards:
            self._cards[card.id] = card
        return list(cards)

    def find_by_title(self, title: str) -> Optional[Card]:
        """查找标题精确匹配的卡牌（用于去重）。"""
        for card in self._cards.values():
            if card.title == title:
                return card
        return None

    def find_similar(self, embedding: list[float], threshold: float = 0.85, limit: int = 3) -> list[tuple[str, float]]:
        """内存模式不支持向量搜索，返回空列表。"""
        return []

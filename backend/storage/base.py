"""
Abstract base classes for all Store implementations.

Defines the interface contract that every Store must satisfy,
enabling future storage backend swaps (e.g. filesystem → SQLite)
with compile-time safety rather than convention alone.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Iterable, List, Optional

from backend.models.card import Card
from backend.models.session import Session
from backend.models.user import User
from backend.models.hub import HubManifest, HubSessionSummary


# ═══════════════════════════════════════════════════════════════
# BaseCardStore
# ═══════════════════════════════════════════════════════════════

class BaseCardStore(ABC):
    """Abstract interface for card storage.

    Implementations: CardStore (filesystem), InMemoryCardStore (tests).
    """

    @abstractmethod
    def create_card(self, card: Card) -> Card:
        """Create a new card. Raises FileExistsError if card.id already exists."""
        ...

    @abstractmethod
    def read_card(self, card_id: str) -> Optional[Card]:
        """Read a single card by ID. Returns None if not found."""
        ...

    @abstractmethod
    def get_card(self, card_id: str) -> Optional[Card]:
        """Alias for read_card (used by LinkManager / Classifier)."""
        ...

    @abstractmethod
    def update_card(self, card: Card) -> Card:
        """Update an existing card. Raises FileNotFoundError if not found."""
        ...

    @abstractmethod
    def delete_card(self, card_id: str) -> bool:
        """Delete a card by ID. Returns True on success, False if not found."""
        ...

    @abstractmethod
    def list_cards(self) -> List[Card]:
        """List all cards in this store."""
        ...

    @abstractmethod
    def card_exists(self, card_id: str) -> bool:
        """Check whether a card with the given ID exists."""
        ...

    @abstractmethod
    def save_cards(self, cards: List[Card]) -> List[Card]:
        """Batch save (create or update) multiple cards."""
        ...


# ═══════════════════════════════════════════════════════════════
# BaseSessionStore
# ═══════════════════════════════════════════════════════════════

class BaseSessionStore(ABC):
    """Abstract interface for session storage.

    Implementation: SessionStore (JSON-backed filesystem).
    """

    @abstractmethod
    def list_sessions(self) -> List[Session]:
        """List all sessions for the user (real-time card counts)."""
        ...

    @abstractmethod
    def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID. Returns None if not found."""
        ...

    @abstractmethod
    def create_session(self, name: str) -> Session:
        """Create a new session (UUID-based, duplicate-name check)."""
        ...

    @abstractmethod
    def update_session(self, session_id: str, name: str) -> Optional[Session]:
        """Rename a session. Returns None if not found."""
        ...

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """Delete a session and its card directory (default session is protected)."""
        ...

    @abstractmethod
    def get_session_dir(self, session_id: str) -> Path:
        """Get the filesystem path for a session's card directory."""
        ...

    @abstractmethod
    def move_cards_to_session(
        self, source_session_id: str, target_session_id: str, card_ids: List[str]
    ) -> int:
        """Move cards between sessions. Returns count of moved cards."""
        ...


# ═══════════════════════════════════════════════════════════════
# BaseUserStore
# ═══════════════════════════════════════════════════════════════

class BaseUserStore(ABC):
    """Abstract interface for user storage.

    Implementation: UserStore (JSON-backed filesystem).
    """

    @abstractmethod
    def user_exists(self, username: str) -> bool:
        """Check whether a username is already registered."""
        ...

    @abstractmethod
    def create_user(self, username: str, password: str) -> User:
        """Create a new user (password auto-hashed). Raises ValueError if duplicate."""
        ...

    @abstractmethod
    def get_user(self, username: str) -> Optional[User]:
        """Get a User model instance by username."""
        ...

    @abstractmethod
    def verify_user(self, username: str, password: str) -> Optional[User]:
        """Verify credentials. Returns User on success, None on failure."""
        ...

    @abstractmethod
    def update_user(self, username: str, **kwargs) -> None:
        """Update arbitrary user fields (e.g. avatar_url)."""
        ...

    @abstractmethod
    def get_user_cards_dir(self, username: str) -> str:
        """Get the user's card storage directory path."""
        ...


# ═══════════════════════════════════════════════════════════════
# BaseHubStore
# ═══════════════════════════════════════════════════════════════

class BaseHubStore(ABC):
    """Abstract interface for Hub (forum) storage.

    Implementation: HubStore (directory-per-session filesystem).
    """

    @abstractmethod
    def session_exists(self, username: str, session_name: str) -> bool:
        """Check if a Hub session already exists."""
        ...

    @abstractmethod
    def share_session(
        self,
        username: str,
        session_name: str,
        cards: list,
        description: str = "",
        topics: Optional[List[str]] = None,
        source_dir: str = "",
    ) -> HubManifest:
        """Copy cards to Hub and create manifest. Returns the manifest."""
        ...

    @abstractmethod
    def unshare_session(self, username: str, session_name: str) -> None:
        """Remove a session from the Hub."""
        ...

    @abstractmethod
    def list_sessions(
        self,
        query: str = "",
        sort_by: str = "newest",
        page: int = 1,
        page_size: int = 20,
        creator: str = "",
    ) -> List[HubSessionSummary]:
        """List Hub sessions with filtering and sorting."""
        ...

    @abstractmethod
    def get_user_stats(self, username: str) -> dict:
        """Aggregate stats for a user's Hub presence."""
        ...

    @abstractmethod
    def get_user_sessions(
        self, username: str, sort_by: str = "newest", page: int = 1, page_size: int = 20
    ) -> List[HubSessionSummary]:
        """List sessions created by a specific user."""
        ...

    @abstractmethod
    def get_session_detail(self, username: str, session_name: str) -> Optional[dict]:
        """Get full session detail: manifest + card list."""
        ...

    @abstractmethod
    def like_session(self, username: str, session_name: str, voter: str) -> Optional[HubManifest]:
        """Like a session (dedup via liked_by). Returns updated manifest."""
        ...

    @abstractmethod
    def dislike_session(self, username: str, session_name: str, voter: str) -> Optional[HubManifest]:
        """Dislike a session (mutually exclusive with like). Returns updated manifest."""
        ...

    @abstractmethod
    def add_comment(self, username: str, session_name: str, commenter: str, content: str) -> Optional[HubManifest]:
        """Add a comment to a Hub session."""
        ...

    @abstractmethod
    def delete_comment(self, username: str, session_name: str, index: int) -> Optional[HubManifest]:
        """Delete a comment by index. Returns updated manifest."""
        ...

    @abstractmethod
    def import_session(
        self, creator_username: str, session_name: str, target_username: str
    ) -> Optional[str]:
        """Import a Hub session into target user's workspace. Returns new session_id."""
        ...


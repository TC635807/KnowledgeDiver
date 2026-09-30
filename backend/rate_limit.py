"""API rate limiting via slowapi in-memory backend.

Centralizes the Limiter instance so both main.py and auth.py
can import it without creating circular imports
( auth → main → pipeline → auth ).
"""

from slowapi import Limiter
from slowapi.util import get_remote_address
from backend.config import RATE_LIMIT_DEFAULT

limiter = Limiter(key_func=get_remote_address, default_limits=[RATE_LIMIT_DEFAULT])

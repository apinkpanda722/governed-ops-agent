"""Ephemeral sessions containing only token digests."""

import hashlib
import secrets
import time
from collections.abc import Callable

SESSION_TTL_SECONDS = 8 * 3600  # 임시값, M5에서 확정


class SessionStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._sessions: dict[str, tuple[str, float]] = {}

    def issue(self, user_id: str) -> tuple[str, float]:
        token = secrets.token_urlsafe(32)
        expires = self._clock() + self._ttl_seconds
        self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (user_id, expires)
        return token, expires

    def resolve(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        session = self._sessions.get(digest)
        if session is None:
            return None
        user, expires = session
        if self._clock() >= expires:
            del self._sessions[digest]
            return None
        return user

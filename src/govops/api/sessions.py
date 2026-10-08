"""Ephemeral sessions containing only token digests."""

import hashlib
import secrets
import time
from collections.abc import Callable

SESSION_TTL_SECONDS = 8 * 3600


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

    def _purge_expired(self, now: float) -> None:
        for digest in [
            d for d, (_, expires) in self._sessions.items() if now >= expires
        ]:
            del self._sessions[digest]

    def issue(self, user_id: str) -> tuple[str, float]:
        now = self._clock()
        self._purge_expired(now)
        token = secrets.token_urlsafe(32)
        expires = now + self._ttl_seconds
        self._sessions[hashlib.sha256(token.encode()).hexdigest()] = (user_id, expires)
        return token, expires

    def resolve(self, token: str) -> str | None:
        self._purge_expired(self._clock())
        session = self._sessions.get(hashlib.sha256(token.encode()).hexdigest())
        return None if session is None else session[0]

    def revoke(self, token: str) -> bool:
        digest = hashlib.sha256(token.encode()).hexdigest()
        return self._sessions.pop(digest, None) is not None

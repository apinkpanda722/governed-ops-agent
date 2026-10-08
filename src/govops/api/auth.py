"""Session-only authenticated identity."""

import re
import threading
import time
from collections.abc import Callable

from fastapi import HTTPException, Request

_BEARER = re.compile(r"(?i:Bearer) ([A-Za-z0-9_-]+)")


class AuthFailureLimiter:
    """Thread-safe fixed windows for an application's authentication failures."""

    def __init__(
        self,
        limit: int = 60,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._start = clock()
        self._count = 0
        self._suppressed = 0
        self._lock = threading.Lock()

    def allow(self) -> dict[str, int] | None:
        with self._lock:
            now = self._clock()
            fields = {}
            if now - self._start >= self._window_seconds:
                # Suppressed counts remain unrecorded if no later failure arrives.
                if self._suppressed:
                    fields["suppressed"] = self._suppressed
                self._start = now
                self._count = 0
                self._suppressed = 0
            if self._count >= self._limit:
                self._suppressed += 1
                return None
            self._count += 1
            return fields


def record_auth_failure(request: Request, reason: str) -> None:
    """Called in synchronous handlers; auditing failures never change responses."""
    try:
        audit = request.app.state.audit
        if audit is None:
            return
        fields = request.app.state.auth_failure_limiter.allow()
        if fields is not None:
            audit.append(
                "auth.failed", task_id=None, actor_sub="system", reason=reason, **fields
            )
    except Exception:  # noqa: BLE001 — fail-open; exception text may contain secrets
        return


def authenticated_token(request: Request) -> str:
    values = request.headers.getlist("authorization")
    match = _BEARER.fullmatch(values[0]) if len(values) == 1 else None
    user = request.app.state.sessions.resolve(match.group(1)) if match else None
    if user is None:
        record_auth_failure(
            request, "invalid_token" if match else "missing_credentials"
        )
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return match.group(1)


def current_user(request: Request) -> str:
    token = authenticated_token(request)
    return request.app.state.sessions.resolve(token)

"""Session-only authenticated identity."""

import re

from fastapi import HTTPException, Request

_BEARER = re.compile(r"(?i:Bearer) ([A-Za-z0-9_-]+)")


def current_user(request: Request) -> str:
    values = request.headers.getlist("authorization")
    match = _BEARER.fullmatch(values[0]) if len(values) == 1 else None
    user = request.app.state.sessions.resolve(match.group(1)) if match else None
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user

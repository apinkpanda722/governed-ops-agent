"""Session API and ordered fail-closed startup checks."""

from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from govops.api.auth import current_user
from govops.api.sessions import SessionStore


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: StrictStr


def create_app(
    *,
    users: frozenset[str],
    sessions: SessionStore,
    demo_login: bool,
    startup_checks: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for check in startup_checks:
            await check()
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.sessions = sessions

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # FastAPI's default errors echo input, which may contain credentials.
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    if demo_login:

        @app.post("/sessions", status_code=201)
        def issue_session(body: Login, request: Request):
            if request.client is None or request.client.host not in {
                "127.0.0.1",
                "::1",
            }:
                raise HTTPException(status_code=403, detail="Loopback required")
            if body.user not in users:
                raise HTTPException(status_code=404, detail="User not found")
            token, expires = sessions.issue(body.user)
            return {"session_token": token, "expires_at": expires}

    @app.get("/sessions/me")
    def me(user_id: str = Depends(current_user)):
        return {"user_id": user_id}

    return app

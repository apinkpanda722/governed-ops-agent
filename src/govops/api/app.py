"""Session API and ordered fail-closed startup checks."""

from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from govops.api.auth import current_user
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: StrictStr


class TaskRequest(BaseModel):
    request: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator("request")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Invalid request")
        return value


def create_app(
    *,
    users: frozenset[str],
    sessions: SessionStore,
    demo_login: bool,
    service: TaskService | None = None,
    startup_checks: Sequence[Callable[[], Awaitable[None]]] = (),
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        for check in startup_checks:
            await check()
        try:
            yield
        finally:
            if service is not None:
                await service.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.sessions = sessions
    app.state.task_service = service

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

    if service is not None:

        @app.post("/tasks", status_code=202)
        async def start_task(
            body: TaskRequest, request: Request, user_id: str = Depends(current_user)
        ):
            try:
                task_id = await request.app.state.task_service.start(
                    user_id, body.request
                )
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid request") from None
            return {"task_id": task_id}

        @app.get("/tasks/{task_id}")
        async def read_task(
            task_id: str, request: Request, user_id: str = Depends(current_user)
        ):
            result = await request.app.state.task_service.get(user_id, task_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Task not found")
            return result

    return app

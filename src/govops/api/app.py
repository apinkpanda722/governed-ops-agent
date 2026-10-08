"""Session API and ordered fail-closed startup checks."""

from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from govops.api.auth import AuthFailureLimiter, current_user, record_auth_failure
from govops.api.sessions import SessionStore
from govops.api.tasks import ApprovalError, TaskService


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


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decision: Literal["approve", "reject", "edit_approve"]
    draft_sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    edited: dict[StrictStr, StrictStr] | None = None

    @model_validator(mode="after")
    def valid_edit(self):
        if (self.decision == "edit_approve") != (self.edited is not None):
            raise ValueError("Invalid edit")
        if self.decision != "edit_approve" and "edited" in self.model_fields_set:
            raise ValueError("Invalid edit")
        return self


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
    app.state.audit = service.audit if service is not None else None
    app.state.auth_failure_limiter = AuthFailureLimiter()

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
                record_auth_failure(request, "loopback_required")
                raise HTTPException(status_code=403, detail="Loopback required")
            if body.user not in users:
                record_auth_failure(request, "unknown_user")
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

        @app.post("/tasks/{task_id}/approval")
        async def approve_task(
            task_id: str,
            body: ApprovalRequest,
            request: Request,
            user_id: str = Depends(current_user),
        ):
            try:
                code, view = await request.app.state.task_service.approve(
                    user_id, task_id, **body.model_dump()
                )
            except ApprovalError as error:
                raise HTTPException(
                    status_code=error.status_code,
                    detail="Task not found"
                    if error.status_code == 404
                    else "Approval rejected",
                ) from None
            return JSONResponse(view, status_code=code)

        @app.get("/tasks/{task_id}")
        async def read_task(
            task_id: str, request: Request, user_id: str = Depends(current_user)
        ):
            result = await request.app.state.task_service.get(user_id, task_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Task not found")
            return result

    return app

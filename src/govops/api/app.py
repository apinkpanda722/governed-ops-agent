"""Session API and ordered fail-closed startup checks."""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from govops.api.auth import (
    AuthFailureLimiter,
    authenticated_token,
    current_user,
    record_auth_failure,
)
from govops.api.conversations import ConversationError
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


def get_task_service(request: Request) -> TaskService:
    """라우트가 서비스를 얻는 유일한 경로.

    실제 서버 진입점(`api/__main__.py`)은 자리표시자 서비스로 앱을 만들고 lifespan에서
    `app.state.task_service`를 실제 서비스로 바꾼다. create_app 클로저의 `service`를 라우트가
    붙잡으면 생성 시점의 자리표시자를 쓰게 된다(FP-070).
    """
    return request.app.state.task_service


TASK_SERVICE = Depends(get_task_service)


def create_app(
    *,
    users: frozenset[str],
    sessions: SessionStore,
    demo_login: bool,
    service: TaskService | None = None,
    startup_checks: Sequence[Callable[[], Awaitable[None]]] = (),
    sse_poll_seconds: float = 0.5,
    sse_heartbeat_seconds: float = 15.0,
    sse_max_seconds: float = 1800.0,
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
    app.state.sse_active_streams = 0
    app.state.sessions = sessions
    app.state.task_service = service
    app.state.audit = service.audit if service is not None else None
    app.state.auth_failure_limiter = AuthFailureLimiter()

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # FastAPI's default errors echo input, which may contain credentials.
        return JSONResponse(
            {
                "detail": "invalid_request"
                if request.url.path.startswith("/conversations")
                else "Invalid request"
            },
            status_code=422,
        )

    if demo_login:

        @app.delete("/sessions", status_code=204)
        def revoke_session(request: Request, token: str = Depends(authenticated_token)):
            request.app.state.sessions.revoke(token)
            return Response(status_code=204)

        @app.get("/demo/users")
        def demo_users(request: Request):
            if request.client is None or request.client.host not in {
                "127.0.0.1",
                "::1",
            }:
                record_auth_failure(request, "loopback_required")
                raise HTTPException(status_code=403, detail="Loopback required")
            return {"users": sorted(users)}

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

        @app.get("/conversations/{conversation_id}")
        async def read_conversation(
            conversation_id: str,
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            try:
                return await tasks.conversation_view(user_id, conversation_id)
            except ConversationError as error:
                raise HTTPException(error.status_code, detail=error.code) from None

        @app.get("/conversations/{conversation_id}/events")
        async def conversation_events(
            conversation_id: str,
            request: Request,
            user_id: str = Depends(current_user),
            task_service: TaskService = TASK_SERVICE,
        ):
            initial = await task_service.progress(user_id, conversation_id)
            if initial is None:
                raise HTTPException(404, detail="conversation_not_found")

            async def events():
                app.state.sse_active_streams += 1
                started = last_event = time.monotonic()
                previous = None
                current = initial
                seq = 0
                try:
                    while current is not None:
                        if await request.is_disconnected():
                            return
                        if current != previous:
                            turn_id, status = current
                            data = {
                                "conversation_id": conversation_id,
                                "turn_id": turn_id,
                                "status": status,
                                "seq": seq,
                            }
                            yield "event: status\ndata: " + json.dumps(data) + "\n\n"
                            previous = current
                            seq += 1
                            last_event = time.monotonic()
                            if status not in {"running", "awaiting_approval"}:
                                return
                        now = time.monotonic()
                        if now - started >= sse_max_seconds:
                            return
                        if now - last_event >= sse_heartbeat_seconds:
                            yield ": ping\n\n"
                            last_event = now
                        await asyncio.sleep(
                            min(
                                sse_poll_seconds,
                                max(0, sse_max_seconds - (now - started)),
                            )
                        )
                        if await request.is_disconnected():
                            return
                        remaining = sse_max_seconds - (time.monotonic() - started)
                        if remaining <= 0:
                            return
                        try:
                            async with asyncio.timeout(remaining):
                                current = await task_service.progress(
                                    user_id, conversation_id
                                )
                        except TimeoutError:
                            return
                finally:
                    app.state.sse_active_streams -= 1

            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache, no-transform",
                    "X-Accel-Buffering": "no",
                },
            )

        @app.get("/approvals")
        async def list_approvals(
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            return await tasks.approvals(user_id)

        @app.get("/conversations")
        async def list_conversations(
            limit: int = Query(default=50, ge=1, le=50),
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            return await tasks.conversations(user_id, limit)

        @app.post("/conversations", status_code=202)
        async def start_conversation(
            body: TaskRequest,
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            try:
                return await tasks.start_conversation(user_id, body.request)
            except ConversationError as error:
                raise HTTPException(error.status_code, detail=error.code) from None

        @app.post("/conversations/{conversation_id}/turns", status_code=202)
        async def add_turn(
            conversation_id: str,
            body: TaskRequest,
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            try:
                return await tasks.add_turn(user_id, conversation_id, body.request)
            except ConversationError as error:
                raise HTTPException(error.status_code, detail=error.code) from None

        @app.post("/tasks", status_code=202)
        async def start_task(
            body: TaskRequest,
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            try:
                task_id = await tasks.start(user_id, body.request)
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid request") from None
            return {"task_id": task_id}

        @app.post("/tasks/{task_id}/approval")
        async def approve_task(
            task_id: str,
            body: ApprovalRequest,
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            try:
                code, view = await tasks.approve(user_id, task_id, **body.model_dump())
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
            task_id: str,
            user_id: str = Depends(current_user),
            tasks: TaskService = TASK_SERVICE,
        ):
            result = await tasks.get(user_id, task_id)
            if result is None:
                raise HTTPException(status_code=404, detail="Task not found")
            return result

    return app

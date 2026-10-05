"""Request-number fault scripts; no deduplication or cross-layer dependencies.

Overlap order: before delays (file order), first before status/rate limit,
normal application with maximum hide_for, after delays (file order), drop,
first after status. A before rejection skips application and after actions.
Counters count all matched rules, including ones superseded by this order.
Use FaultH11Protocol for real transport closure without HTTP response bytes.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
from threading import RLock
from typing import Literal

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from uvicorn.protocols.http.h11_impl import H11Protocol

ENDPOINTS = {
    "slack.send",
    "slack.history",
    "slack.channels",
    "jira.comment.create",
    "jira.comment.list",
    "jira.child.create",
    "jira.child.list",
    "jira.ticket.get",
    "jira.ticket.search",
}
WRITES = {"slack.send", "jira.comment.create", "jira.child.create"}
_HIDE: ContextVar[int] = ContextVar("mock_hide_ms", default=0)


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["delay", "status", "rate_limit", "drop_after_apply", "hide_for"]
    when: Literal["before", "after_apply"] | None = None
    ms: StrictInt | None = Field(default=None, ge=0)
    status: StrictInt | None = Field(default=None, ge=500, le=599)
    seconds: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_fields(self):
        required = {
            "delay": {"when", "ms"},
            "status": {"when", "status"},
            "rate_limit": {"seconds"},
            "drop_after_apply": set(),
            "hide_for": {"ms"},
        }[self.type]
        present = {
            key
            for key in ("when", "ms", "status", "seconds")
            if getattr(self, key) is not None
        }
        if present != required:
            raise ValueError("invalid fields for fault action")
        return self


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str
    nth: Literal["all"] | list[StrictInt]
    action: Action

    @model_validator(mode="after")
    def validate_rule(self):
        if self.endpoint not in ENDPOINTS:
            raise ValueError("unknown endpoint")
        if self.nth != "all" and (
            not self.nth
            or any(n < 1 for n in self.nth)
            or len(set(self.nth)) != len(self.nth)
        ):
            raise ValueError("nth must contain distinct positive request numbers")
        if self.endpoint not in WRITES and self.action.type in {
            "hide_for",
            "drop_after_apply",
        }:
            raise ValueError("action requires a write endpoint")
        return self


class FaultState:
    def __init__(self, *, wait: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.lock = RLock()
        self.wait = wait
        self.rules: tuple[Rule, ...] = ()
        self.counters: dict[str, dict[str, int]] = {}

    def set(self, rules: list[dict] | list[Rule]) -> None:
        validated = tuple(Rule.model_validate(r).model_copy(deep=True) for r in rules)
        with self.lock:
            self.rules = validated
            self.counters = {}

    def reset(self) -> None:
        self.set([])

    def select(self, endpoint: str) -> tuple[Action, ...]:
        with self.lock:
            stats = self.counters.setdefault(
                endpoint, {"requests": 0, "applied_rules": 0}
            )
            stats["requests"] += 1
            actions = tuple(
                r.action
                for r in self.rules
                if r.endpoint == endpoint
                and (r.nth == "all" or stats["requests"] in r.nth)
            )
            stats["applied_rules"] += len(actions)
            return actions

    def stats(self) -> dict:
        with self.lock:
            return deepcopy(self.counters)


def visible_at(timestamp: str) -> str | None:
    ms = _HIDE.get()
    return (
        (datetime.fromisoformat(timestamp) + timedelta(milliseconds=ms)).isoformat()
        if ms
        else None
    )


def is_visible(effect: dict, timestamp: str) -> bool:
    return not effect.get("visible_at") or datetime.fromisoformat(
        effect["visible_at"]
    ) <= datetime.fromisoformat(timestamp)


class FaultH11Protocol(H11Protocol):
    """Expose transport closure only inside the mock server's ASGI scope."""

    def handle_events(self):
        super().handle_events()
        if self.scope is not None:
            self.scope["mock.close_transport"] = self.transport.close


def _endpoint(method: str, path: str) -> str | None:
    if path == "/channels" and method == "GET":
        return "slack.channels"
    if re.fullmatch(r"/channels/[^/]+/messages", path):
        return {"GET": "slack.history", "POST": "slack.send"}.get(method)
    if path == "/tickets" and method == "GET":
        return "jira.ticket.search"
    if re.fullmatch(r"/tickets/[^/]+", path) and method == "GET":
        return "jira.ticket.get"
    for suffix, name in [("comments", "comment"), ("children", "child")]:
        if re.fullmatch(r"/tickets/[^/]+/" + suffix, path):
            verb = {"GET": "list", "POST": "create"}.get(method)
            return f"jira.{name}.{verb}" if verb else None
    return None


class FaultMiddleware:
    def __init__(self, app, faults: FaultState):
        self.app = app
        self.faults = faults

    async def __call__(self, scope, receive, send):
        endpoint = (
            _endpoint(scope.get("method", ""), scope.get("path", ""))
            if scope["type"] == "http"
            else None
        )
        if endpoint is None:
            return await self.app(scope, receive, send)
        actions = self.faults.select(endpoint)
        for action in actions:
            if action.type == "delay" and action.when == "before":
                await self.faults.wait(action.ms / 1000)
        for action in actions:
            if action.type == "rate_limit" or (
                action.type == "status" and action.when == "before"
            ):
                response = JSONResponse(
                    {"error": {"code": "injected_fault"}},
                    status_code=429 if action.type == "rate_limit" else action.status,
                    headers={"Retry-After": str(action.seconds)}
                    if action.type == "rate_limit"
                    else None,
                )
                return await response(scope, receive, send)
        token = _HIDE.set(
            max((a.ms for a in actions if a.type == "hide_for"), default=0)
        )
        messages = []

        async def capture(message):
            messages.append(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            _HIDE.reset(token)
        applied = any(
            m["type"] == "http.response.start" and 200 <= m["status"] < 300
            for m in messages
        )
        if applied:
            for action in actions:
                if action.type == "delay" and action.when == "after_apply":
                    await self.faults.wait(action.ms / 1000)
            if any(a.type == "drop_after_apply" for a in actions):
                close = scope.get("mock.close_transport")
                if close is None:
                    raise RuntimeError("drop_after_apply requires FaultH11Protocol")
                close()
                return
            for action in actions:
                if action.type == "status" and action.when == "after_apply":
                    return await JSONResponse(
                        {"error": {"code": "injected_fault"}}, status_code=action.status
                    )(scope, receive, send)
        for message in messages:
            await send(message)


def add_admin_routes(app, faults: FaultState) -> None:
    @app.post("/faults")
    def set_faults(rules: list[Rule]) -> dict:
        faults.set(rules)
        return {"status": "ok"}

    @app.delete("/faults")
    def clear_faults() -> dict:
        faults.reset()
        return {"status": "ok"}

    @app.get("/faults/stats")
    def stats() -> dict:
        return faults.stats()

"""In-memory Jira REST; each POST creates a new effect, without deduplication."""

import hashlib
from collections.abc import Callable, Iterator
from copy import deepcopy
from datetime import UTC, datetime
from itertools import count
from threading import RLock
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, StrictStr

from govops.mocks.auth import MockUser, TokenHashStore, bearer_user
from govops.mocks.faults import FaultState, is_visible, visible_at
from govops.mocks.seed import TICKET_FIELDS

RESPONSE_FIELDS = (
    TICKET_FIELDS - {"access_level", "access_teams", "access_min_grade"}
) | {"parent_id", "metadata"}


class CommentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: StrictStr
    metadata: dict[StrictStr, StrictStr]


class ChildInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: StrictStr
    description: StrictStr | None = None
    priority: StrictStr | None = None
    metadata: dict[StrictStr, StrictStr]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _response(ticket: dict) -> dict:
    return {field: deepcopy(ticket[field]) for field in sorted(RESPONSE_FIELDS)}


def _readable(ticket: dict, user: MockUser) -> bool:
    level = ticket["access_level"]
    if level == "public":
        return True
    if user.team not in ticket["access_teams"]:
        return False
    return level == "team" or (
        level == "restricted" and user.grade >= ticket["access_min_grade"]
    )


class JiraState:
    def __init__(
        self,
        tickets: list[dict],
        *,
        clock: Callable[[], str | datetime] = _utc_now,
        counter: Iterator[int] | None = None,
    ):
        self._seed = deepcopy(tickets)
        self.clock = clock
        self.faults = FaultState()
        self._initial_counter = deepcopy(counter if counter is not None else count(1))
        self.lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.faults.reset()
            self.tickets = {
                t["ticket_id"]: {**deepcopy(t), "parent_id": None, "metadata": {}}
                for t in self._seed
            }
            self.comments: list[dict] = []
            self.child_ids: list[str] = []
            self.counter = deepcopy(self._initial_counter)

    def timestamp(self) -> str:
        value = self.clock()
        return value.isoformat() if isinstance(value, datetime) else value

    def visible_ticket(self, ticket_id: str, user: MockUser) -> dict:
        ticket = self.tickets.get(ticket_id)
        if ticket is None or not _readable(ticket, user):
            raise HTTPException(status_code=404)
        return ticket

    def report(self, *, bodies: bool = False) -> dict:
        with self.lock:
            comments = [
                {
                    **{
                        field: deepcopy(c[field])
                        for field in (
                            "comment_id",
                            "ticket_id",
                            "author",
                            "metadata",
                            "created_at",
                        )
                    },
                    "body_sha256": _hash(c["body"]),
                    **({"body": c["body"]} if bodies else {}),
                }
                for c in self.comments
            ]
            children = []
            for ticket_id in self.child_ids:
                ticket = self.tickets[ticket_id]
                children.append(
                    {
                        "ticket_id": ticket_id,
                        "parent_id": ticket["parent_id"],
                        "author": ticket["reporter_id"],
                        "metadata": deepcopy(ticket["metadata"]),
                        "created_at": ticket["created_at"],
                        "title_sha256": _hash(ticket["title"]),
                        "description_sha256": _hash(ticket["description"]),
                        **(
                            {
                                "title": ticket["title"],
                                "description": ticket["description"],
                            }
                            if bodies
                            else {}
                        ),
                    }
                )
            return {
                "ticket_count": len(self.tickets),
                "comments": comments,
                "children": children,
            }


def _hash(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _user(request: Request) -> MockUser:
    user = bearer_user(request)
    if user is None:
        raise HTTPException(status_code=401)
    return user


User = Annotated[MockUser, Depends(_user)]


def add_rest_routes(
    app: FastAPI, state: JiraState, token_store: TokenHashStore
) -> None:
    app.state.token_store = token_store

    @app.exception_handler(HTTPException)
    async def error(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = (
            ("not_found", "Ticket not found")
            if exc.status_code == 404
            else ("unauthorized", "Bearer authentication required")
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
        )

    @app.get("/tickets")
    def search(
        user: User,
        project: str | None = None,
        team: str | None = None,
        status: str | None = None,
        q: str | None = None,
        limit: Annotated[int, Query(ge=1)] = 20,
    ) -> dict:
        with state.lock:
            tickets = [
                t
                for _, t in sorted(state.tickets.items())
                if _readable(t, user)
                and (project is None or t["project_key"] == project)
                and (team is None or t["owner_team"] == team)
                and (status is None or t["status"] == status)
                and (q is None or q in t["title"])
            ]
            return {"tickets": [_response(t) for t in tickets[: min(limit, 50)]]}

    @app.get("/tickets/{ticket_id}")
    def get_ticket(ticket_id: str, user: User) -> dict:
        with state.lock:
            return _response(state.visible_ticket(ticket_id, user))

    @app.get("/tickets/{ticket_id}/comments")
    def get_comments(ticket_id: str, user: User) -> dict:
        with state.lock:
            state.visible_ticket(ticket_id, user)
            return {
                "comments": deepcopy(
                    [
                        c
                        for c in state.comments
                        if c["ticket_id"] == ticket_id
                        and is_visible(c, state.timestamp())
                    ]
                )
            }

    @app.post("/tickets/{ticket_id}/comments", status_code=201)
    def add_comment(ticket_id: str, body: CommentInput, user: User) -> dict:
        with state.lock:
            state.visible_ticket(ticket_id, user)
            comment = {
                "comment_id": f"comment-{next(state.counter)}",
                "ticket_id": ticket_id,
                "author": user.label,
                "body": body.body,
                "metadata": dict(body.metadata),
                "created_at": state.timestamp(),
            }
            if (deadline := visible_at(comment["created_at"])) is not None:
                comment["visible_at"] = deadline
            state.comments.append(comment)
            return {"comment": deepcopy(comment)}

    @app.get("/tickets/{ticket_id}/children")
    def get_children(ticket_id: str, user: User) -> dict:
        with state.lock:
            state.visible_ticket(ticket_id, user)
            return {
                "tickets": [
                    _response(t)
                    for _, t in sorted(state.tickets.items())
                    if t["parent_id"] == ticket_id
                    and _readable(t, user)
                    and is_visible(t, state.timestamp())
                ]
            }

    @app.post("/tickets/{ticket_id}/children", status_code=201)
    def add_child(ticket_id: str, body: ChildInput, user: User) -> dict:
        with state.lock:
            parent = state.visible_ticket(ticket_id, user)
            project = parent["project_key"]
            number = (
                max(
                    int(t["ticket_id"].rsplit("-", 1)[1])
                    for t in state.tickets.values()
                    if t["project_key"] == project
                )
                + 1
            )
            child_id = f"{project}-{number}"
            child = {
                **deepcopy(parent),
                "ticket_id": child_id,
                "title": body.title,
                "description": body.description,
                "priority": body.priority
                if body.priority is not None
                else parent["priority"],
                "ticket_type": "task",
                "status": "open",
                "reporter_id": user.label,
                "created_at": state.timestamp(),
                "resolved_at": None,
                "story_points": None,
                "parent_id": ticket_id,
                "metadata": dict(body.metadata),
            }
            if (deadline := visible_at(child["created_at"])) is not None:
                child["visible_at"] = deadline
            state.tickets[child_id] = child
            state.child_ids.append(child_id)
            return _response(child)


def add_admin_routes(app: FastAPI, state: JiraState) -> None:
    @app.get("/state")
    def report(bodies: bool = False) -> dict:
        return state.report(bodies=bodies)

    @app.post("/reset")
    def reset() -> dict:
        state.reset()
        return {"status": "ok"}

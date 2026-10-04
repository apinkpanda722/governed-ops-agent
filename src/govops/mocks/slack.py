"""Slack REST without deduplication; history is for tool-layer marker lookup."""

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

from govops.mocks.auth import TEAMS, MockUser, TokenHashStore, bearer_user

CHANNELS = tuple(
    {"channel_id": f"team-{team}", "kind": "team", "team": team}
    for team in sorted(TEAMS)
) + ({"channel_id": "announce", "kind": "announce", "team": None},)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr
    metadata: dict[StrictStr, StrictStr]


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class SlackState:
    def __init__(
        self,
        *,
        clock: Callable[[], str | datetime] = _utc_now,
        counter: Iterator[int] | None = None,
    ):
        self.clock = clock
        self._initial_counter = deepcopy(counter if counter is not None else count(1))
        self.lock = RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.messages: dict[str, list[dict]] = {
                channel["channel_id"]: [] for channel in CHANNELS
            }
            self.counter = deepcopy(self._initial_counter)

    def timestamp(self) -> str:
        value = self.clock()
        return value.isoformat() if isinstance(value, datetime) else value

    def authorized_channel(self, channel_id: str, user: MockUser) -> list[dict]:
        if channel_id not in self.messages:
            raise HTTPException(status_code=404)
        # Channels are public; both sending and marker history deny explicitly.
        if channel_id not in {f"team-{user.team}", "announce"}:
            raise HTTPException(status_code=403)
        return self.messages[channel_id]

    def report(self) -> dict:
        with self.lock:
            return {
                "channels": [
                    {
                        "channel_id": channel_id,
                        "message_count": len(messages),
                        "messages": [
                            {
                                **{k: deepcopy(v) for k, v in m.items() if k != "text"},
                                "text_sha256": hashlib.sha256(
                                    m["text"].encode("utf-8")
                                ).hexdigest(),
                            }
                            for m in messages
                        ],
                    }
                    for channel_id, messages in self.messages.items()
                ]
            }


def _user(request: Request) -> MockUser:
    user = bearer_user(request)
    if user is None:
        raise HTTPException(status_code=401)
    return user


User = Annotated[MockUser, Depends(_user)]


def add_rest_routes(
    app: FastAPI, state: SlackState, token_store: TokenHashStore
) -> None:
    app.state.token_store = token_store

    @app.exception_handler(HTTPException)
    async def error(request: Request, exc: HTTPException) -> JSONResponse:
        code, message = {
            401: ("unauthorized", "Bearer authentication required"),
            403: ("forbidden", "권한 없음"),
            404: ("not_found", "Channel not found"),
        }[exc.status_code]
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": message}},
        )

    @app.get("/channels")
    def channels(user: User) -> dict:
        return {"channels": deepcopy(list(CHANNELS))}

    @app.post("/channels/{channel_id}/messages", status_code=201)
    def send(channel_id: str, body: MessageInput, user: User) -> dict:
        with state.lock:
            messages = state.authorized_channel(channel_id, user)
            message = {
                "message_id": f"message-{next(state.counter)}",
                "channel_id": channel_id,
                "author": user.label,
                "text": body.text,
                "metadata": dict(body.metadata),
                "created_at": state.timestamp(),
            }
            messages.append(message)
            return {"message": deepcopy(message)}

    @app.get(
        "/channels/{channel_id}/messages",
        description="에이전트 도구가 아니라 도구 계층의 재시도 전 표식 조회용 이력 API.",
        response_description="최신순 메시지와 원래 metadata: 재시도 전 중복 표식 확인용.",
    )
    def history(
        channel_id: str,
        user: User,
        limit: Annotated[int, Query(ge=1)] = 50,
    ) -> dict:
        """Tool-layer marker lookup before retries; not an agent tool."""
        with state.lock:
            messages = state.authorized_channel(channel_id, user)
            return {"messages": deepcopy(list(reversed(messages))[: min(limit, 100)])}


def add_admin_routes(app: FastAPI, state: SlackState) -> None:
    @app.get("/state")
    def report() -> dict:
        return state.report()

    @app.post("/reset")
    def reset() -> dict:
        state.reset()
        return {"status": "ok"}

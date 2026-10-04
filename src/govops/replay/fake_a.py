"""Authenticated stateless MCP server replaying synthetic A responses."""

import hashlib
import hmac
import json
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from govops.replay.recordings import RecordingSet
from govops.tools.contract import load_snapshot

_AUTHORIZATION = re.compile(r"(?i:Bearer) (?P<token>[^\s]+)")


def _read_hashes(path: Path) -> dict[str, str] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"users"}
            or not isinstance(payload["users"], list)
            or not payload["users"]
        ):
            return None
        hashes = {}
        labels = set()
        for raw in payload["users"]:
            if not isinstance(raw, dict) or set(raw) != {
                "label",
                "team",
                "grade",
                "token",
            }:
                return None
            label, team, grade, token = (
                raw[key] for key in ("label", "team", "grade", "token")
            )
            if (
                not isinstance(team, str)
                or not team
                or type(grade) is not int
                or label != f"demo-{team}-g{grade}"
                or label in labels
                or not isinstance(token, str)
                or not token
                or re.search(r"\s", token)
            ):
                return None
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
            if digest in hashes:
                return None
            hashes[digest] = label
            labels.add(label)
        return hashes
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return None


class _BearerAuthMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        hashes: dict[str, str],
        current_user: ContextVar[str | None],
    ) -> None:
        self.app = app
        self.hashes = hashes
        self.current_user = current_user

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        values = [
            value.decode("latin-1")
            for name, value in scope.get("headers", ())
            if name.lower() == b"authorization"
        ]
        match = _AUTHORIZATION.fullmatch(values[0]) if len(values) == 1 else None
        user = None
        if match is not None:
            digest = hashlib.sha256(match.group("token").encode("utf-8")).hexdigest()
            for stored, label in self.hashes.items():
                if hmac.compare_digest(digest, stored):
                    user = label
        if user is None:
            await JSONResponse(
                {"error": {"code": "unauthorized", "message": "인증에 실패했습니다."}},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )(scope, receive, send)
            return
        context_token = self.current_user.set(user)
        try:
            await self.app(scope, receive, send)
        finally:
            self.current_user.reset(context_token)


def create_app(
    *, recordings: RecordingSet, tokens_file: Path, contract_root: Path | None = None
) -> Starlette:
    hashes = _read_hashes(tokens_file)
    if hashes is None:
        raise ValueError("replay token source unavailable or invalid format")
    snapshot = load_snapshot(contract_root)
    current_user: ContextVar[str | None] = ContextVar("replay_caller", default=None)

    async def list_tools(
        _ctx: ServerRequestContext[Any], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=tool.name,
                    description=tool.description,
                    inputSchema=tool.input_schema,
                    outputSchema=tool.output_schema,
                    _meta={"version": tool.version},
                )
                for tool in snapshot.values()
            ]
        )

    async def call_tool(
        _ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        caller = current_user.get()
        if caller is None:
            raise RuntimeError("authenticated replay request has no caller")
        entry = recordings.lookup(caller, params.name, params.arguments or {})
        payload = (
            entry.result
            if entry
            else {"error": {"code": "replay_miss", "message": "기록에 없는 호출입니다"}}
        )
        return types.CallToolResult(
            content=[
                types.TextContent(
                    text=json.dumps(payload, ensure_ascii=False, sort_keys=True)
                )
            ],
            structuredContent=payload,
            isError=entry.is_error if entry else True,
        )

    server: Server[dict[str, Any]] = Server(
        "govops-replay-a", version="1", on_list_tools=list_tools, on_call_tool=call_tool
    )
    app = server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        host="127.0.0.1",
    )
    app.add_middleware(_BearerAuthMiddleware, hashes=hashes, current_user=current_user)
    return app

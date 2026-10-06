"""Stateless A MCP connections with credentials attached per HTTP request."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from govops.broker import credentials
from govops.tools.contract import ContractTool, ensure_contract, load_snapshot
from govops.tools.rest import _retry_after


@dataclass(frozen=True)
class ToolResult:
    payload: dict
    is_error: bool


class AMcpError(Exception):
    """Safe transport or protocol failure without underlying request details."""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "protocol",
        status: int | None = None,
        retry_after: float | None = None,
    ):
        self.kind = kind
        self.status = status
        self.retry_after = retry_after
        super().__init__(message)


@dataclass
class _TransportFacts:
    """Call-local facts only; never retain requests, responses or exceptions."""

    kind: str | None = None
    status: int | None = None
    retry_after: float | None = None

    def observe_error(self, error: httpx2.TransportError) -> None:
        if isinstance(error, (httpx2.ConnectError, httpx2.ConnectTimeout)):
            self.kind = "connect"
        elif isinstance(error, (httpx2.ReadTimeout, httpx2.WriteTimeout)):
            self.kind = "timeout"
        else:
            self.kind = "transport"


class _ObservedStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream, facts: _TransportFacts):
        self.stream = stream
        self.facts = facts

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                yield chunk
        except httpx2.TransportError as error:
            self.facts.observe_error(error)
            raise

    async def aclose(self) -> None:
        try:
            await self.stream.aclose()
        except httpx2.TransportError as error:
            self.facts.observe_error(error)
            raise


class _ObservedTransport(httpx2.AsyncHTTPTransport):
    def __init__(self, facts: _TransportFacts):
        super().__init__(trust_env=False)
        self.facts = facts

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        try:
            response = await super().handle_async_request(request)
        except httpx2.TransportError as error:
            self.facts.observe_error(error)
            raise
        response.stream = _ObservedStream(response.stream, self.facts)
        return response


def _failure(
    error: Exception, facts: _TransportFacts | None = None
) -> tuple[str, int | None, float | None]:
    if facts is not None:
        if facts.status == 401:
            return ("http", 401, None)
        if facts.kind is not None:
            return (facts.kind, None, None)
        if facts.status is not None:
            return ("http", facts.status, facts.retry_after)
    if isinstance(error, BaseExceptionGroup):
        failures = [
            _failure(child)
            for child in error.exceptions
            if isinstance(child, Exception)
        ]
        for failure in failures:
            if failure[1] is not None and 400 <= failure[1] < 500:
                return failure
        for failure in failures:
            if failure[0] != "protocol":
                return failure
    if isinstance(error, httpx2.HTTPStatusError):
        return (
            "http",
            error.response.status_code,
            _retry_after(error.response.headers.get("Retry-After")),
        )
    if isinstance(error, (httpx2.ConnectError, httpx2.ConnectTimeout)):
        return ("connect", None, None)
    if isinstance(error, (TimeoutError, httpx2.TimeoutException)):
        return ("timeout", None, None)
    if isinstance(error, httpx2.TransportError):
        return ("transport", None, None)
    return ("protocol", None, None)


def _reason(error: Exception) -> str:
    if isinstance(error, BaseExceptionGroup):
        reasons = [
            _reason(child) for child in error.exceptions if isinstance(child, Exception)
        ]
        if "401 인증 실패" in reasons:
            return "401 인증 실패"
        if "연결 실패" in reasons:
            return "연결 실패"
    if isinstance(error, httpx2.HTTPStatusError) and error.response.status_code == 401:
        return "401 인증 실패"
    if isinstance(error, httpx2.TransportError):
        return "연결 실패"
    return "프로토콜 오류"


class AMcpClient:
    def __init__(self, url: str, *, timeout_s: float = 30.0):
        self.url = url
        self.timeout_s = timeout_s

    async def _request(
        self, user_id: str, name: str | None, arguments: Mapping[str, Any]
    ):
        # Fail before opening a connection, outside the SDK's exception wrappers.
        credentials.credential_for(user_id, "kodocs-mcp")

        async def authorize(request: httpx2.Request) -> None:
            request.headers["Authorization"] = "Bearer " + credentials.credential_for(
                user_id, "kodocs-mcp"
            )

        unauthorized = False
        facts = _TransportFacts()

        async def observe_status(response: httpx2.Response) -> None:
            nonlocal unauthorized
            unauthorized = unauthorized or response.status_code == 401
            if not 200 <= response.status_code < 300:
                facts.status = response.status_code
                facts.retry_after = _retry_after(response.headers.get("Retry-After"))

        reason = None
        failure = ("protocol", None, None)
        try:
            async with (
                httpx2.AsyncClient(
                    timeout=self.timeout_s,
                    trust_env=False,
                    transport=_ObservedTransport(facts),
                    event_hooks={"request": [authorize], "response": [observe_status]},
                ) as http,
                streamable_http_client(self.url, http_client=http) as streams,
                # Let httpx own transport timeouts. An equal SDK timer can cancel
                # the HTTP task before it raises ReadTimeout, losing the fact.
                # ToolPolicy still enforces the independent hard call deadline.
                ClientSession(*streams) as session,
            ):
                await session.initialize()
                if name is None:
                    tools = []
                    cursor = None
                    while True:
                        page = await session.list_tools(
                            params=types.PaginatedRequestParams(cursor=cursor)
                        )
                        tools.extend(page.tools)
                        cursor = page.next_cursor
                        if cursor is None:
                            return tools
                return await session.call_tool(name, dict(arguments))
        except credentials.CredentialError:
            raise
        except Exception as error:  # noqa: BLE001 — sanitize all SDK exception details
            reason = "401 인증 실패" if unauthorized else _reason(error)
            failure = ("http", 401, None) if unauthorized else _failure(error, facts)
        # Raising outside except discards secret-bearing exception context.
        raise AMcpError(
            reason, kind=failure[0], status=failure[1], retry_after=failure[2]
        )

    async def list_tools(self, user_id: str) -> list[dict]:
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
                "version": (tool.meta or {}).get("version"),
            }
            for tool in await self._request(user_id, None, {})
        ]

    async def call_tool(
        self, user_id: str, name: str, arguments: Mapping[str, Any]
    ) -> ToolResult:
        result = await self._request(user_id, name, arguments)
        if not isinstance(result.structured_content, dict):
            raise AMcpError("프로토콜 오류")
        return ToolResult(result.structured_content, result.is_error)


async def check_contract(
    client: AMcpClient,
    snapshot: Mapping[str, ContractTool] | None = None,
    *,
    user_id: str | None = None,
) -> None:
    if user_id is None:
        users = credentials.known_users("kodocs-mcp")
        if not users:
            raise credentials.CredentialSourceError("kodocs-mcp: no known users")
        user_id = users[0]
    listed = await client.list_tools(user_id)
    ensure_contract(
        load_snapshot() if snapshot is None else snapshot,
        [
            {
                **tool,
                "inputSchema": tool["input_schema"],
                "outputSchema": tool["output_schema"],
                "_meta": {"version": tool["version"]},
            }
            for tool in listed
        ],
    )

"""Stateless A MCP connections with credentials attached per HTTP request."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client

from govops.broker import credentials
from govops.tools.contract import ContractTool, ensure_contract, load_snapshot


@dataclass(frozen=True)
class ToolResult:
    payload: dict
    is_error: bool


class AMcpError(Exception):
    """Safe transport or protocol failure without underlying request details."""


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

        async def observe_status(response: httpx2.Response) -> None:
            nonlocal unauthorized
            unauthorized = unauthorized or response.status_code == 401

        reason = None
        try:
            async with (
                httpx2.AsyncClient(
                    timeout=self.timeout_s,
                    trust_env=False,
                    event_hooks={"request": [authorize], "response": [observe_status]},
                ) as http,
                streamable_http_client(self.url, http_client=http) as streams,
                ClientSession(*streams, read_timeout_seconds=self.timeout_s) as session,
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
        # Raising outside except discards secret-bearing exception context.
        raise AMcpError(reason)

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

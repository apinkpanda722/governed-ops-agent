"""Real localhost transport and request-only credential checks."""

import asyncio
import json
import logging
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from govops.broker import credentials
from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.tools.a_mcp import AMcpClient, AMcpError, check_contract
from govops.tools.contract import ContractMismatchError, load_snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "a_recordings"
USER = "demo-platform-g3"
ARGS = {"kind": "incident", "value": "INC-2026-012"}


class Capture:
    """Test-owned ASGI wrapper, including lifespan passthrough."""

    def __init__(self, app):
        self.app = app
        self.requests = []
        self.responses = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = {"headers": scope["headers"], "body": b""}
        self.requests.append(request)

        async def receiving():
            message = await receive()
            request["body"] += message.get("body", b"")
            return message

        async def sending(message):
            self.responses.append(message.copy())
            await send(message)

        await self.app(scope, receiving, sending)


def test_transport_and_secrets(serve_asgi, a_tokens, monkeypatch, caplog):
    path, tokens = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    caplog.set_level(logging.DEBUG)
    for name in ("httpx", "httpcore", "httpx2", "httpcore2", "mcp"):
        caplog.set_level(logging.DEBUG, logger=name)
    capture = Capture(
        create_app(recordings=RecordingSet.load(FIXTURES), tokens_file=path)
    )
    client = AMcpClient(serve_asgi(capture) + "/mcp")

    async def exercise():
        listed = await client.list_tools(USER)
        assert {item["name"]: item for item in listed} == {
            name: asdict(tool) for name, tool in load_snapshot().items()
        }
        await check_contract(client)
        changed = load_snapshot()
        changed["lookup_id"] = replace(changed["lookup_id"], version=2)
        with pytest.raises(ContractMismatchError):
            await check_contract(client, changed, user_id=USER)
        results = []
        for user, token in tokens.items():
            start = len(capture.requests)
            result = await client.call_tool(user, "lookup_id", ARGS)
            results.append(result)
            for request in capture.requests[start:]:
                assert [
                    v for k, v in request["headers"] if k.lower() == b"authorization"
                ] == [f"Bearer {token}".encode()]
                body = json.loads(request["body"])
                if body["method"] == "tools/call":
                    assert body["params"]["arguments"] == ARGS
        assert results[0].payload["documents"][0]["doc_id"] == "syn-incident-022"
        assert results[1].payload["documents"] == []
        miss = await client.call_tool(
            USER, "lookup_id", {"kind": "incident", "value": "missing"}
        )
        assert miss.is_error and miss.payload["error"]["code"] == "replay_miss"
        start = len(capture.requests)
        with pytest.raises(credentials.UnknownUserError):
            await client.call_tool("unknown", "lookup_id", ARGS)
        assert len(capture.requests) == start
        payload = json.loads(path.read_text())
        payload["users"][0]["token"] = "SENTINEL_WRONG_TOKEN"
        path.write_text(json.dumps(payload))
        with pytest.raises(AMcpError) as error:
            await client.call_tool(USER, "lookup_id", ARGS)
        assert "401" in str(error.value)
        assert error.value.__context__ is None
        for request in capture.requests:
            authorization = [
                value
                for key, value in request["headers"]
                if key.lower() == b"authorization"
            ]
            assert len(authorization) == 1
            assert authorization[0] in {
                f"Bearer {token}".encode()
                for token in (*tokens.values(), "SENTINEL_WRONG_TOKEN")
            }
        assert authorization == [b"Bearer SENTINEL_WRONG_TOKEN"]
        assert capture.responses[-2]["status"] == 401
        public = repr(
            (listed, results, miss, vars(client), client, error.value, error.value.args)
        )
        for token in (*tokens.values(), "SENTINEL_WRONG_TOKEN"):
            assert token not in public + repr(capture.responses) + caplog.text

    asyncio.run(exercise())


def test_empty_users_and_connection_failure(a_tokens, monkeypatch):
    path, _ = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    client = AMcpClient("http://127.0.0.1:1/mcp", timeout_s=0.1)
    with pytest.raises(AMcpError, match="연결 실패"):
        asyncio.run(client.list_tools(USER))
    path.write_text('{"users": []}')
    with pytest.raises(credentials.CredentialSourceError):
        asyncio.run(check_contract(client))

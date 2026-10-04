import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from govops.api.startup import StartupError
from govops.api.wiring import open_task_service
from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.tools.contract import load_snapshot


@pytest.mark.parametrize("missing", ["KODOCS_MCP_URL", "LITELLM_AGENT_KEY"])
def test_missing_configuration(monkeypatch, missing):
    monkeypatch.setenv("KODOCS_MCP_URL", "PRIVATE-VALUE")
    monkeypatch.setenv("LITELLM_AGENT_KEY", "PRIVATE-KEY")
    monkeypatch.delenv(missing)

    async def run():
        async with open_task_service():
            pytest.fail("must not start")

    with pytest.raises(StartupError) as error:
        asyncio.run(run())
    assert str(error.value) == missing
    assert error.value.__context__ is None


def test_wiring_contract_and_memory(serve_asgi, a_tokens, monkeypatch):
    path, _ = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    monkeypatch.setenv("LITELLM_AGENT_KEY", "fake-key")
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "")
    recordings = RecordingSet.load(Path(__file__).parent / "fixtures/a_recordings")
    monkeypatch.setenv(
        "KODOCS_MCP_URL",
        serve_asgi(create_app(recordings=recordings, tokens_file=path)) + "/mcp",
    )

    async def run():
        async with open_task_service() as service:
            assert isinstance(service.graph.checkpointer, InMemorySaver)

    asyncio.run(run())
    snapshot = load_snapshot()
    snapshot["lookup_id"] = replace(snapshot["lookup_id"], version=2)
    monkeypatch.setattr("govops.replay.fake_a.load_snapshot", lambda _: snapshot)
    monkeypatch.setenv(
        "KODOCS_MCP_URL",
        serve_asgi(create_app(recordings=recordings, tokens_file=path)) + "/mcp",
    )
    with pytest.raises(StartupError):
        asyncio.run(run())


def test_server_lifespan_assembles_and_closes(monkeypatch):
    from contextlib import asynccontextmanager

    from fastapi.testclient import TestClient

    from govops.api.__main__ import build_server_config

    events = []

    class Service:
        async def start(self, user_id, request):
            events.append((user_id, request))
            return "a" * 32

    @asynccontextmanager
    async def assembly():
        events.append("open")
        try:
            yield Service()
        finally:
            events.append("close")

    monkeypatch.setattr("govops.api.__main__.open_task_service", assembly)
    monkeypatch.setenv("DEMO_LOGIN", "true")
    config = build_server_config(8000)
    assert config.host == "127.0.0.1"
    with TestClient(config.app, client=("127.0.0.1", 50000)) as client:
        token = client.post("/sessions", json={"user": "demo-platform-g3"}).json()[
            "session_token"
        ]
        response = client.post(
            "/tasks",
            json={"request": "조회"},
            headers={"Authorization": "Bearer " + token},
        )
        assert response.status_code == 202
    assert events == ["open", ("demo-platform-g3", "조회"), "close"]

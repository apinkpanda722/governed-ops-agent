import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from govops.api.startup import StartupError
from govops.api.wiring import open_task_service
from govops.gateway.team_keys import load_teams, team_env_name
from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.tools.contract import load_snapshot


@pytest.mark.parametrize("missing", ["KODOCS_MCP_URL", "LITELLM_TEAM_KEY_PLATFORM"])
def test_missing_configuration(monkeypatch, missing):
    monkeypatch.setenv("KODOCS_MCP_URL", "PRIVATE-VALUE")
    monkeypatch.setenv("LITELLM_AGENT_KEY", "PRIVATE-KEY")
    for team in load_teams():
        monkeypatch.setenv(team_env_name(team), "synthetic-team-wiring-key")
    monkeypatch.delenv(missing)

    async def run():
        async with open_task_service():
            pytest.fail("must not start")

    with pytest.raises(StartupError) as error:
        asyncio.run(run())
    assert str(error.value) == (
        "team_keys_missing" if missing.startswith("LITELLM_TEAM_KEY") else missing
    )
    assert error.value.__context__ is None


def test_wiring_contract_and_memory(serve_asgi, a_tokens, monkeypatch):
    path, _ = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    monkeypatch.setenv("LITELLM_AGENT_KEY", "fake-key")
    for team in load_teams():
        monkeypatch.setenv(team_env_name(team), "synthetic-team-wiring-key")
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


@pytest.mark.parametrize("fail", [False, True])
def test_wiring_closes_shared_rest_client(serve_asgi, a_tokens, monkeypatch, fail):
    path, _ = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    monkeypatch.setenv("LITELLM_AGENT_KEY", "fake-key")
    for team in load_teams():
        monkeypatch.setenv(team_env_name(team), "synthetic-team-wiring-key")
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "")
    recordings = RecordingSet.load(Path(__file__).parent / "fixtures/a_recordings")
    monkeypatch.setenv(
        "KODOCS_MCP_URL",
        serve_asgi(create_app(recordings=recordings, tokens_file=path)) + "/mcp",
    )
    created = []

    class Tracked(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr("govops.api.wiring.httpx.AsyncClient", Tracked)

    async def run():
        async with open_task_service():
            assert len(created) == 1 and not created[0].is_closed
            if fail:
                raise RuntimeError("body failure")

    if fail:
        with pytest.raises(RuntimeError):
            asyncio.run(run())
    else:
        asyncio.run(run())
    # Jira·Slack이 공유하는 HTTP 클라이언트는 서비스 종료 시 닫힌다.
    assert len(created) == 1 and created[0].is_closed


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


@pytest.mark.parametrize("profile", ["read_only", "writes", "writes_org", "invalid"])
def test_wiring_approval_dependencies(
    serve_asgi, a_tokens, monkeypatch, tmp_path, profile
):
    from govops.tools.outbox import InMemoryOutboxStore

    path, _ = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    monkeypatch.setenv("LITELLM_AGENT_KEY", "synthetic-key")
    for team in load_teams():
        monkeypatch.setenv(team_env_name(team), "synthetic-team-wiring-key")
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "")
    monkeypatch.setenv("GOVOPS_AGENT_PROFILE", profile)
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path))
    recordings = RecordingSet.load(Path(__file__).parent / "fixtures/a_recordings")
    monkeypatch.setenv(
        "KODOCS_MCP_URL",
        serve_asgi(create_app(recordings=recordings, tokens_file=path)) + "/mcp",
    )

    async def run():
        async with open_task_service() as service:
            assert isinstance(service.effects.outbox, InMemoryOutboxStore)
            assert service.audit.directory == tmp_path
            records = [
                json.loads(line)
                for path in tmp_path.glob("chain-*.jsonl")
                for line in path.read_text().splitlines()
            ]
            assert [row["event"] for row in records] == [
                "chain.genesis",
                "service.start",
            ]
            assert records[1]["actor"]["sub"] == "system"
            assert len(service.users) == 38
            assert service.graph.agent_profile.name == profile
            assert service.graph.agent_profile.allow_writes
            assert service.policy is not None

    if profile != "writes_org":
        with pytest.raises(StartupError, match="GOVOPS_AGENT_PROFILE"):
            asyncio.run(run())
    else:
        asyncio.run(run())


@pytest.mark.parametrize("mode", ["mock" + "_param", "down_alias", "invalid"])
def test_service_rejects_fault_mode(monkeypatch, mode):
    monkeypatch.setenv("GOVOPS_MODEL_FAULT_MODE", mode)

    async def run():
        async with open_task_service():
            pytest.fail("must not start")

    with pytest.raises(StartupError, match="GOVOPS_MODEL_FAULT_MODE"):
        asyncio.run(run())


@pytest.mark.parametrize("value", [None, "", "   "])
def test_agent_key_cannot_replace_missing_team_key(monkeypatch, value):
    monkeypatch.setenv("KODOCS_MCP_URL", "http://synthetic.invalid")
    monkeypatch.setenv("LITELLM_AGENT_KEY", "synthetic-diagnostic-key")
    for team in load_teams():
        monkeypatch.setenv(team_env_name(team), "synthetic-team-wiring-key")
    if value is None:
        monkeypatch.delenv(team_env_name("platform"))
    else:
        monkeypatch.setenv(team_env_name("platform"), value)

    async def run():
        async with open_task_service():
            pytest.fail("must not start")

    with pytest.raises(StartupError, match="^team_keys_missing$"):
        asyncio.run(run())

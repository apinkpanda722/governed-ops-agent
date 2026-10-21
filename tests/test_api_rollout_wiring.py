"""Offline assembly parity, rollout selection, and shared audit serialization."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

import govops.api.__main__ as entry
from govops.agent.graph import READ_ONLY
from govops.api import rollout_wiring
from govops.api.routed import (
    RecordingClassifier,
    RoutedTaskService,
    VariantFilteredTurnIndex,
)
from govops.api.startup import StartupError
from govops.api.tasks import TaskService
from govops.api.variant import InMemoryVariantStore
from govops.api.wiring import build_task_service, open_task_service
from govops.audit.log import AuditLog
from govops.audit.verify import verify_chain
from govops.gateway.team_keys import load_teams, team_env_name
from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy


@pytest.fixture(autouse=True)
def rollout_environment(monkeypatch, tmp_path):
    for name in (
        "GOVOPS_V2_ROLLOUT",
        "GOVOPS_GUARDRAIL_V2",
        "GOVOPS_MODEL_FAULT_MODE",
        "GOVOPS_AGENT_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("GOVOPS_ROLLOUT_DIR", str(tmp_path / "rollout"))
    for team in load_teams():
        monkeypatch.setenv(team_env_name(team), "synthetic-team-wiring-key")


@pytest.fixture
def fake_a(serve_asgi, a_tokens, monkeypatch):
    path, _ = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    recordings = RecordingSet.load(Path(__file__).parent / "fixtures/a_recordings")
    app = create_app(recordings=recordings, tokens_file=path)
    requests = []

    async def track(scope, receive, send):
        if scope["type"] == "http":
            requests.append(scope["method"])
        await app(scope, receive, send)

    monkeypatch.setenv("KODOCS_MCP_URL", serve_asgi(track) + "/mcp")
    return requests


def records(directory):
    return [
        json.loads(line)
        for path in sorted(directory.glob("chain-*.jsonl"))
        for line in path.read_text().splitlines()
    ]


@pytest.mark.parametrize(
    "opener", [open_task_service, rollout_wiring.open_rollout_service]
)
@pytest.mark.parametrize(
    "setting,code",
    [
        ("KODOCS_MCP_URL", "KODOCS_MCP_URL"),
        ("LITELLM_TEAM_KEY_PLATFORM", "team_keys_missing"),
        ("mock_param", "GOVOPS_MODEL_FAULT_MODE is harness-only"),
        ("down_alias", "GOVOPS_MODEL_FAULT_MODE is harness-only"),
        ("invalid", "GOVOPS_MODEL_FAULT_MODE is harness-only"),
    ],
)
def test_preflight_matches_existing_opener(monkeypatch, opener, setting, code):
    monkeypatch.setenv("KODOCS_MCP_URL", "http://synthetic.invalid")
    if setting in {"KODOCS_MCP_URL", "LITELLM_TEAM_KEY_PLATFORM"}:
        monkeypatch.delenv(setting)
    else:
        monkeypatch.setenv("GOVOPS_MODEL_FAULT_MODE", setting)

    async def run():
        async with opener():
            pytest.fail("must not start")

    with pytest.raises(StartupError) as error:
        asyncio.run(run())
    assert str(error.value) == code
    assert error.value.__context__ is None


def test_conflict_precedes_audit_and_a_requests(fake_a, monkeypatch, tmp_path):
    monkeypatch.setenv("GOVOPS_V2_ROLLOUT", "on")
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", "on")
    monkeypatch.setenv("GOVOPS_MODEL_FAULT_MODE", "invalid")

    async def run():
        async with rollout_wiring.open_rollout_service():
            pytest.fail("must not start")

    with pytest.raises(StartupError, match="^rollout_conflict$"):
        asyncio.run(run())
    assert not list(tmp_path.rglob("chain-*.jsonl"))
    assert fake_a == []


@pytest.mark.parametrize(
    "value,expected",
    [(None, False), ("", False), ("   ", False), ("off", False), ("on", True)],
)
def test_rollout_enabled_defaults(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("GOVOPS_V2_ROLLOUT", value)
    assert rollout_wiring.rollout_enabled() is expected


@pytest.mark.parametrize("value", ["true", "ON", "invalid"])
def test_rollout_enabled_rejects_other_values(monkeypatch, value):
    monkeypatch.setenv("GOVOPS_V2_ROLLOUT", value)
    with pytest.raises(StartupError, match="^GOVOPS_V2_ROLLOUT$"):
        rollout_wiring.rollout_enabled()


def test_assembly_shares_resources_and_runs_maintenance_once(
    fake_a, monkeypatch, tmp_path
):
    cleanup, prune, closed = [], [], []
    original_cleanup = TaskService.startup_cleanup
    original_prune = rollout_wiring.startup_prune
    original_close = TaskService.aclose

    async def cleanup_once(self):
        cleanup.append(self)
        await original_cleanup(self)

    async def prune_once(service, saver, turns, *, variants):
        prune.append((service, saver, turns, variants))
        await original_prune(service, saver, turns, variants=variants)

    async def close(self):
        closed.append(self)
        await original_close(self)

    monkeypatch.setattr(TaskService, "startup_cleanup", cleanup_once)
    monkeypatch.setattr(TaskService, "aclose", close)
    monkeypatch.setattr(rollout_wiring, "startup_prune", prune_once)

    async def run():
        async with rollout_wiring.open_rollout_service() as router:
            assert isinstance(router, RoutedTaskService)
            first, second = router.v1, router.v2
            saver = first.graph.checkpointer
            assert isinstance(saver, InMemorySaver)
            assert second.graph.checkpointer is saver
            assert first.graph.agent_semantic is None
            assert isinstance(second.graph.agent_semantic, RecordingClassifier)
            assert second.graph.agent_semantic.directory == tmp_path / "rollout"
            assert first.effects.outbox is second.effects.outbox
            assert first.effects.jira is second.effects.jira
            assert first.effects.slack is second.effects.slack
            assert first.policy is second.policy
            assert isinstance(router.variants, InMemoryVariantStore)
            for service, variant in ((first, "v1"), (second, "v2")):
                assert isinstance(service.turns, VariantFilteredTurnIndex)
                assert service.turns.inner is router.turns
                assert service.turns.variants is router.variants
                assert service.turns.variant == variant
            assert first.audit is not second.audit
            assert first.audit.directory == second.audit.directory == tmp_path / "audit"
            assert router.audit is first.audit
            assert cleanup == [first]
            assert prune == [(router, saver, router.turns, router.variants)]
        assert closed == [first, second]

    asyncio.run(run())
    assert fake_a
    rows = records(tmp_path / "audit")
    assert sum(row["event"] == "service.start" for row in rows) == 2
    assert verify_chain(tmp_path / "audit").ok


@pytest.mark.parametrize("fail", [False, True])
def test_rollout_closes_shared_rest_client(fake_a, monkeypatch, fail):
    created = []

    class Tracked(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            assert kwargs["trust_env"] is False
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(rollout_wiring.httpx, "AsyncClient", Tracked)

    async def run():
        async with rollout_wiring.open_rollout_service():
            assert len(created) == 1 and not created[0].is_closed
            if fail:
                raise RuntimeError("synthetic body failure")

    if fail:
        with pytest.raises(RuntimeError, match="synthetic body failure"):
            asyncio.run(run())
    else:
        asyncio.run(run())
    assert len(created) == 1 and created[0].is_closed


@pytest.mark.parametrize("chat", [object(), SimpleNamespace(classify=None)])
def test_classifier_required_before_service_assembly(
    fake_a, monkeypatch, tmp_path, chat
):
    monkeypatch.setattr(rollout_wiring, "LiteLLMChatClient", lambda **_: chat)

    async def run():
        async with rollout_wiring.open_rollout_service():
            pytest.fail("must not start")

    with pytest.raises(StartupError, match="^guardrail_v2_unavailable$"):
        asyncio.run(run())
    assert not list(tmp_path.rglob("chain-*.jsonl"))


def test_unset_rollout_uses_existing_lifespan_opener(monkeypatch):
    calls = []
    service = SimpleNamespace(audit=object())

    @asynccontextmanager
    async def existing():
        calls.append("open")
        yield service

    def forbidden():
        pytest.fail("rollout must remain disabled")

    monkeypatch.setattr(entry, "open_task_service", existing)
    monkeypatch.setattr(entry, "open_rollout_service", forbidden)
    app = entry.build_server_config(0).app

    async def run():
        async with app.router.lifespan_context(app):
            assert app.state.task_service is service
            assert app.state.audit is service.audit

    asyncio.run(run())
    assert calls == ["open"]


def test_unset_rollout_real_lifespan_starts_one_service(fake_a, tmp_path):
    app = entry.build_server_config(0).app

    async def run():
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.task_service, TaskService)
            assert app.state.task_service.graph.agent_semantic is None

    asyncio.run(run())
    rows = records(tmp_path / "audit")
    assert sum(row["event"] == "service.start" for row in rows) == 1
    assert verify_chain(tmp_path / "audit").ok


@pytest.mark.parametrize("concurrent", [False, True])
def test_two_audit_instances_keep_contiguous_unique_sequences(tmp_path, concurrent):
    def build():
        return build_task_service(
            chat=object(),
            tools=SimpleNamespace(jira=object(), slack=object(), policy=ToolPolicy()),
            saver=InMemorySaver(),
            outbox=InMemoryOutboxStore(),
            profile=READ_ONLY,
            audit_dir=tmp_path,
            semantic=None,
        )

    first, second = build().audit, build().audit
    assert isinstance(first, AuditLog) and isinstance(second, AuditLog)
    assert first is not second

    def append(audit, ident):
        return audit.append(
            "conversation.deleted",
            task_id=f"{ident:032x}",
            actor_sub="demo-platform-g3",
        )

    if concurrent:
        barrier = threading.Barrier(2)

        def write(audit, start):
            barrier.wait(timeout=5)
            return [append(audit, start + index) for index in range(20)]

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, first, 0), pool.submit(write, second, 20)]
            seqs = [seq for future in futures for seq in future.result(timeout=10)]
        expected = 40
    else:
        seqs = [
            append(audit, index) for index in range(10) for audit in (first, second)
        ]
        expected = 20
    assert len(seqs) == expected
    assert set(seqs) == set(range(4, expected + 4))
    rows = records(tmp_path)
    assert len(rows) == expected + 3
    assert {row["seq"] for row in rows} == set(range(1, expected + 4))
    assert verify_chain(tmp_path).ok

"""Offline argument-key preflight and complete service auditor assembly."""

import asyncio
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, call, reply

from govops.agent.graph import READ_ONLY, run_task
from govops.api import rollout_wiring, wiring
from govops.api.startup import StartupError
from govops.audit.crypto import load_signing_key
from govops.audit.toolcall import ToolCallAuditor
from govops.audit.verify import verify_chain
from govops.gateway.team_keys import load_teams, team_env_name
from govops.obs.mask import mask_attributes
from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import ToolOutcome
from govops.tools.resilience import ToolPolicy


def build(directory, *, chat=None, tools=None):
    return wiring.build_task_service(
        chat=chat if chat is not None else object(),
        tools=tools
        if tools is not None
        else SimpleNamespace(jira=object(), slack=object(), policy=ToolPolicy()),
        saver=InMemorySaver(),
        outbox=InMemoryOutboxStore(),
        profile=READ_ONLY,
        audit_dir=directory,
        semantic=None,
    )


def records(directory):
    return [
        json.loads(line)
        for path in sorted(directory.glob("chain-*.jsonl"))
        for line in path.read_text().splitlines()
    ]


@pytest.mark.parametrize(
    "failure,code",
    [
        ("missing", "audit_args_hmac_key_missing"),
        ("empty", "audit_args_hmac_key_missing"),
        ("whitespace", "audit_args_hmac_key_missing"),
        ("invalid", "audit_args_hmac_key_invalid"),
        ("reused", "audit_args_hmac_key_reused"),
    ],
)
def test_key_refused_before_start(tmp_path, monkeypatch, failure, code):
    value = secrets.token_hex(32)
    if failure == "missing":
        monkeypatch.delenv("AUDIT_ARGS_HMAC_KEY", raising=False)
    else:
        if failure == "empty":
            value = ""
        elif failure == "whitespace":
            value = " \t "
        elif failure == "invalid":
            value += "x"
        elif failure == "reused":
            value = (
                load_signing_key()
                .private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
                .hex()
            )
        monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", value)
    with pytest.raises(StartupError) as error:
        build(tmp_path)
    assert str(error.value) == code
    assert error.value.__context__ is None
    assert error.value.__cause__ is None
    assert not list(tmp_path.glob("chain-*.jsonl"))
    assert not value or value not in str(error.value), "key exposed in startup error"


def test_build_records_two_reads_with_independent_hmac(tmp_path, monkeypatch):
    key = secrets.token_hex(32)
    monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", key)
    arguments = '{"ticket_id":"SYN-1"}'
    chat = FakeChat(
        reply(
            calls=[call(arguments=arguments), call(arguments=arguments, id="read-2")]
        ),
        reply("합성 답변"),
    )
    tools = FakeTools(*[ToolOutcome({"text": "합성 결과"}, False, ())] * 2)
    tools.jira, tools.slack, tools.policy = object(), object(), ToolPolicy()

    async def run():
        service = build(tmp_path, chat=chat, tools=tools)
        try:
            state = await run_task(
                service.graph,
                task_id="a" * 32,
                user_id="demo-platform-g3",
                request="합성 티켓 조회",
            )
            assert state["status"] == "completed"
        finally:
            await service.aclose()

    asyncio.run(run())
    assert len(tools.calls) == 2
    rows = records(tmp_path)
    calls = [row for row in rows if row["event"] == "tool.call"]
    assert len(calls) == 2
    key_bytes = bytes.fromhex(key)
    kid = hashlib.sha256(b"govops-args-hmac\0" + key_bytes).hexdigest()[:12]
    canonical = json.dumps(
        {"tool": "jira_get_ticket", "args": {"ticket_id": "SYN-1"}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode()
    digest = hmac.new(key_bytes, canonical, hashlib.sha256).hexdigest()
    assert [row["args_hmac_kid"] for row in calls] == [kid, kid]
    assert [row["args_hmac"] for row in calls] == [digest, digest]
    assert [row["status"] for row in calls] == ["ok", "ok"]
    assert [row["tool_call_sha256"] for row in calls] == [
        hashlib.sha256(value.encode()).hexdigest() for value in ("call-1", "read-2")
    ]
    assert key not in json.dumps(rows), "key exposed in audit"
    assert verify_chain(tmp_path).ok


@pytest.fixture
def fake_a(serve_asgi, a_tokens, monkeypatch, tmp_path):
    for name in (
        "GOVOPS_GUARDRAIL_V2",
        "GOVOPS_MODEL_FAULT_MODE",
        "GOVOPS_AGENT_PROFILE",
        "GOVOPS_V2_ROLLOUT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "")
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("GOVOPS_ROLLOUT_DIR", str(tmp_path / "rollout"))
    for team in load_teams():
        monkeypatch.setenv(team_env_name(team), "synthetic-team-wiring-key")
    path, _ = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    recordings = RecordingSet.load(Path(__file__).parent / "fixtures/a_recordings")
    app = create_app(recordings=recordings, tokens_file=path)
    monkeypatch.setenv("KODOCS_MCP_URL", serve_asgi(app) + "/mcp")
    for module in (wiring, rollout_wiring):
        monkeypatch.setattr(module, "langfuse_sink_from_env", lambda: None)
        monkeypatch.setattr(module, "configure", lambda sinks: None)
        monkeypatch.setattr(
            module,
            "LiteLLMChatClient",
            lambda **kwargs: SimpleNamespace(classify=lambda **kwargs: None),
        )


@pytest.mark.parametrize("module", [wiring, rollout_wiring], ids=["task", "rollout"])
def test_openers_refuse_missing_key(fake_a, monkeypatch, tmp_path, module):
    monkeypatch.delenv("AUDIT_ARGS_HMAC_KEY", raising=False)
    opener = (
        wiring.open_task_service
        if module is wiring
        else rollout_wiring.open_rollout_service
    )

    async def run():
        async with opener():
            pytest.fail("must not start without argument HMAC key")

    with pytest.raises(StartupError) as error:
        asyncio.run(run())
    assert str(error.value) == "audit_args_hmac_key_missing"
    assert error.value.__context__ is None
    assert not list((tmp_path / "audit").glob("chain-*.jsonl"))


@pytest.mark.parametrize("module", [wiring, rollout_wiring], ids=["task", "rollout"])
def test_openers_inject_auditors_and_mask_key(fake_a, monkeypatch, tmp_path, module):
    key = secrets.token_hex(32)
    monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", key)
    # A raw hex value has no token-like prefix; only registration should hide it.
    assert mask_attributes({"content_hash": key}) == {"content_hash": key}, (
        "fresh synthetic key already registered"
    )
    captured = []
    original = wiring.build_graph

    def graph(*args, **kwargs):
        assert isinstance(kwargs.get("tool_audit"), ToolCallAuditor)
        captured.append(kwargs["tool_audit"])
        return original(*args, **kwargs)

    monkeypatch.setattr(wiring, "build_graph", graph)
    opener = (
        wiring.open_task_service
        if module is wiring
        else rollout_wiring.open_rollout_service
    )

    async def run():
        async with opener() as assembled:
            services = [assembled] if module is wiring else [assembled.v1, assembled.v2]
            assert len(captured) == len(services)
            for service, auditor in zip(services, captured, strict=True):
                assert auditor._audit is service.audit
                assert (
                    auditor._mac.kid
                    == hashlib.sha256(
                        b"govops-args-hmac\0" + bytes.fromhex(key)
                    ).hexdigest()[:12]
                )
            masked = mask_attributes(
                {"content_hash": key, "input.request": "합성 " + key + " 끝"}
            )
            assert masked == {
                "content_hash": "[REDACTED]",
                "input.request": "합성 [REDACTED] 끝",
            }, "registered key not masked"

    asyncio.run(run())
    assert len(captured) == (1 if module is wiring else 2)
    assert verify_chain(tmp_path / "audit").ok

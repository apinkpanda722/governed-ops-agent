"""Event schemas shared by persistent and offline audit sinks."""

import json
import random
import re
from pathlib import Path
from uuid import uuid4

import pytest

from govops.audit.crypto import format_usd
from govops.audit.log import AuditLog, AuditWriteError, MemoryAuditLog
from govops.gateway.hook_header import _POST, _PRE

CASES = {
    "service.start": {
        "policy_sha256": "a" * 64,
        "rules_sha256": "b" * 64,
        "access_index_sha256": "c" * 64,
        "hook_rules_digest": "a" * 12,
        "hook_code_digest": "b" * 12,
    },
    "chain.genesis": {
        "legacy_files": 0,
        "legacy_lines": 0,
        "legacy_last_seq": 0,
        "legacy_sha256": "a" * 64,
    },
    "chain.anchor": {"label": "test-1"},
    "model.call": {
        "team": "none",
        "model": "unknown",
        "deployment_id": "unknown",
        "fallback": False,
        "cost_usd": format_usd(0),
    },
    "auth.failed": {"reason": "invalid_token"},
}
BUNDLE = {
    "guardrail_pre": "allow",
    "guardrail_post": "none",
    "guardrail_q": 0,
    "guardrail_m": 10000,
}


@pytest.fixture(params=[AuditLog, MemoryAuditLog])
def sink(request, tmp_path):
    return request.param(tmp_path) if request.param is AuditLog else request.param()


def emit(sink, event, fields, **identity):
    defaults = {"task_id": uuid4().hex, "actor_sub": "demo-platform-g3"}
    if event in CASES and event != "model.call":
        defaults = {"task_id": None, "actor_sub": "system"}
    return sink.append(event, **(defaults | identity), **fields)


@pytest.mark.parametrize("event", CASES)
def test_required_and_undefined_fields(sink, event):
    fields = CASES[event]
    assert emit(sink, event, fields) == (2 if isinstance(sink, AuditLog) else 1)
    for key in fields:
        with pytest.raises(AuditWriteError):
            emit(sink, event, {k: v for k, v in fields.items() if k != key})
    for key in ("unexpected", "kid", "text", "body"):
        with pytest.raises(AuditWriteError):
            emit(sink, event, fields | {key: None})
    for key in fields.keys() - {"team", "reason"}:
        with pytest.raises(AuditWriteError):
            emit(sink, "side_effect.attempt", {key: fields[key]})


@pytest.mark.parametrize("event", [*CASES, "side_effect.attempt"])
def test_identity(sink, event):
    fields = CASES.get(event, {})
    system = event in CASES and event != "model.call"
    for task, actor in [
        (None, "system"),
        (None, "demo-platform-g3"),
        (uuid4().hex, "system"),
        (uuid4().hex, "demo-platform-g3"),
    ]:
        valid = (
            (task is None and actor == "system")
            if system
            else (task is not None and actor != "system")
        )
        if valid:
            emit(sink, event, fields, task_id=task, actor_sub=actor)
        else:
            with pytest.raises(AuditWriteError):
                emit(sink, event, fields, task_id=task, actor_sub=actor)


def test_bad_typed_values(sink, monkeypatch):
    from govops.audit.log import find_pii

    def forbidden(value):
        pytest.fail("Invalid typed field invoked PII regex")

    monkeypatch.setattr("govops.audit.log.find_pii", forbidden)
    for event, fields in CASES.items():
        for key in fields:
            for value in [
                None,
                1.5,
                "",
                "010 1234 5678",
                "A" * 64,
                "a" * 63,
                [],
                True,
                -1,
            ]:
                if (key == "fallback" and value is True) or (
                    key == "deployment_id" and value == "a" * 63
                ):
                    continue
                with pytest.raises(AuditWriteError):
                    emit(sink, event, fields | {key: value})
    for key in BUNDLE:
        for value in [None, True, -1, 10001, 1.5, "bad"]:
            with pytest.raises(AuditWriteError):
                emit(sink, "model.call", CASES["model.call"] | BUNDLE | {key: value})
    for value in [0, -1, True, 1.5, None, "1"]:
        with pytest.raises(AuditWriteError):
            emit(sink, "auth.failed", CASES["auth.failed"] | {"suppressed": value})
    monkeypatch.setattr("govops.audit.log.find_pii", find_pii)
    for reason in [
        "missing_credentials",
        "invalid_token",
        "unknown_user",
        "loopback_required",
    ]:
        emit(sink, "auth.failed", {"reason": reason, "suppressed": 1})
        with pytest.raises(AuditWriteError):
            emit(sink, "side_effect.result", {"reason": reason})
    with pytest.raises(AuditWriteError):
        emit(sink, "auth.failed", {"reason": "connect"})


def test_bundle_and_enums(sink):
    for mask in range(1, 15):
        partial = {k: v for i, (k, v) in enumerate(BUNDLE.items()) if mask & (1 << i)}
        with pytest.raises(AuditWriteError):
            emit(sink, "model.call", CASES["model.call"] | partial)
    for pre in _PRE:
        for post in _POST:
            emit(
                sink,
                "model.call",
                CASES["model.call"]
                | BUNDLE
                | {"guardrail_pre": pre, "guardrail_post": post},
            )
    for key, value in BUNDLE.items():
        with pytest.raises(AuditWriteError):
            emit(sink, "guardrail.error", {key: value})


def test_header_sets_and_model_aliases(sink):
    from govops.audit.log import GUARDRAIL_POST, GUARDRAIL_PRE

    assert GUARDRAIL_PRE == _PRE
    assert GUARDRAIL_POST == _POST
    config = Path("infra/litellm/config.yaml").read_text()
    aliases = re.findall(r"model_name: (\S+)", config)
    assert set(aliases) == {
        "agent-primary",
        "agent-fallback",
        "agent-primary-only",
        "agent-primary-down",
    }
    for model in aliases:
        emit(sink, "model.call", CASES["model.call"] | {"model": model})


def test_random_10000_typed_values(sink, monkeypatch):
    rng = random.Random(20261008)

    def forbidden(value):
        pytest.fail("Typed field invoked PII regex")

    monkeypatch.setattr("govops.audit.log.find_pii", forbidden)
    for _ in range(10000):
        digest = rng.randbytes(32).hex()
        short = rng.randbytes(6).hex()
        emit(
            sink,
            "service.start",
            {
                "policy_sha256": digest,
                "rules_sha256": digest,
                "access_index_sha256": digest,
                "hook_rules_digest": short,
                "hook_code_digest": short,
            },
        )
        emit(sink, "chain.genesis", CASES["chain.genesis"] | {"legacy_sha256": digest})
        emit(sink, "side_effect.attempt", {}, task_id=uuid4().hex)
    for event, key in [
        ("service.start", "policy_sha256"),
        ("chain.genesis", "legacy_sha256"),
        ("service.start", "hook_rules_digest"),
    ]:
        for value in ["a" * 63, "A" * 64, "01012345678"]:
            with pytest.raises(AuditWriteError):
                emit(sink, event, CASES[event] | {key: value})


def test_storage_shape(tmp_path):
    memory = MemoryAuditLog()
    log = AuditLog(tmp_path)
    emit(memory, "chain.anchor", CASES["chain.anchor"])
    emit(log, "chain.anchor", CASES["chain.anchor"])
    assert memory.records == [
        {
            "event": "chain.anchor",
            "task_id": None,
            "actor_sub": "system",
            "label": "test-1",
        }
    ]
    row = json.loads(next(tmp_path.glob("chain-*.jsonl")).read_text().splitlines()[-1])
    assert row["task_id"] is None
    assert row["actor"]["sub"] == "system"
    assert row["event"] == "chain.anchor"
    assert row["label"] == "test-1"
    assert set(row) == {
        "seq",
        "ts",
        "actor",
        "event",
        "task_id",
        "label",
        "prev_hash",
        "kid",
        "hash",
        "sig",
    }


def test_new_fields_are_event_scoped(sink):
    from govops.audit.log import EVENTS, SYSTEM_EVENTS

    assert SYSTEM_EVENTS == {
        "service.start",
        "chain.genesis",
        "chain.anchor",
        "auth.failed",
    }
    owners = {}
    for event, fields in CASES.items():
        for key, value in fields.items():
            if key not in {"team", "reason"}:
                owners[key] = (event, value)
    owners.update({key: ("model.call", value) for key, value in BUNDLE.items()})
    owners["suppressed"] = ("auth.failed", 1)
    for key, (owner, value) in owners.items():
        for event in EVENTS - {owner}:
            with pytest.raises(AuditWriteError):
                emit(sink, event, CASES.get(event, {}) | {key: value})


@pytest.mark.parametrize(
    "key,accepted,rejected",
    [
        ("team", ["platform", "none", "a" * 21], ["a", "a" * 22, "Platform"]),
        ("model", ["agent-a", "agent-" + "a" * 30], ["agent-", "agent-" + "a" * 31]),
        (
            "deployment_id",
            ["a", "a" * 64, "a.b-c", "010-1234-5678"],
            ["a" * 65, "-a", "A", "a_b"],
        ),
        (
            "cost_usd",
            ["0.000000", "123.123456"],
            ["-1.000000", "1.00000", "1.0000000", "1e-6"],
        ),
        ("fallback", [True, False], [0, 1, "false"]),
        ("guardrail_q", [0, 10000], [True, 10001, -1]),
        ("guardrail_m", [0, 10000], [False, 10001, -1]),
    ],
)
def test_model_boundaries(sink, key, accepted, rejected):
    fields = CASES["model.call"] | BUNDLE
    for value in accepted:
        emit(sink, "model.call", fields | {key: value})
    for value in rejected:
        with pytest.raises(AuditWriteError):
            emit(sink, "model.call", fields | {key: value})


def test_anchor_boundaries(sink):
    for label in ["a", "0", "a" * 40, "a-b-9", "010-1234-5678"]:
        emit(sink, "chain.anchor", {"label": label})
    for label in ["a" * 41, "-a", "A", "a_b", "a\n"]:
        with pytest.raises(AuditWriteError):
            emit(sink, "chain.anchor", {"label": label})


@pytest.mark.parametrize(
    "task_id", ["a" * 31, "a" * 33, "A" * 32, "01012345678", 1, True]
)
def test_invalid_task_identifiers(sink, task_id):
    with pytest.raises(AuditWriteError):
        emit(sink, "side_effect.attempt", {}, task_id=task_id)


@pytest.mark.parametrize("actor_sub", ["requester", "demo-platform", None, 1, True])
def test_invalid_actor_identifiers(sink, actor_sub):
    with pytest.raises(AuditWriteError):
        emit(sink, "side_effect.attempt", {}, actor_sub=actor_sub)

"""Minimal audit persistence and fail-closed contract."""

import hashlib
import json
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4

import pytest

from govops.audit import AuditLog, AuditWriteError, append_with_retry
from govops.audit.log import EVENTS


def append(log, **fields):
    return log.append(
        "side_effect.attempt",
        task_id=uuid4().hex,
        actor_sub="demo-platform-g3",
        **fields,
    )


def rows(path):
    return [
        json.loads(line)
        for file in sorted(path.glob("*.jsonl"))
        for line in file.read_text().splitlines()
    ]


def test_sequence_rotation_reopen_and_permissions(tmp_path):
    now = [datetime(2026, 10, 5, tzinfo=UTC)]
    directory = tmp_path / "audit"
    log = AuditLog(directory, clock=lambda: now[0])
    assert append(log) == 1
    now[0] = datetime(2026, 10, 6, tzinfo=UTC)
    assert append(log) == 2
    assert append(AuditLog(directory, clock=lambda: now[0])) == 3
    assert [row["seq"] for row in rows(directory)] == [1, 2, 3]
    assert directory.stat().st_mode & 0o777 == 0o700
    for file in directory.glob("*.jsonl"):
        assert file.stat().st_mode & 0o777 == 0o600
    row = rows(directory)[0]
    assert row["actor"] == {"sub": "demo-platform-g3", "act": "governed-ops-agent"}
    assert set(row) == {"seq", "ts", "actor", "event", "task_id"}


def test_concurrent_instances(tmp_path):
    logs = [AuditLog(tmp_path), AuditLog(tmp_path)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        seqs = list(pool.map(lambda i: append(logs[i % 2]), range(100)))
    assert sorted(seqs) == list(range(1, 101))


def test_events(tmp_path):
    log = AuditLog(tmp_path)
    assert len(EVENTS) == 14
    for event in EVENTS:
        log.append(event, task_id=uuid4().hex, actor_sub="demo-platform-g3")
    with pytest.raises(ValueError):
        log.append(
            "unknown",
            task_id=uuid4().hex,
            actor_sub="demo-platform-g3",
        )


@pytest.mark.parametrize(
    "key",
    [
        "text",
        "body",
        "args",
        "content",
        "description",
        "title",
        "seq",
        "actor",
        "hash",
        "prev_hash",
        "sig",
    ],
)
def test_forbidden_keys(tmp_path, key):
    with pytest.raises(ValueError):
        append(AuditLog(tmp_path), **{key: None})


@pytest.mark.parametrize(
    "value",
    [
        "raw text",
        "x 900101-1234567",
        "x 010-1234-5678",
        "x 4111111111111111",
        "x demo@example.invalid",
        "jira_delete_ticket",
        "approve_all",
        {},
        1.5,
    ],
)
def test_invalid_values(tmp_path, value):
    with pytest.raises(AuditWriteError) as caught:
        append(AuditLog(tmp_path), reason=value)
    assert caught.value.__cause__ is None
    assert str(value) not in str(caught.value)


def test_hashes_and_safe_file_values(tmp_path):
    log = AuditLog(tmp_path)
    rng = random.Random(731)
    hashes = [hashlib.sha256(rng.randbytes(32)).hexdigest() for _ in range(10000)]
    for digest in hashes:
        append(log, draft_sha256=digest)
    append(
        log,
        mock_id="message-1",
        ticket_id="PLT-123",
        comment_id="comment-2",
        tool="slack_send_message",
        decision="approve",
        found=True,
        count=2,
        result=None,
    )
    records = rows(tmp_path)
    assert [row["draft_sha256"] for row in records[:-1]] == hashes
    assert records[-1]["mock_id"] == "message-1"


def test_failure_and_retry(tmp_path, monkeypatch):
    log = AuditLog(tmp_path)
    monkeypatch.setattr(Path, "open", Mock(side_effect=PermissionError("sensitive")))
    with pytest.raises(AuditWriteError) as caught:
        append(log)
    assert caught.value.__cause__ is None
    sink = Mock()
    sink.append.side_effect = AuditWriteError("Audit write failed")
    assert not append_with_retry(
        sink,
        "side_effect.result",
        task_id=uuid4().hex,
        actor_sub="demo-platform-g3",
    )
    assert sink.append.call_count == 3
    sink.append.side_effect = [AuditWriteError("failed"), 1]
    assert append_with_retry(sink, "side_effect.result", attempts=2)
    with pytest.raises(ValueError):
        append_with_retry(sink, "side_effect.result", attempts=0)


def test_environment_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    append(AuditLog())
    assert len(rows(tmp_path / "audit")) == 1


@pytest.mark.parametrize(
    "tool", ["jira_add_comment", "jira_create_followup_ticket", "slack_send_message"]
)
@pytest.mark.parametrize("decision", ["approve", "edit_approve", "reject", "expired"])
def test_allowed_codes(tmp_path, tool, decision):
    assert append(AuditLog(tmp_path), tool=tool, decision=decision) == 1


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tool", "jira_delete_ticket"),
        ("decision", "approve_all"),
        ("tool", "a" * 64),
        ("decision", "message-1"),
    ],
)
def test_disallowed_codes(tmp_path, key, value):
    with pytest.raises(ValueError):
        append(AuditLog(tmp_path), **{key: value})


def test_hash_and_mock_ids_skip_pii(tmp_path, monkeypatch):
    import govops.audit.log as audit_module

    real_find = audit_module.find_pii
    checked = []

    def find(value):
        checked.append(value)
        return real_find(value)

    monkeypatch.setattr(audit_module, "find_pii", find)
    digest = "a" * 64
    append(AuditLog(tmp_path), draft_sha256=digest, mock_id="message-1")
    assert digest not in checked
    assert "message-1" not in checked


def test_directory_failure(tmp_path):
    file = tmp_path / "not_directory"
    file.write_text("")
    with pytest.raises(AuditWriteError) as caught:
        AuditLog(file)
    assert caught.value.__cause__ is None


def test_random_uuid_identifiers(tmp_path):
    from uuid import UUID

    # Seeded v4 UUIDs reproduce failures without replacing production uuid4.
    rng = random.Random(731)
    identifiers = [UUID(bytes=rng.randbytes(16), version=4).hex for _ in range(10000)]
    log = AuditLog(tmp_path)
    for task_id in identifiers:
        log.append("side_effect.draft", task_id=task_id, actor_sub="demo-platform-g3")
    assert [row["task_id"] for row in rows(tmp_path)] == identifiers


@pytest.mark.parametrize(
    "task_id,actor",
    [
        ("010-1234-5678", "demo-platform-g3"),
        ("abc900101-1234567", "demo-platform-g3"),
        ("a" * 31, "demo-platform-g3"),
        ("a" * 33, "demo-platform-g3"),
        ("G" * 32, "demo-platform-g3"),
        ("a" * 32, "requester"),
        ("a" * 32, "demo-platform"),
    ],
)
def test_invalid_identifiers(tmp_path, task_id, actor):
    with pytest.raises(AuditWriteError) as error:
        AuditLog(tmp_path).append("side_effect.draft", task_id=task_id, actor_sub=actor)
    assert isinstance(error.value, ValueError)


@pytest.mark.parametrize(
    "event,fields",
    [
        (
            "guardrail.input_blocked",
            {
                "code": "injection_direct",
                "input_sha256": "a" * 64,
                "rules_digest": "b" * 12,
                "code_digest": "c" * 12,
            },
        ),
        ("guardrail.input_blocked", {"code": "pii_input"}),
        ("guardrail.tool_quarantined", {"tool_call_sha256": "a" * 64, "count": 1}),
        ("guardrail.tool_masked", {"count": 2}),
        ("guardrail.output_flagged", {"team": "platform"}),
        ("guardrail.error", {"code": "guardrail_post_error"}),
    ],
)
def test_guardrail_events_jsonl(tmp_path, event, fields):
    log = AuditLog(tmp_path)
    log.append(event, task_id=uuid4().hex, actor_sub="demo-platform-g3", **fields)
    row = rows(tmp_path)[0]
    assert row["event"] == event
    assert all(row[k] == v for k, v in fields.items())
    if fields.get("code") == "pii_input":
        assert "input_sha256" not in row


@pytest.mark.parametrize(
    "event,fields",
    [
        ("guardrail.input_blocked", {"code": "pii_input", "input_sha256": "a" * 64}),
        ("guardrail.error", {"code": "injection_direct", "input_sha256": "a" * 64}),
        ("guardrail.input_blocked", {"code": "unknown"}),
        ("guardrail.tool_quarantined", {"tool_call_sha256": "a" * 63}),
        (
            "guardrail.input_blocked",
            {"code": "injection_direct", "input_sha256": "g" * 64},
        ),
        ("guardrail.error", {"rules_digest": "a" * 64}),
        ("guardrail.error", {"code_digest": "A" * 12}),
        ("guardrail.error", {"team": "Platform"}),
        ("guardrail.error", {"content": "a" * 64}),
        ("guardrail.error", {"code": 1}),
        ("guardrail.error", {"tool_call_sha256": None}),
    ],
)
def test_guardrail_fields_rejected(tmp_path, event, fields):
    from govops.audit.log import MemoryAuditLog

    for log in (AuditLog(tmp_path), MemoryAuditLog()):
        with pytest.raises(AuditWriteError):
            log.append(
                event, task_id=uuid4().hex, actor_sub="demo-platform-g3", **fields
            )


def test_guardrail_random_identifiers_never_run_pii(monkeypatch):
    from uuid import UUID

    from govops.audit.log import MemoryAuditLog

    # Seeded v4 UUIDs make the 10,000 random samples reproducible.
    rng = random.Random(731)
    log = MemoryAuditLog()

    def forbidden(value):
        pytest.fail("PII regex called for a typed identifier")

    monkeypatch.setattr("govops.audit.log.find_pii", forbidden)
    for _ in range(10000):
        ident = UUID(bytes=rng.randbytes(16), version=4).hex
        log.append(
            "guardrail.input_blocked",
            task_id=ident,
            actor_sub="demo-platform-g3",
            code="injection_direct",
            input_sha256=ident * 2,
            tool_call_sha256=ident * 2,
            rules_digest=ident[:12],
            code_digest=ident[:12],
            team="platform",
        )
    assert len(log.records) == 10000

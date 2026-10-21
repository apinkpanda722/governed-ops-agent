"""Offline, synthetic evidence for the read-only records boundary."""

import json
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from govops.audit import records
from govops.audit.crypto import canonical_json, kid_of, record_hash
from govops.audit.log import AuditLog

TASK = "1" * 32
OTHER = "2" * 32
ACTOR = "demo-platform-g3"
BASE = {"seq", "ts", "event", "actor", "hash12"}
ALLOWED = {
    "tool",
    "decision",
    "check_result",
    "reason",
    "code",
    "mock_id",
    "fallback",
    "cost_usd",
    "tool_name",
    "status",
    "result_count",
}


def write_rows(path, rows):
    path.write_text("".join(canonical_json(row) + "\n" for row in rows))


@pytest.mark.parametrize("result_count", [0, 1, 10000])
def test_tool_call_projection_excludes_argument_and_call_hashes(tmp_path, result_count):
    from govops.audit.argmac import load_args_mac
    from govops.audit.toolcall import ToolCallAuditor

    now = datetime(2026, 10, 21, tzinfo=UTC)
    audit = AuditLog(tmp_path / "audit", clock=lambda: now)
    auditor = ToolCallAuditor(audit, load_args_mac())
    seq = auditor.record(
        task_id=TASK,
        actor_sub=ACTOR,
        tool_name="search_docs",
        tool_call_id="synthetic-call",
        arguments='{"query":"합성 검색"}',
        status="ok",
        result_count=result_count,
    )
    rows = [
        json.loads(line)
        for path in audit.directory.glob("chain-*.jsonl")
        for line in path.read_bytes().splitlines()
    ]
    row = next(row for row in rows if row["seq"] == seq)
    events, truncated = records.read_events(audit.directory, [TASK])
    assert not truncated
    assert events[TASK] == [
        {
            "seq": seq,
            "ts": now.isoformat(),
            "event": "tool.call",
            "actor": ACTOR,
            "hash12": row["hash"][:12],
            "tool_name": "search_docs",
            "status": "ok",
            "result_count": result_count,
        }
    ]
    encoded = json.dumps(events)
    for key in ("args_hmac", "args_hmac_kid", "tool_call_sha256"):
        assert key not in encoded
        assert row[key] not in encoded


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("tool_name", "unregistered_tool"),
        ("tool_name", True),
        ("status", "unregistered_status"),
        ("status", True),
        ("result_count", True),
        ("result_count", False),
        ("result_count", -1),
        ("result_count", 10001),
        ("result_count", 1.0),
        ("result_count", "1"),
        ("result_count", None),
    ],
)
def test_tool_call_invalid_display_values_are_not_exposed(tmp_path, key, value):
    from govops.audit.argmac import load_args_mac
    from govops.audit.toolcall import ToolCallAuditor

    audit = AuditLog(tmp_path / "audit")
    ToolCallAuditor(audit, load_args_mac()).record(
        task_id=TASK,
        actor_sub=ACTOR,
        tool_name="search_docs",
        tool_call_id="synthetic-call",
        arguments="{}",
        status="ok",
    )
    path = next(audit.directory.glob("chain-*.jsonl"))
    row = json.loads(path.read_bytes().splitlines()[-1])
    assert records._display_field(key, value) is False
    # Bypass writer canonicalization to exercise untrusted JSON, including floats.
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in ({**row, key: value}, row))
    )
    events, truncated = records.read_events(audit.directory, [TASK])
    assert not truncated
    assert len(events[TASK]) == 1
    assert events[TASK][0][key] == row[key]


def test_tool_calls_keep_default_500_event_limit(tmp_path):
    from govops.audit.argmac import load_args_mac
    from govops.audit.toolcall import ToolCallAuditor

    audit = AuditLog(tmp_path / "audit")
    auditor = ToolCallAuditor(audit, load_args_mac())
    for index in range(600):
        auditor.record(
            task_id=TASK,
            actor_sub=ACTOR,
            tool_name="search_docs",
            tool_call_id=f"synthetic-call-{index}",
            arguments="{}",
            status="ok",
        )
    events, truncated = records.read_events(audit.directory, [TASK])
    assert truncated is True
    assert len(events[TASK]) == 500
    assert all(event["event"] == "tool.call" for event in events[TASK])
    assert [event["seq"] for event in events[TASK]] == list(range(2, 502))


@pytest.fixture
def chain(tmp_path, audit_test_directory):
    directory = tmp_path / "evidence/audit"
    # Legacy evidence must also survive the snapshot, including its seal.
    directory.mkdir(parents=True)
    (directory / "2026-10-01.jsonl").write_text('{"seq":1}\n')
    log = AuditLog(directory, clock=lambda: datetime(2026, 10, 16, tzinfo=UTC))
    for task in (TASK, OTHER, TASK):
        log.append(
            "side_effect.draft",
            task_id=task,
            actor_sub=ACTOR,
            tool="slack_send_message",
            draft_hash="a" * 64,
        )
    pending = tmp_path / "pending.jsonl"
    log.anchor("synthetic", repo_root=tmp_path, pending_file=pending)
    path = next(directory.glob("chain-*.jsonl"))
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    return directory, path, rows, pending, audit_test_directory / "public.pem", tmp_path


def summary(chain, **overrides):
    directory, _, _, pending, public, root = chain
    return records.verify_summary(
        directory,
        public_key_path=public,
        anchor_paths=[pending],
        repo_root=root,
        **overrides,
    )


def test_projection_and_conversation_wide_limit(chain):
    directory, _, rows, *_ = chain
    assert records.ALLOWED_EVENT_FIELDS == frozenset(ALLOWED)
    events, truncated = records.read_events(directory, [TASK, OTHER])
    assert not truncated
    assert len(events[TASK]) == 2
    assert len(events[OTHER]) == 1
    expected = [row for row in rows if row["task_id"] == TASK]
    assert [event["seq"] for event in events[TASK]] == [r["seq"] for r in expected]
    selected, _ = records.read_events(directory, [TASK])
    assert set(selected) == {TASK}
    assert selected[TASK] == events[TASK]
    assert events[TASK]
    assert all(set(event) == BASE | {"tool", "draft_hash12"} for event in events[TASK])
    encoded = json.dumps(events)
    assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}", encoded)
    assert rows[0]["kid"] not in encoded
    limited, truncated = records.read_events(directory, [TASK, OTHER], limit=2)
    assert truncated
    assert [e["seq"] for e in limited[TASK]] == [expected[0]["seq"]]
    assert len(limited[OTHER]) == 1
    assert records.read_events(directory, [TASK], limit=2)[1] is False
    assert records.read_events(directory, [TASK], limit=0) == ({TASK: []}, True)


def test_all_allowed_fields_and_excluded_fields(chain):
    directory, path, rows, *_ = chain
    row = rows[1]
    row.update(
        tool="jira_add_comment",
        decision="approve",
        check_result="passed",
        reason="already_succeeded",
        mock_id="comment-12",
        extra_hash="e" * 64,
    )
    blocked = {
        **row,
        "event": "guardrail.input_blocked",
        "code": "injection_direct",
        "input_sha256": "b" * 64,
        "tool_call_sha256": "c" * 64,
        "team": "platform",
    }
    model = {
        **rows[1],
        "event": "model.call",
        "team": "platform",
        "model": "agent-primary",
        "deployment_id": "synthetic",
        "fallback": False,
        "cost_usd": "0.123456",
    }
    for key in (
        "tool",
        "draft_hash",
        "decision",
        "check_result",
        "reason",
        "mock_id",
        "extra_hash",
    ):
        model.pop(key, None)
    write_rows(path, [row, blocked, model])
    events, _ = records.read_events(directory, [TASK])
    assert len(events[TASK]) == 3
    first, second, third = events[TASK]
    assert set(first) == BASE | {
        "tool",
        "decision",
        "check_result",
        "reason",
        "mock_id",
        "draft_hash12",
    }
    assert second["code"] == "injection_direct"
    assert third == {
        **{key: first[key] for key in BASE},
        "event": "model.call",
        "fallback": False,
        "cost_usd": "0.123456",
    }
    encoded = json.dumps(events)
    assert not re.search(r"[0-9a-f]{64}", encoded)
    assert all(
        set(event) <= BASE | ALLOWED | {"draft_hash12"} for event in events[TASK]
    )


@pytest.mark.parametrize("mock_id", ["message-1", "comment-42", "ABC-123"])
def test_mock_id_format(chain, mock_id):
    directory, path, rows, *_ = chain
    write_rows(path, [{**rows[1], "mock_id": mock_id}])
    assert records.read_events(directory, [TASK])[0][TASK][0]["mock_id"] == mock_id


def test_field_type_defense_keeps_other_fields(chain):
    directory, path, rows, *_ = chain
    # The writer accepts some numeric/null fields and generic hash/mock strings.
    # The reader's display allowlist must be stricter, field by field.
    row = {
        **rows[1],
        "tool": 1,
        "decision": True,
        "check_result": 5,
        "reason": None,
        "mock_id": "a" * 64,
        "draft_hash": True,
    }
    write_rows(path, [row])
    events, _ = records.read_events(directory, [TASK])
    assert len(events[TASK]) == 1
    assert set(events[TASK][0]) == BASE


@pytest.mark.parametrize(
    "bad",
    [
        {"event": "unknown"},
        {"task_id": "bad"},
        {"actor": {"sub": "bad"}},
        {"text": "a" * 64},
        {"code": "unknown"},
        {"hash": "bad"},
        {"seq": True},
        {"ts": "bad"},
    ],
)
def test_invalid_records_are_skipped(chain, bad):
    directory, path, rows, *_ = chain
    write_rows(path, [{**rows[1], **bad}, rows[1]])
    with path.open("ab") as target:
        target.write(b"not-json\n[]\n\xff\n")
    events, _ = records.read_events(directory, [TASK])
    assert len(events[TASK]) == 1


def test_final_incomplete_line_and_sorted_rotation(chain):
    directory, path, rows, *_ = chain
    write_rows(path, [rows[1]])
    later = directory / "chain-2026-10-17.jsonl"
    later.write_bytes(
        canonical_json(rows[3]).encode() + b"\n" + canonical_json(rows[1]).encode()
    )
    events, truncated = records.read_events(directory, [TASK])
    assert not truncated
    assert [event["seq"] for event in events[TASK]] == [rows[1]["seq"], rows[3]["seq"]]


def test_random_v4_identifiers_do_not_run_pii(chain, monkeypatch):
    directory, path, rows, *_ = chain
    rng = random.Random(731)
    ids = [UUID(bytes=rng.randbytes(16), version=4).hex for _ in range(10000)]
    write_rows(path, [{**rows[1], "task_id": task} for task in ids])
    monkeypatch.setattr(
        "govops.audit.log.find_pii",
        Mock(side_effect=AssertionError("typed identifier")),
    )
    events, truncated = records.read_events(directory, ids, limit=10000)
    assert not truncated
    assert len(events) == 10000
    assert all(len(events[task]) == 1 for task in ids)


def test_unavailable_directory_and_read_failure(tmp_path, chain, monkeypatch):
    with pytest.raises(records.AuditUnavailable) as caught:
        records.read_events(tmp_path / "missing", [TASK])
    assert caught.value.code == "audit_unavailable"
    directory = chain[0]
    monkeypatch.setattr(
        Path, "read_bytes", Mock(side_effect=PermissionError("private path"))
    )
    with pytest.raises(records.AuditUnavailable) as caught:
        records.read_events(directory, [TASK])
    assert str(caught.value) == "audit_unavailable"


def test_summary_contract_and_read_only(chain):
    directory, path, _, pending, *_ = chain
    before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in directory.iterdir()}
    before[pending] = (pending.read_bytes(), pending.stat().st_mtime_ns)
    value = summary(chain)
    assert set(value) == {
        "status",
        "checked_at",
        "chain",
        "anchors",
        "legacy_unverified",
        "known_limit",
    }
    assert value["status"] == "pass"
    assert datetime.fromisoformat(value["checked_at"]).utcoffset().total_seconds() == 0
    assert value["chain"] == {
        "records": 5,
        "files": 1,
        "anchors_checked": 1,
        "problems": [],
    }
    assert value["anchors"] == {"committed": 0, "pending": 1}
    assert value["legacy_unverified"] == {"files": 1, "lines": 1}
    assert value["known_limit"] == "tail_after_last_anchor_not_detected"
    records.read_events(directory, [TASK])
    assert path in before
    assert set(directory.iterdir()) == set(before) - {pending}
    assert {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before} == before


@pytest.mark.parametrize(
    ("kind", "codes"),
    [
        ("modify", {"hash_mismatch"}),
        ("delete", {"seq_gap"}),
        ("reorder", {"seq_order", "prev_hash_mismatch"}),
        ("tail", {"tail_truncated"}),
        ("key", {"kid_unknown"}),
    ],
)
def test_summary_tampering(chain, kind, codes):
    _, path, rows, *_ = chain
    if kind == "modify":
        rows[1]["tool"] = "jira_add_comment"
    elif kind == "delete":
        del rows[2]
    elif kind == "reorder":
        rows[1], rows[2] = rows[2], rows[1]
    elif kind == "tail":
        rows = rows[:-1]
    else:
        other = Ed25519PrivateKey.generate()
        previous = "0" * 64
        for row in rows:
            row["kid"] = kid_of(other.public_key())
            row["prev_hash"] = previous
            row["hash"] = record_hash(
                {k: v for k, v in row.items() if k not in {"hash", "sig"}}
            )
            row["sig"] = other.sign(row["hash"].encode("ascii")).hex()
            previous = row["hash"]
    write_rows(path, rows)
    value = summary(chain)
    assert value["status"] == "fail"
    problems = value["chain"]["problems"]
    assert problems
    assert all(set(problem) <= {"code", "seq"} for problem in problems)
    assert codes & {problem["code"] for problem in problems}
    encoded = json.dumps(value)
    assert not re.search(r"[0-9a-f]{64}", encoded)
    assert str(path) not in encoded
    assert rows[0]["kid"] not in encoded


def test_partial_tail_uses_relative_snapshot_and_preserves_evidence(chain):
    directory, path, rows, *_ = chain
    path.write_bytes(path.read_bytes() + canonical_json(rows[1]).encode()[:35])
    before = path.read_bytes(), path.stat().st_mtime_ns
    value = summary(chain)
    assert value["status"] == "pass"
    assert value["chain"]["anchors_checked"] == 1
    assert value["chain"]["records"] == 5
    assert len(records.read_events(directory, [TASK])[0][TASK]) == 2
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_incomplete_genesis_is_unavailable(chain):
    _, path, rows, *_ = chain
    path.write_bytes(canonical_json(rows[0]).encode()[:35])
    before = path.read_bytes(), path.stat().st_mtime_ns
    with pytest.raises(records.AuditUnavailable):
        summary(chain)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_complete_chain_uses_original_directory_and_partial_snapshot_is_removed(
    chain, monkeypatch
):
    directory, path, *_ = chain
    original = records.verify_chain
    calls = []

    def verify(target, **kwargs):
        calls.append((target, kwargs["repo_root"]))
        return original(target, **kwargs)

    monkeypatch.setattr(records, "verify_chain", verify)
    assert summary(chain)["status"] == "pass"
    assert calls == [(directory, chain[-1])]
    path.write_bytes(path.read_bytes() + b'{"seq":')
    assert summary(chain)["status"] == "pass"
    target, snapshot_root = calls[-1]
    assert target != directory
    assert target.relative_to(snapshot_root) == directory.relative_to(chain[-1])
    assert not snapshot_root.exists()


def test_defaults_are_rooted_and_anchor_counts_are_lines(chain, monkeypatch):
    directory, _, _, pending, public, root = chain
    committed = root / "docs/results/audit-anchors.jsonl"
    committed.parent.mkdir(parents=True)
    committed.write_bytes(pending.read_bytes() * 2)
    default_pending = root / "data/interim/audit/anchors-pending.jsonl"
    default_pending.parent.mkdir(parents=True)
    default_pending.write_bytes(pending.read_bytes())
    default_public = root / "data/seed/audit_public_key.pem"
    default_public.parent.mkdir(parents=True)
    default_public.write_bytes(public.read_bytes())
    monkeypatch.setattr(records, "ROOT", root)
    monkeypatch.setenv("AUDIT_PUBLIC_KEY_FILE", " ")
    monkeypatch.setenv("AUDIT_ANCHORS_PENDING_FILE", "")
    monkeypatch.chdir(root.parent)
    value = records.verify_summary(directory.relative_to(root))
    assert value["status"] == "pass"
    assert value["chain"]["anchors_checked"] == 1
    assert value["anchors"] == {"committed": 2, "pending": 1}
    monkeypatch.setenv("AUDIT_PUBLIC_KEY_FILE", str(default_public.relative_to(root)))
    monkeypatch.setenv("AUDIT_ANCHORS_PENDING_FILE", "pending.jsonl")
    assert records.verify_summary(directory, repo_root=root)["status"] == "pass"


def test_missing_anchor_directory_fails(chain):
    _, _, _, pending, *_ = chain
    fixed = json.loads(pending.read_bytes())
    pending.write_text(canonical_json({**fixed, "dir": "missing/audit"}) + "\n")
    value = summary(chain)
    assert value["status"] == "fail"
    assert {"code": "anchor_dir_missing"} in value["chain"]["problems"]


@pytest.mark.parametrize("target", ["anchor", "public"])
def test_invalid_anchor_or_key_is_unavailable(chain, target):
    _, _, _, pending, public, _ = chain
    (pending if target == "anchor" else public).write_text("broken\n")
    with pytest.raises(records.AuditUnavailable) as caught:
        summary(chain)
    assert caught.value.code == "audit_unavailable"
    assert caught.value.__cause__ is None


def test_cache_single_flight_and_ttl():
    now = [0.0]
    cache = records.VerifySummaryCache(clock=lambda: now[0])
    barrier = threading.Barrier(10, timeout=5)
    entered = threading.Event()
    release = threading.Event()

    def compute():
        entered.set()
        assert release.wait(timeout=5)
        return {"version": 1}

    mocked = Mock(side_effect=compute)

    def get():
        barrier.wait()
        return cache.get("chain", mocked)

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(get) for _ in range(10)]
        assert entered.wait(timeout=5)
        release.set()
        values = [future.result(timeout=5) for future in futures]
    assert mocked.call_count == 1
    assert len(values) == 10
    assert all(value is values[0] for value in values)
    now[0] = 9.999
    assert cache.get("chain", mocked) is values[0]
    now[0] = 10.0
    assert cache.get("chain", mocked) is not values[0]
    assert mocked.call_count == 2
    cache.get("other", mocked)
    assert mocked.call_count == 3


def test_cache_exception_is_not_cached():
    cache = records.VerifySummaryCache(ttl=10.0)
    compute = Mock(side_effect=[ValueError("synthetic"), {"ok": True}])
    with pytest.raises(ValueError, match="synthetic"):
        cache.get("chain", compute)
    assert cache.get("chain", compute) == {"ok": True}
    assert cache.get("chain", compute) == {"ok": True}
    assert compute.call_count == 2

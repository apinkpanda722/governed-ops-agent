"""Synthetic contracts for tool-call audit records and argument HMACs."""

import hashlib
import hmac
import json
import random
import re
import secrets
from pathlib import Path

import pytest

from govops.audit import argmac
from govops.audit.argmac import ArgsMacKeyError, load_args_mac
from govops.audit.log import (
    EVENTS,
    SYSTEM_EVENTS,
    TOOL_CALL_NAMES,
    TOOL_CALL_STATUSES,
    AuditLog,
    AuditWriteError,
    MemoryAuditLog,
)
from govops.audit.toolcall import ToolCallAuditor
from govops.audit.verify import verify_chain

FIELDS = {
    "tool_name": "search_docs",
    "status": "ok",
    "result_count": 0,
    "tool_call_sha256": "a" * 64,
    "args_hmac": "b" * 64,
    "args_hmac_kid": "c" * 12,
}
IDENTITY = {"task_id": "d" * 32, "actor_sub": "demo-platform-g3"}


@pytest.fixture
def mac_key(monkeypatch):
    key_hex = secrets.token_hex(32)
    monkeypatch.setenv(argmac.ENV_NAME, key_hex)
    return load_args_mac(), bytes.fromhex(key_hex)


@pytest.fixture(params=[AuditLog, MemoryAuditLog])
def sink(request, tmp_path):
    return request.param(tmp_path) if request.param is AuditLog else request.param()


def emit(sink, fields, **identity):
    return sink.append("tool.call", **(IDENTITY | identity), **fields)


def record(auditor, **changes):
    return auditor.record(
        **(
            IDENTITY
            | {
                "tool_name": "search_docs",
                "tool_call_id": "synthetic-call",
                "arguments": '{"query":"합성 검색어"}',
                "status": "ok",
            }
            | changes
        )
    )


def assert_absent(text, *sensitive):
    if any(value in text for value in sensitive):
        pytest.fail("Sensitive synthetic value exposed", pytrace=False)


def test_signed_record_exact_fields_and_chain(tmp_path, mac_key):
    mac, key = mac_key
    auditor = ToolCallAuditor(AuditLog(tmp_path), mac)
    assert record(auditor) == 2
    rows = [
        json.loads(line)
        for path in tmp_path.glob("chain-*.jsonl")
        for line in path.read_text().splitlines()
    ]
    row = rows[-1]
    assert set(row) == set(FIELDS) | {
        "seq",
        "ts",
        "actor",
        "event",
        "task_id",
        "prev_hash",
        "kid",
        "hash",
        "sig",
    }
    assert row["event"] == "tool.call"
    assert row["actor"] == {"sub": IDENTITY["actor_sub"], "act": "governed-ops-agent"}
    assert row["task_id"] == IDENTITY["task_id"]
    assert {k: row[k] for k in FIELDS} == FIELDS | {
        "tool_call_sha256": hashlib.sha256(b"synthetic-call").hexdigest(),
        "args_hmac": mac.digest("search_docs", '{"query":"합성 검색어"}'),
        "args_hmac_kid": mac.kid,
    }
    assert_absent(
        json.dumps(rows, ensure_ascii=False), key.hex(), "합성 검색어", "synthetic-call"
    )
    report = verify_chain(tmp_path, repo_root=tmp_path)
    assert report.ok
    assert report.records == 2


def test_required_and_extra_fields(sink):
    assert emit(sink, FIELDS) == (2 if isinstance(sink, AuditLog) else 1)
    for missing in FIELDS:
        with pytest.raises(AuditWriteError):
            emit(sink, {k: v for k, v in FIELDS.items() if k != missing})
    for extra in ("unexpected", "args_sha256", "args", "body", "team"):
        with pytest.raises(AuditWriteError):
            emit(sink, FIELDS | {extra: "a" * 64})


@pytest.mark.parametrize(
    "key,value",
    [
        ("tool_call_sha256", "A" * 64),
        ("tool_call_sha256", "a" * 63),
        ("args_hmac", "A" * 64),
        ("args_hmac", "a" * 65),
        ("args_hmac_kid", "a" * 13),
        ("args_hmac_kid", "A" * 12),
        ("result_count", -1),
        ("result_count", 10001),
        ("result_count", True),
        ("result_count", False),
        ("result_count", "1"),
        ("result_count", 1.5),
        ("status", "invented"),
        ("tool_name", "invented"),
        *[(key, None) for key in FIELDS],
    ],
)
def test_invalid_fields(sink, key, value):
    with pytest.raises(AuditWriteError):
        emit(sink, FIELDS | {key: value})


def test_all_enums_and_count_boundaries(sink):
    for name in TOOL_CALL_NAMES:
        for status in TOOL_CALL_STATUSES:
            for count in (0, 10000):
                emit(
                    sink,
                    FIELDS
                    | {"tool_name": name, "status": status, "result_count": count},
                )


@pytest.mark.parametrize(
    "identity",
    [
        {"task_id": None, "actor_sub": "system"},
        {"task_id": "A" * 32},
        {"task_id": "a" * 31},
        {"actor_sub": "system"},
        {"actor_sub": "requester"},
    ],
)
def test_requires_task_identity(sink, identity):
    assert "tool.call" not in SYSTEM_EVENTS
    with pytest.raises(AuditWriteError):
        emit(sink, FIELDS, **identity)


def test_guardrail_hash_remains_accepted(sink):
    sink.append(
        "guardrail.tool_quarantined",
        **IDENTITY,
        tool_call_sha256=FIELDS["tool_call_sha256"],
        count=1,
    )


def test_fields_rejected_on_all_other_events(sink):
    from test_audit_events import CASES

    for event in EVENTS - {"tool.call"}:
        identity = (
            {"task_id": None, "actor_sub": "system"}
            if event in SYSTEM_EVENTS
            else IDENTITY
        )
        for key in FIELDS.keys() - {"tool_call_sha256"}:
            with pytest.raises(AuditWriteError):
                sink.append(
                    event, **identity, **(CASES.get(event, {}) | {key: FIELDS[key]})
                )


@pytest.mark.parametrize("value", [None, "", " \t\n"])
def test_missing_key(monkeypatch, value):
    monkeypatch.delenv(argmac.ENV_NAME, raising=False)
    if value is not None:
        monkeypatch.setenv(argmac.ENV_NAME, value)
    with pytest.raises(ArgsMacKeyError) as caught:
        load_args_mac()
    assert caught.value.code == "audit_args_hmac_key_missing"
    assert str(caught.value) == caught.value.code


@pytest.mark.parametrize("kind", ["upper", "short", "long", "padding", "nonhex"])
def test_invalid_key(monkeypatch, kind):
    key = secrets.token_hex(32)
    invalid = {
        "upper": "A" + key[1:],
        "short": key[:-1],
        "long": key + "a",
        "padding": " " + key,
        "nonhex": "g" + key[1:],
    }[kind]
    monkeypatch.setenv(argmac.ENV_NAME, invalid)
    with pytest.raises(ArgsMacKeyError) as caught:
        load_args_mac()
    assert caught.value.code == "audit_args_hmac_key_invalid"
    assert_absent(str(caught.value) + repr(caught.value), key, invalid)


def test_reused_key_and_safe_repr(monkeypatch, mac_key):
    mac, key = mac_key
    with pytest.raises(ArgsMacKeyError) as caught:
        load_args_mac(signing_key_raw=key)
    assert caught.value.code == "audit_args_hmac_key_reused"
    assert_absent(
        str(caught.value) + repr(caught.value) + repr(mac), key.hex(), repr(key)
    )
    assert load_args_mac(signing_key_raw=b"different-signing-key").kid == mac.kid
    assert mac.kid in repr(mac)
    with pytest.raises(ValueError) as invalid:
        ArgsMacKeyError(key.hex())
    assert_absent(str(invalid.value) + repr(invalid.value), key.hex())


def test_registered_kid_and_hmac_formulas(mac_key):
    mac, key = mac_key
    assert mac.kid == hashlib.sha256(b"govops-args-hmac\0" + key).hexdigest()[:12]
    name = "search_docs"
    arguments = '{ "z": 2, "query": "합성" }'
    data = json.dumps(
        {"tool": name, "args": {"query": "합성", "z": 2}},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    expected = hmac.new(key, data, hashlib.sha256).hexdigest()
    assert mac.digest(name, arguments) == expected
    assert mac.digest(name, '{"query":"합성","z":2}') == expected
    for raw in ("not json", "[]", "null", '"string"', '{"x":NaN}', '{"x":Infinity}'):
        expected = hmac.new(
            key, ("raw\0" + name + "\0" + raw).encode(), hashlib.sha256
        ).hexdigest()
        assert mac.digest(name, raw) == expected
    for nonstring in (None, 1, True, {}, {"query": "합성"}, []):
        expected = hmac.new(
            key, ("raw-nonstr\0" + name).encode(), hashlib.sha256
        ).hexdigest()
        assert mac.digest(name, nonstring) == expected


def test_known_and_unknown_names_and_call_ids(mac_key):
    mac, _ = mac_key
    sink = MemoryAuditLog()
    auditor = ToolCallAuditor(sink, mac)
    for name in sorted(TOOL_CALL_NAMES - {"unknown"}) + [
        "invented-name",
        "unknown",
        None,
        [],
    ]:
        assert record(auditor, tool_name=name, tool_call_id=None) == len(sink.records)
        row = sink.records[-1]
        known = isinstance(name, str) and name in TOOL_CALL_NAMES - {"unknown"}
        assert row["tool_name"] == (name if known else "unknown")
        assert row["args_hmac"] == mac.digest(str(name), '{"query":"합성 검색어"}')
        assert row["tool_call_sha256"] == hashlib.sha256(b"").hexdigest()
    assert record(auditor, tool_call_id=123) == len(sink.records)
    assert sink.records[-1]["tool_call_sha256"] == hashlib.sha256(b"").hexdigest()


def test_append_exception_propagates(mac_key):
    error = AuditWriteError("Synthetic write failure")

    class FailingAudit:
        def append(self, *args, **kwargs):
            raise error

    with pytest.raises(AuditWriteError) as caught:
        record(ToolCallAuditor(FailingAudit(), mac_key[0]))
    assert caught.value is error


def test_random_call_id_hashes_all_recorded_without_pii(monkeypatch, mac_key):
    def forbidden(value):
        pytest.fail("Typed tool-call field invoked PII regex")

    monkeypatch.setattr("govops.audit.log.find_pii", forbidden)
    rng = random.Random(20261021)
    ids = [rng.randbytes(32).hex() for _ in range(10000)]
    sink = MemoryAuditLog()
    auditor = ToolCallAuditor(sink, mac_key[0])
    for call_id in ids:
        record(auditor, tool_call_id=call_id)
    assert len(sink.records) == 10000
    assert [row["tool_call_sha256"] for row in sink.records] == [
        hashlib.sha256(call_id.encode("utf-8")).hexdigest() for call_id in ids
    ]


def test_constants_match_preregistered_lists():
    text = (
        Path(__file__).resolve().parents[1] / "docs/results/hypotheses-m8-tool-audit.md"
    ).read_text()
    section3 = text.split("## 3. ", 1)[1].split("## 4. ", 1)[0]
    section10 = text.split("## 10. ", 1)[1].split("## 11. ", 1)[0]

    def codes(section, label):
        rows = [
            line for line in section.splitlines() if line.startswith(f"| {label} |")
        ]
        assert len(rows) == 1
        values = set(re.findall(r"`([^`]+)`", rows[0].split("|")[2]))
        assert values
        return values

    assert codes(section3, "`tool_name`") == TOOL_CALL_NAMES
    assert codes(section3, "`status`") == TOOL_CALL_STATUSES
    assert codes(section10, "status") == TOOL_CALL_STATUSES
    assert codes(section10, "키 오류") == argmac._KEY_CODES
    assert codes(section10, "이벤트") == {"tool.call"}
    assert argmac.ENV_NAME == "AUDIT_ARGS_HMAC_KEY"

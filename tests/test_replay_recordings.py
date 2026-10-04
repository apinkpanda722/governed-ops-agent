"""Recording integrity, safety, and deterministic serialization."""

import hashlib
import json
from dataclasses import replace

import pytest

from govops.replay.recordings import (
    Recording,
    RecordingRejected,
    RecordingSet,
    arguments_key,
    validate_recording,
    write_recordings,
)


def entry(**kwargs):
    return replace(
        Recording(
            "demo-platform-g3",
            "lookup_id",
            {"value": "장애", "kind": "incident"},
            {"doc_id": "syn-incident-022"},
            False,
        ),
        **kwargs,
    )


def test_roundtrip_sorted_and_canonical(tmp_path):
    records = [
        entry(),
        entry(caller="demo-finance-g3"),
        entry(arguments={"value": "다름"}),
    ]
    write_recordings(tmp_path, records)
    first = (tmp_path / "recordings.jsonl").read_bytes()
    write_recordings(tmp_path, reversed(records))
    assert first == (tmp_path / "recordings.jsonl").read_bytes()
    loaded = RecordingSet.load(tmp_path)
    assert loaded.entries() == sorted(
        records, key=lambda r: (r.caller, r.tool, arguments_key(r.arguments))
    )
    assert (
        loaded.lookup(
            records[0].caller,
            records[0].tool,
            dict(reversed(list(records[0].arguments.items()))),
        )
        == records[0]
    )
    assert loaded.lookup("absent", "lookup_id", {}) is None
    assert (
        arguments_key({"b": "한글", "a": 1})
        == hashlib.sha256('{"a":1,"b":"한글"}'.encode()).hexdigest()
    )
    assert RecordingSet.load(tmp_path / "missing").entries() == []


def test_duplicate_and_invalid_file(tmp_path):
    write_recordings(tmp_path, [entry()])
    path = tmp_path / "recordings.jsonl"
    path.write_bytes(path.read_bytes() * 2)
    with pytest.raises(RecordingRejected, match="duplicate"):
        RecordingSet.load(tmp_path)
    path.write_text('{"token":"private-sentinel"}')
    with pytest.raises(RecordingRejected) as error:
        RecordingSet.load(tmp_path)
    assert "private-sentinel" not in str(error.value)


@pytest.mark.parametrize(
    "result",
    [
        {"doc_id": "real-private"},
        {"doc_id": 42},
        {"a": [{"b": {"doc_id": "real-private"}}]},
        *[
            {"a": [{key: "private-sentinel"}]}
            for key in ("Authorization", "TOKEN", "bearer", "password", "api_key")
        ],
        {"text": "contains Bearer private-sentinel"},
    ],
)
def test_rejections_safe(result):
    with pytest.raises(RecordingRejected) as error:
        validate_recording(entry(result=result))
    assert "demo-platform-g3" in str(error.value)
    assert "lookup_id" in str(error.value)
    assert "private-sentinel" not in str(error.value)
    assert "real-private" not in str(error.value)


def test_arguments_and_sql_rows():
    with pytest.raises(RecordingRejected):
        validate_recording(entry(arguments={"nested": {"token": "private-sentinel"}}))
    validate_recording(entry())
    validate_recording(
        entry(tool="run_sql", result={"rows": [[{"doc_id": "db-value"}]]})
    )
    with pytest.raises(RecordingRejected):
        validate_recording(
            entry(tool="run_sql", result={"rows": [[{"token": "private-sentinel"}]]})
        )
    with pytest.raises(RecordingRejected):
        validate_recording(entry(tool="run_sql", result={"doc_id": "db-value"}))


def test_invalid_write_preserves_existing(tmp_path):
    write_recordings(tmp_path, [entry()])
    before = (tmp_path / "recordings.jsonl").read_bytes()
    with pytest.raises(RecordingRejected):
        write_recordings(tmp_path, [entry(result={"token": "private-sentinel"})])
    assert (tmp_path / "recordings.jsonl").read_bytes() == before
    with pytest.raises(RecordingRejected):
        write_recordings(tmp_path, [entry(), entry()])


@pytest.mark.parametrize(
    "change", [{"is_error": "false"}, {"arguments": []}, {"result": []}]
)
def test_field_types(tmp_path, change):
    payload = {
        "caller": "demo-platform-g3",
        "tool": "lookup_id",
        "arguments": {},
        "result": {},
        "is_error": False,
        **change,
    }
    (tmp_path / "recordings.jsonl").write_text(json.dumps(payload))
    with pytest.raises(RecordingRejected):
        RecordingSet.load(tmp_path)

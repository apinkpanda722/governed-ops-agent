"""Rollout file contracts; all file access uses temporary directories."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from govops.rollout import store as s
from govops.rollout.policy import CLASSIFY_STATUSES, OFF, RolloutState


def exposure(i=0):
    return {
        "event": "assigned",
        "ts": "2026-10-21T00:00:00Z",
        "conversation_id": f"{i:032x}",
        "user_sha256": f"{i:064x}",
        "bucket": i,
        "variant": "v2",
        "pct_bp": 1000,
        "kill": False,
    }


def classify(i=0):
    return {
        "ts": "2026-10-21T00:00:00Z",
        "turn_id": f"{i:032x}",
        "status": "ok",
        "started_ms": 0.0,
        "ended_ms": 100.0,
    }


def test_missing_files(tmp_path):
    directory = tmp_path / "absent"
    assert s.read_state(directory) == OFF
    assert s.read_exposures(directory) == []
    assert s.read_classify_tail(directory) == []


@pytest.mark.parametrize(
    "payload",
    [
        "{",
        "[]",
        json.dumps({**asdict(OFF), "extra": 0}),
        json.dumps({"pct_bp": 0, "kill": False, "reason": None}),
        *[
            json.dumps({**asdict(OFF), "pct_bp": pct})
            for pct in [-1, 10001, True, 0.0, "0"]
        ],
        json.dumps({**asdict(OFF), "kill": 0}),
        json.dumps({**asdict(OFF), "reason": 1}),
        json.dumps({**asdict(OFF), "rolled_back_at": False}),
    ],
)
def test_invalid_state_fails_safe(tmp_path, payload):
    (tmp_path / "state.json").write_text(payload)
    assert s.read_state(tmp_path) == RolloutState(0, True, None, "state_invalid")


def test_state_io_errors_fail_safe(tmp_path):
    (tmp_path / "state.json").mkdir()
    assert s.read_state(tmp_path) == RolloutState(0, True, None, "state_invalid")


def test_atomic_state_write_and_permissions(tmp_path):
    directory = tmp_path / "rollout"
    state = RolloutState(1000, True, "2026-10-21T00:00:00Z", "manual_kill")
    real_replace = s.os.replace
    with (
        patch.object(s.os, "replace", wraps=real_replace) as replace,
        patch.object(s.os, "fsync", wraps=s.os.fsync) as fsync,
    ):
        s.write_state(directory, state)
        replace.assert_called_once()
        assert Path(replace.call_args.args[0]).parent == directory
        assert replace.call_args.args[1] == directory / "state.json"
        fsync.assert_called_once()
    assert s.read_state(directory) == state
    assert directory.stat().st_mode & 0o777 == 0o700
    assert (directory / "state.json").stat().st_mode & 0o777 == 0o600
    assert {p.name for p in directory.iterdir()} == {"state.json"}
    with (
        patch.object(s.os, "replace", side_effect=OSError("synthetic")),
        pytest.raises(OSError),
    ):
        s.write_state(directory, OFF)
    assert s.read_state(directory) == state
    assert {p.name for p in directory.iterdir()} == {"state.json"}


def test_allowed_events_and_classify_statuses_round_trip(tmp_path):
    directory = tmp_path / "records"
    rows = [
        exposure(),
        {"event": "variant_missing", "ts": "now", "conversation_id": "f" * 32},
        {
            "event": "rollback",
            "ts": "now",
            "reason": "rollback_block_diff",
            "pct_bp": 1000,
        },
    ]
    for row in rows:
        s.append_exposure(directory, row)
    assert s.read_exposures(directory) == rows
    calls = [
        {**classify(i), "status": status}
        for i, status in enumerate(sorted(CLASSIFY_STATUSES))
    ]
    for row in calls:
        s.append_classify(directory, row)
    assert s.read_classify_tail(directory) == calls
    assert directory.stat().st_mode & 0o777 == 0o700
    for name in ("exposures.jsonl", "classify.jsonl"):
        assert (directory / name).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("key", ["request", "answer", "token", "extra"])
@pytest.mark.parametrize("kind", ["exposure", "classify"])
def test_unknown_and_body_keys_are_rejected(tmp_path, key, kind):
    row = exposure() if kind == "exposure" else classify()
    row[key] = "synthetic"
    append = s.append_exposure if kind == "exposure" else s.append_classify
    with pytest.raises(ValueError):
        append(tmp_path, row)


@pytest.mark.parametrize(
    "key,value",
    [
        ("conversation_id", "g" * 32),
        ("conversation_id", "A" * 32),
        ("conversation_id", "0" * 31),
        ("conversation_id", "0" * 32 + "\n"),
        ("user_sha256", "0" * 63),
        ("user_sha256", "z" * 64),
        ("event", "unknown"),
    ],
)
def test_bad_exposure_identifiers_and_event(tmp_path, key, value):
    with pytest.raises(ValueError):
        s.append_exposure(tmp_path, {**exposure(), key: value})


@pytest.mark.parametrize(
    "key,value",
    [("turn_id", "bad"), ("turn_id", "0" * 32 + "\n"), ("status", "unknown")],
)
def test_bad_classify_identifiers_and_status(tmp_path, key, value):
    with pytest.raises(ValueError):
        s.append_classify(tmp_path, {**classify(), key: value})


def test_event_specific_and_required_keys(tmp_path):
    for row in [
        {"event": "variant_missing", "ts": "now"},
        {**exposure(), "reason": "manual_kill"},
        {"event": "rollback", "ts": "now", "reason": "manual_kill"},
    ]:
        with pytest.raises(ValueError):
            s.append_exposure(tmp_path, row)
    row = classify()
    del row["ts"]
    with pytest.raises(ValueError):
        s.append_classify(tmp_path, row)


@pytest.mark.parametrize(
    "name,reader",
    [("exposures.jsonl", s.read_exposures), ("classify.jsonl", s.read_classify_tail)],
)
@pytest.mark.parametrize("line", ["{", "[]", "", '{"answer":"synthetic"}'])
def test_broken_lines_are_not_skipped(tmp_path, name, reader, line):
    (tmp_path / name).write_text(line + "\n")
    with pytest.raises(ValueError):
        reader(tmp_path)


def test_classify_tail_counts(tmp_path):
    rows = [classify(i) for i in range(8)]
    for row in rows:
        s.append_classify(tmp_path, row)
    assert s.read_classify_tail(tmp_path, 3) == rows[-3:]
    assert s.read_classify_tail(tmp_path, 20) == rows
    assert s.read_classify_tail(tmp_path, 0) == []
    with pytest.raises(ValueError):
        s.read_classify_tail(tmp_path, -1)


@pytest.mark.parametrize("value", ["", " \t"])
def test_empty_rollout_dir_uses_default(monkeypatch, value):
    monkeypatch.setenv("GOVOPS_ROLLOUT_DIR", value)
    assert s.rollout_dir() == Path("data/interim/rollout")


def test_custom_rollout_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("GOVOPS_ROLLOUT_DIR", str(tmp_path))
    assert s.rollout_dir() == tmp_path


def test_concurrent_append_has_all_valid_rows(tmp_path):
    directory = tmp_path / "concurrent"
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: s.append_exposure(directory, exposure(i)), range(8)))
    lines = (directory / "exposures.jsonl").read_text().splitlines()
    assert len(lines) == 8
    rows = [json.loads(line) for line in lines]
    assert {json.dumps(row, sort_keys=True) for row in rows} == {
        json.dumps(exposure(i), sort_keys=True) for i in range(8)
    }

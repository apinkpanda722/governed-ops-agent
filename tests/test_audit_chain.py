"""Signed chain, legacy sealing and bounded concurrent append contracts."""

import fcntl
import hashlib
import json
import multiprocessing
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from govops.audit import AuditLog, AuditWriteError, append_with_retry
from govops.audit.crypto import (
    AuditKeyError,
    canonical_json,
    load_public_keys,
    record_hash,
    verify_hash,
)


def emit(log):
    return log.append(
        "side_effect.attempt", task_id="a" * 32, actor_sub="demo-platform-g3"
    )


def rows(directory):
    return [
        json.loads(line)
        for p in sorted(directory.glob("chain-*.jsonl"))
        for line in p.read_text().splitlines()
    ]


def check_chain(directory, count, start=1):
    records = rows(directory)
    assert len(records) == count
    assert [r["seq"] for r in records] == list(range(start, start + count))
    previous = "0" * 64
    keys = load_public_keys()
    for r in records:
        assert r["prev_hash"] == previous
        unsigned = {k: v for k, v in r.items() if k not in {"hash", "sig"}}
        assert r["hash"] == record_hash(unsigned)
        assert verify_hash(keys[r["kid"]], r["hash"], r["sig"])
        assert record_hash({**unsigned, "kid": "different"}) != r["hash"]
        previous = r["hash"]
    for p in directory.glob("chain-*.jsonl"):
        assert p.stat().st_mode & 0o777 == 0o600
        assert p.read_text() == "".join(
            canonical_json(r) + "\n"
            for r in [json.loads(line) for line in p.read_text().splitlines()]
        )
    assert directory.stat().st_mode & 0o777 == 0o700
    return records


def test_signed_genesis_and_mixed_events(tmp_path):
    log = AuditLog(tmp_path)
    assert not list(tmp_path.iterdir())
    assert emit(log) == 2
    log.append("auth.failed", task_id=None, actor_sub="system", reason="invalid_token")
    assert emit(AuditLog(tmp_path)) == 4
    genesis = check_chain(tmp_path, 4)[0]
    assert genesis["event"] == "chain.genesis"
    assert genesis["actor"] == {"sub": "system", "act": "governed-ops-agent"}
    assert genesis["task_id"] is None
    assert [
        genesis[k] for k in ("legacy_files", "legacy_lines", "legacy_last_seq")
    ] == [0, 0, 0]
    assert (
        genesis["legacy_sha256"]
        == hashlib.sha256(canonical_json([]).encode()).hexdigest()
    )


def test_missing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_KEY_FILE", str(tmp_path / "missing.pem"))
    with pytest.raises(AuditKeyError):
        AuditLog(tmp_path)


def test_chain_file_is_private_at_creation(tmp_path, monkeypatch):
    original_open = os.open
    created = []

    def capture_open(path, flags, mode=0o777, **kwargs):
        fd = original_open(path, flags, mode, **kwargs)
        if flags & os.O_CREAT and Path(path).name.startswith("chain-"):
            created.append(os.fstat(fd).st_mode & 0o777)
        return fd

    monkeypatch.setattr(os, "open", capture_open)
    emit(AuditLog(tmp_path))
    assert created == [0o600, 0o600]


def test_legacy_seal(tmp_path):
    legacy = tmp_path / "2026-10-06.jsonl"
    data = b"".join(json.dumps({"seq": i}).encode() + b"\n" for i in range(1, 4))
    legacy.write_bytes(data)
    assert emit(AuditLog(tmp_path)) == 5
    genesis = check_chain(tmp_path, 2, start=4)[0]
    assert [
        genesis[k] for k in ("legacy_files", "legacy_lines", "legacy_last_seq")
    ] == [1, 3, 3]
    assert (
        genesis["legacy_sha256"]
        == hashlib.sha256(
            canonical_json([[legacy.name, hashlib.sha256(data).hexdigest()]]).encode()
        ).hexdigest()
    )
    assert legacy.read_bytes() == data


@pytest.mark.parametrize(
    "data", [b"invalid\n", b'{"seq":true}\n', b'{"seq":"1"}\n', b"{}\n", b"[]\n"]
)
def test_invalid_legacy(tmp_path, data):
    legacy = tmp_path / "2026-10-06.jsonl"
    legacy.write_bytes(data)
    log = AuditLog(tmp_path)
    with pytest.raises(AuditWriteError) as error:
        emit(log)
    assert error.value.code == "audit_legacy_invalid"
    assert not list(tmp_path.glob("chain-*.jsonl"))
    assert legacy.read_bytes() == data


def test_utc_rotation(tmp_path):
    now = [datetime(2026, 10, 9, 23, 59, 59, 900000, tzinfo=UTC)]
    log = AuditLog(tmp_path, clock=lambda: now[0])
    emit(log)
    now[0] += timedelta(milliseconds=200)
    emit(log)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "chain-2026-10-09.jsonl",
        "chain-2026-10-10.jsonl",
    ]
    check_chain(tmp_path, 3)


def test_naive_clock(tmp_path):
    with pytest.raises(AuditWriteError):
        emit(
            AuditLog(
                tmp_path,
                clock=lambda: datetime(2026, 10, 9, tzinfo=UTC).replace(tzinfo=None),
            )
        )
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "tail",
    [
        None,
        b"invalid\n",
        b'{"seq":true,"hash":"' + b"a" * 64 + b'"}\n',
        b'{"seq":2,"hash":"bad"}\n',
        b"[]\n",
        b"null\n",
        b'{"hash":"' + b"a" * 64 + b'"}\n',
        b'{"seq":2}\n',
    ],
)
def test_corrupt_tail_unchanged(tmp_path, tail):
    log = AuditLog(tmp_path)
    emit(log)
    path = next(tmp_path.glob("chain-*.jsonl"))
    data = path.read_bytes()[:-1] if tail is None else tail
    path.write_bytes(data)
    with pytest.raises(AuditWriteError) as error:
        emit(log)
    assert error.value.code == "audit_chain_corrupt"
    assert path.read_bytes() == data


def test_skip_empty_latest(tmp_path):
    log = AuditLog(tmp_path, clock=lambda: datetime(2026, 10, 9, tzinfo=UTC))
    emit(log)
    (tmp_path / "chain-2026-10-10.jsonl").touch(mode=0o600)
    assert emit(log) == 3
    check_chain(tmp_path, 3)


def test_only_empty_chain(tmp_path):
    path = tmp_path / "chain-2026-10-09.jsonl"
    path.touch(mode=0o600)
    emit(AuditLog(tmp_path, clock=lambda: datetime(2026, 10, 9, tzinfo=UTC)))
    check_chain(tmp_path, 2)
    assert list(tmp_path.iterdir()) == [path]


def _writer(directory, count, queue):
    try:
        log = AuditLog(Path(directory))
        for _ in range(count):
            emit(log)
        queue.put("ok")
    except (AuditWriteError, AuditKeyError, OSError) as error:
        queue.put(type(error).__name__)


def _timeout_writer(directory, released, queue):
    log = AuditLog(Path(directory), lock_timeout=0.3)
    try:
        emit(log)
        queue.put("unexpected success")
    except AuditWriteError as error:
        queue.put(error.code)
    if not released.wait(timeout=10):
        queue.put("release timeout")
        return
    queue.put(emit(log))


def cleanup(processes):
    for process in processes:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        assert not process.is_alive()


def test_process_lock_timeout_then_success(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue, released = ctx.Queue(), ctx.Event()
    fd = os.open(tmp_path, os.O_RDONLY)
    process = ctx.Process(target=_timeout_writer, args=(str(tmp_path), released, queue))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        process.start()
        assert queue.get(timeout=15) == "audit_lock_timeout"
        assert not list(tmp_path.glob("chain-*.jsonl"))
        fcntl.flock(fd, fcntl.LOCK_UN)
        released.set()
        assert queue.get(timeout=15) == 2
        process.join(timeout=10)
        assert process.exitcode == 0
        check_chain(tmp_path, 2)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        if process.pid is not None:
            cleanup([process])
        queue.close()


def test_process_concurrent_chain(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_writer, args=(str(tmp_path), 20, queue)) for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        assert [queue.get(timeout=30) for _ in processes] == ["ok", "ok"]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        check_chain(tmp_path, 41)
    finally:
        cleanup([p for p in processes if p.pid is not None])
        queue.close()


def test_thread_concurrent_chain(tmp_path):
    logs = [AuditLog(tmp_path), AuditLog(tmp_path)]
    errors = []

    def write(index):
        try:
            for _ in range(20):
                emit(logs[index % 2])
        except (AuditWriteError, AuditKeyError, OSError) as error:
            errors.append(error)

    threads = [threading.Thread(target=write, args=(i,), daemon=True) for i in range(8)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert all(not thread.is_alive() for thread in threads)
        assert not errors
        check_chain(tmp_path, 161)
    finally:
        for thread in threads:
            thread.join(timeout=1)


@pytest.mark.parametrize(
    "code",
    [
        "audit_write_failed",
        "audit_lock_timeout",
        "audit_chain_corrupt",
        "audit_legacy_invalid",
    ],
)
def test_retry_all_codes(code):
    sink = Mock()
    sink.append.side_effect = AuditWriteError("failed", code=code)
    assert not append_with_retry(sink, "side_effect.result")
    assert sink.append.call_count == 3


@pytest.mark.parametrize(
    "fields", [{"body": "raw"}, {"task_id": "bad"}, {"actor_sub": "bad"}]
)
def test_validation_before_lock(tmp_path, fields, monkeypatch):
    log = AuditLog(tmp_path)
    monkeypatch.setattr(fcntl, "flock", Mock(side_effect=AssertionError("lock called")))
    kwargs = {"task_id": "a" * 32, "actor_sub": "demo-platform-g3", **fields}
    with pytest.raises(AuditWriteError):
        log.append("side_effect.attempt", **kwargs)
    assert not list(tmp_path.iterdir())


def test_fsync_failure_releases_locks(tmp_path, monkeypatch):
    log = AuditLog(tmp_path)
    emit(log)
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", Mock(side_effect=OSError("synthetic failure")))
        with pytest.raises(AuditWriteError) as error:
            emit(log)
        assert error.value.code == "audit_write_failed"
        assert "synthetic failure" not in str(error.value)
    # The flushed record remains append-only even when durability fails.
    assert emit(AuditLog(tmp_path, lock_timeout=0.3)) == 4
    check_chain(tmp_path, 4)

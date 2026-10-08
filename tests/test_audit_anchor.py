"""Anchors use isolated signed chains and pending evidence only."""

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from govops.audit import AuditLog
from govops.audit.anchor import AuditAnchorError, main
from govops.audit.crypto import canonical_json
from govops.audit.log import AuditWriteError
from govops.audit.verify import main as verify_main
from govops.audit.verify import read_anchor_lines, verify_chain


def records(directory):
    return [
        json.loads(line)
        for p in sorted(directory.glob("chain-*.jsonl"))
        for line in p.read_text().splitlines()
    ]


def emit(log):
    log.append("auth.failed", task_id=None, actor_sub="system", reason="invalid_token")


def worker(root, pending, environment, ready, start):
    os.environ.update(environment)
    log = AuditLog(Path(root) / "audit")
    ready.put(True)
    if not start.wait(timeout=20):
        raise RuntimeError("start timeout")
    log.anchor("parallel", repo_root=Path(root), pending_file=Path(pending))


def test_normal_and_repeat(tmp_path):
    log = AuditLog(tmp_path / "nested/audit")
    pending = tmp_path / "pending/anchors.jsonl"
    row = log.anchor("m4-audit-first", repo_root=tmp_path, pending_file=pending)
    assert pending.read_text() == canonical_json(row) + "\n"
    assert pending.stat().st_mode & 0o777 == 0o600
    assert pending.parent.stat().st_mode & 0o777 == 0o700
    chain = records(log.directory)
    assert [r["event"] for r in chain] == ["chain.genesis", "chain.anchor"]
    assert row == {
        "dir": "nested/audit",
        **{k: chain[-1][k] for k in ("seq", "hash", "sig", "kid", "label", "ts")},
    }
    report = verify_chain(
        log.directory, repo_root=tmp_path, anchors=read_anchor_lines([pending])
    )
    assert report.ok and report.anchors_checked == 1
    second = log.anchor("second", repo_root=tmp_path, pending_file=pending)
    assert second["seq"] != row["seq"]
    assert len(read_anchor_lines([pending])) == 2


@pytest.mark.parametrize("label", ["Upper", "", "a" * 41, "-start", "a\n"])
def test_invalid_label(tmp_path, label):
    log = AuditLog(tmp_path / "audit")
    pending = tmp_path / "pending"
    emit(log)
    before = records(log.directory)
    with pytest.raises(AuditAnchorError) as error:
        log.anchor(label, repo_root=tmp_path, pending_file=pending)
    assert str(error.value) == error.value.code == "audit_anchor_label_invalid"
    assert records(log.directory) == before
    assert not pending.exists()


def test_outside_and_pending_failure(tmp_path):
    log = AuditLog(tmp_path / "audit")
    pending = tmp_path / "pending"
    with pytest.raises(AuditAnchorError, match="audit_anchor_dir_outside_repo"):
        log.anchor("x", repo_root=tmp_path / "repo", pending_file=pending)
    assert not records(log.directory) and not pending.exists()
    pending.touch()
    with pytest.raises(AuditAnchorError, match="audit_anchor_write_failed"):
        log.anchor("x", repo_root=tmp_path, pending_file=pending / "anchors")
    assert records(log.directory)[-1]["event"] == "chain.anchor"


def test_chain_failure_preserves_pending(tmp_path, monkeypatch):
    log = AuditLog(tmp_path / "audit")
    pending = tmp_path / "pending"
    log.anchor("first", repo_root=tmp_path, pending_file=pending)
    before = pending.read_bytes()
    failure = AuditWriteError("", code="audit_lock_timeout")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(log, "_append_record", fail)
    with pytest.raises(AuditWriteError) as error:
        log.anchor("second", repo_root=tmp_path, pending_file=pending)
    assert error.value is failure
    assert pending.read_bytes() == before


def test_invalid_label_preserves_existing_pending(tmp_path):
    log = AuditLog(tmp_path / "audit")
    pending = tmp_path / "pending"
    log.anchor("first", repo_root=tmp_path, pending_file=pending)
    before = pending.read_bytes(), records(log.directory)
    with pytest.raises(AuditAnchorError, match="audit_anchor_label_invalid"):
        log.anchor("INVALID", repo_root=tmp_path, pending_file=pending)
    assert (pending.read_bytes(), records(log.directory)) == before


def test_symlink_outside_repo_rejected_before_append(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "audit").symlink_to(outside, target_is_directory=True)
    log = AuditLog(root / "audit")
    pending = tmp_path / "pending"
    with pytest.raises(AuditAnchorError, match="audit_anchor_dir_outside_repo"):
        log.anchor("x", repo_root=root, pending_file=pending)
    assert not list(outside.iterdir()) and not pending.exists()


def test_scene_five(tmp_path, capsys):
    log = AuditLog(tmp_path / "audit")
    pending = tmp_path / "pending"
    for _ in range(3):
        emit(log)
    row = log.anchor("scene-five", repo_root=tmp_path, pending_file=pending)
    for _ in range(3):
        emit(log)
    chain = records(log.directory)
    path = next(log.directory.glob("chain-*.jsonl"))
    # A tail after the most recent anchor has no independent evidence yet.
    path.write_text(
        "".join(canonical_json(r) + "\n" for r in chain if r["seq"] <= row["seq"])
    )
    assert verify_chain(
        log.directory, repo_root=tmp_path, anchors=read_anchor_lines([pending])
    ).ok
    path.write_text(
        "".join(canonical_json(r) + "\n" for r in chain if r["seq"] < row["seq"])
    )
    report = verify_chain(
        log.directory, repo_root=tmp_path, anchors=read_anchor_lines([pending])
    )
    assert "tail_truncated" in {p.code for p in report.problems}
    assert (
        verify_main(
            [
                "--dir",
                str(log.directory),
                "--repo-root",
                str(tmp_path),
                "--anchors",
                str(pending),
            ]
        )
        == 1
    )
    assert "FAIL" in capsys.readouterr().out


def test_parallel(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, start = ctx.Queue(), ctx.Event()
    pending = tmp_path / "pending"
    environment = {
        k: os.environ[k] for k in ("AUDIT_KEY_FILE", "AUDIT_PUBLIC_KEY_FILE")
    }
    processes = [
        ctx.Process(
            target=worker, args=(str(tmp_path), str(pending), environment, ready, start)
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        for _ in processes:
            assert ready.get(timeout=20)
        start.set()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
        rows = read_anchor_lines([pending])
        assert len(rows) == 2 and rows[0]["seq"] != rows[1]["seq"]
        assert verify_chain(tmp_path / "audit", repo_root=tmp_path, anchors=rows).ok
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        ready.close()


def test_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))
    monkeypatch.setenv("AUDIT_ANCHORS_PENDING_FILE", str(tmp_path / "pending"))
    assert main(["--label", "x", "--repo-root", str(tmp_path)]) == 0
    output = capsys.readouterr()
    assert output.out == (tmp_path / "pending").read_text() and output.err == ""
    assert main(["--label", "x"]) == 1
    assert capsys.readouterr().err == "FAIL audit_anchor_dir_outside_repo\n"
    assert main(["--label", "INVALID"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "FAIL audit_anchor_label_invalid\n"
    monkeypatch.setenv("AUDIT_KEY_FILE", str(tmp_path / "missing"))
    assert main(["--label", "x", "--repo-root", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "FAIL audit_key_missing\n"

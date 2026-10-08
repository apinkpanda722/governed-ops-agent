"""Offline smoke checks use isolated keys and audit artifacts only."""

import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest
from audit_keys import make_key_pair

from govops.audit import smoke
from govops.audit.log import AuditLog

STAGES = [
    "chain_basic",
    "tamper_modify",
    "tamper_delete_middle",
    "tamper_reorder",
    "tamper_tail_truncate",
    "tamper_other_key",
    "scene5_tail_cut",
]


def snapshot(directory):
    return {
        str(p.relative_to(directory)): (
            p.stat().st_size,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in directory.rglob("*")
        if p.is_file()
    }


def test_normal_and_no_chain(capsys):
    assert smoke.main() == 0
    out = capsys.readouterr().out
    for stage in ["key", *STAGES]:
        assert f"PASS {stage}\n" in out
    assert "INFO real_chain=none (first write creates genesis)" in out
    assert "INFO known_limit=tail_after_last_anchor_not_detected" in out
    assert re.search(r"INFO kid=[0-9a-f]{12}\n", out)
    assert "INFO key_mode=0600\nINFO key_pair=match\n" in out
    assert out.endswith("RESULT PASS\n")
    assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}|BEGIN .*KEY", out)
    assert os.environ["AUDIT_KEY_FILE"] not in out


@pytest.mark.parametrize(
    "failure,code",
    [
        ("missing", "audit_key_missing"),
        ("mode", "audit_key_permissions"),
        ("pair", "audit_key_mismatch"),
    ],
)
def test_key_failure(failure, code, tmp_path, capsys):
    private = Path(os.environ["AUDIT_KEY_FILE"])
    if failure == "missing":
        private.unlink()
    elif failure == "mode":
        private.chmod(0o644)
    else:
        Path(os.environ["AUDIT_PUBLIC_KEY_FILE"]).write_bytes(
            make_key_pair(tmp_path).public_path.read_bytes()
        )
    assert smoke.main() == 1
    assert capsys.readouterr().out == f"FAIL key {code}\nRESULT FAIL\n"


@pytest.mark.parametrize(
    "kind,codes",
    [
        ("modify", {"hash_mismatch"}),
        ("delete_middle", {"seq_gap"}),
        ("reorder", {"seq_order", "prev_hash_mismatch"}),
        ("tail_truncate", {"tail_truncated"}),
        ("other_key", {"kid_unknown"}),
    ],
)
def test_independent_detection(kind, codes, monkeypatch, capsys):
    verify = smoke.verify_chain

    def hidden(directory, **kwargs):
        report = verify(directory, **kwargs)
        if Path(directory).name == kind:
            return replace(
                report,
                problems=tuple(p for p in report.problems if p.code not in codes),
            )
        return report

    monkeypatch.setattr(smoke, "verify_chain", hidden)
    assert smoke.main() == 1
    out = capsys.readouterr().out
    assert f"FAIL tamper_{kind} not_detected\n" in out
    assert sum(line.startswith("FAIL ") for line in out.splitlines()) == 1
    assert out.endswith("RESULT FAIL\n")
    for stage in STAGES:
        if stage != f"tamper_{kind}":
            assert f"PASS {stage}\n" in out


@pytest.mark.parametrize("tampered", [False, True])
def test_real_chain_read_only(tampered, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(smoke, "ROOT", tmp_path)
    directory = Path(os.environ["AUDIT_DIR"])
    log = AuditLog(directory)
    log.append("auth.failed", task_id=None, actor_sub="system", reason="invalid_token")
    pending = Path(os.environ["AUDIT_ANCHORS_PENDING_FILE"])
    log.anchor("real-test", repo_root=directory.parent, pending_file=pending)
    monkeypatch.setattr(smoke, "ROOT", directory.parent)
    if tampered:
        path = next(directory.glob("chain-*.jsonl"))
        rows = path.read_text().splitlines()
        row = json.loads(rows[1])
        row["reason"] = "unknown_user"
        rows[1] = json.dumps(row)
        path.write_text("\n".join(rows) + "\n")
    before = snapshot(directory.parent)
    assert smoke.main() == int(tampered)
    out = capsys.readouterr().out
    assert ("FAIL real_chain hash_mismatch" if tampered else "PASS real_chain") in out
    if not tampered:
        assert "INFO records=3 anchors=1" in out
    assert snapshot(directory.parent) == before


def test_legacy_count(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "audit"
    directory.mkdir()
    monkeypatch.setenv("AUDIT_DIR", str(directory))
    (directory / "2026-01-01.jsonl").write_bytes(b"not-json\nsecond\n")
    before = snapshot(directory)
    assert smoke.main() == 0
    assert (
        "INFO legacy_unverified files=1 lines=2 (체인 없음, 미검증)"
        in capsys.readouterr().out
    )
    assert snapshot(directory) == before


def test_scene_requires_pending_anchor_count(monkeypatch, capsys):
    verify = smoke.verify_chain

    def unchecked(directory, **kwargs):
        report = verify(directory, **kwargs)
        return (
            replace(report, anchors_checked=0)
            if Path(directory).name == "scene5"
            else report
        )

    monkeypatch.setattr(smoke, "verify_chain", unchecked)
    assert smoke.main() == 1
    out = capsys.readouterr().out
    assert "FAIL scene5_tail_cut not_detected\n" in out
    for stage in STAGES[:-1]:
        assert f"PASS {stage}\n" in out


def test_invalid_real_anchor_is_safe_and_read_only(monkeypatch, capsys):
    directory = Path(os.environ["AUDIT_DIR"])
    log = AuditLog(directory)
    log.append("auth.failed", task_id=None, actor_sub="system", reason="invalid_token")
    pending = Path(os.environ["AUDIT_ANCHORS_PENDING_FILE"])
    pending.write_text("invalid-anchor")
    before = snapshot(directory.parent)
    assert smoke.main() == 1
    assert "FAIL real_chain anchor_file_invalid\n" in capsys.readouterr().out
    assert snapshot(directory.parent) == before

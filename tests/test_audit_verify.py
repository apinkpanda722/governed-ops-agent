"""Offline verification of real signed chains and independent tamper signals."""

import json
import multiprocessing
import os
import re
from datetime import UTC, datetime

import pytest
from audit_keys import make_key_pair

from govops.audit import AuditLog
from govops.audit.crypto import canonical_json, load_signing_key, record_hash
from govops.audit.verify import (
    main,
    read_anchor_lines,
    verify_anchor_dirs,
    verify_chain,
)


def emit(log):
    log.append("side_effect.attempt", task_id="a" * 32, actor_sub="demo-platform-g3")


def rows(directory):
    return [
        json.loads(line)
        for p in sorted(directory.glob("chain-*.jsonl"))
        for line in p.read_text().splitlines()
    ]


def write_rows(directory, records):
    paths = sorted(directory.glob("chain-*.jsonl"))
    paths[0].write_text("".join(canonical_json(r) + "\n" for r in records))
    for path in paths[1:]:
        path.unlink()


def chain(directory):
    log = AuditLog(directory)
    emit(log)
    log.append("chain.anchor", task_id=None, actor_sub="system", label="test")
    emit(log)
    return rows(directory)


def anchor(root, directory, record):
    return {
        "dir": directory.relative_to(root).as_posix(),
        **{k: record[k] for k in ("seq", "hash", "sig", "kid", "label", "ts")},
    }


def codes(report):
    return {p.code for p in report.problems}


def cli(root, directory, *extra):
    return main(
        [
            "--repo-root",
            str(root),
            "--dir",
            str(directory),
            "--anchors",
            str(root / "absent"),
            *extra,
        ]
    )


def test_normal_dates_and_output(tmp_path, capsys):
    directory = tmp_path / "audit"
    clock = iter(
        [datetime(2026, 1, 1, 23, 59, tzinfo=UTC), datetime(2026, 1, 2, tzinfo=UTC)]
    )
    log = AuditLog(directory, clock=lambda: next(clock))
    emit(log)
    log.append("auth.failed", task_id=None, actor_sub="system", reason="invalid_token")
    report = verify_chain(directory, repo_root=tmp_path)
    assert report.ok and report.problems == () and report.records == 3
    assert len(list(directory.glob("chain-*.jsonl"))) == 2
    before = {p: p.read_bytes() for p in directory.iterdir()}
    assert cli(tmp_path, directory) == 0
    output = capsys.readouterr().out
    assert "PASS chain dir=audit" in output and "anchors=0" in output
    assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}", output)
    assert "invalid_token" not in output and "demo-platform" not in output
    assert {p: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize(
    "tamper,expected",
    [
        ("field", "hash_mismatch"),
        ("rehash", "sig_invalid"),
        ("delete", "seq_gap"),
        ("swap", "seq_order"),
    ],
)
def test_independent_tampering(tmp_path, tamper, expected):
    records = chain(tmp_path)
    if tamper in {"field", "rehash"}:
        records[1]["task_id"] = "b" * 32
        if tamper == "rehash":
            records[1]["hash"] = record_hash(
                {k: v for k, v in records[1].items() if k not in {"hash", "sig"}}
            )
    elif tamper == "delete":
        del records[1]
    else:
        records[1], records[2] = records[2], records[1]
    write_rows(tmp_path, records)
    report = verify_chain(tmp_path)
    assert expected in codes(report)
    assert "prev_hash_mismatch" in codes(report)
    if tamper == "field":
        assert any(p.code == "hash_mismatch" and p.seq == 2 for p in report.problems)


def test_tail_limits(tmp_path, capsys):
    directory = tmp_path / "audit"
    records = chain(directory)
    fixed = anchor(tmp_path, directory, records[2])
    write_rows(directory, records[:2])
    assert "tail_truncated" in codes(
        verify_chain(directory, anchors=[fixed], repo_root=tmp_path)
    )
    assert verify_chain(directory).ok  # 한계: 고정점 없이는 꼬리 삭제를 탐지 못 함
    assert cli(tmp_path, directory) == 0
    assert "anchors=0" in capsys.readouterr().out
    write_rows(directory, records[:3])
    # 한계: 다음 고정점 전까지 탐지 못 함
    assert verify_chain(directory, anchors=[fixed], repo_root=tmp_path).ok


def resign(records, key, replace_kid=False):
    previous = "0" * 64
    for record in records:
        if replace_kid:
            record["kid"] = key.kid
        record["prev_hash"] = previous
        record["hash"] = record_hash(
            {k: v for k, v in record.items() if k not in {"hash", "sig"}}
        )
        record["sig"] = key.sign(record["hash"])
        previous = record["hash"]


@pytest.mark.parametrize(
    "mode,expected",
    [("kid", "kid_unknown"), ("sig", "sig_invalid"), ("same", "anchor_hash_mismatch")],
)
def test_resigning(tmp_path, mode, expected):
    directory = tmp_path / "audit"
    records = chain(directory)
    fixed = anchor(tmp_path, directory, records[2])
    key = load_signing_key()
    if mode != "same":
        other = make_key_pair(tmp_path / "other")
        key = load_signing_key(other.private_path, other.public_path)
    records[1]["task_id"] = "b" * 32
    resign(records, key, replace_kid=mode == "kid")
    write_rows(directory, records)
    report = verify_chain(directory, anchors=[fixed], repo_root=tmp_path)
    assert expected in codes(report)
    if mode == "same":
        # 한계: 같은 키로 전체 재서명은 고정점 없이는 탐지 못 함
        assert verify_chain(directory).ok


def test_anchor_dedup_and_entire_deletion(tmp_path, capsys):
    directory = tmp_path / "audit"
    records = chain(directory)
    fixed = anchor(tmp_path, directory, records[2])
    files = [tmp_path / "one", tmp_path / "two"]
    for file in files:
        file.write_text(canonical_json(fixed) + "\n")
    anchors = read_anchor_lines(files)
    assert anchors == [fixed]
    assert (
        verify_chain(directory, anchors=anchors * 2, repo_root=tmp_path).anchors_checked
        == 1
    )
    assert verify_anchor_dirs(anchors, repo_root=tmp_path, verified=[]) == []
    assert (
        verify_chain(
            directory, anchors=[{**fixed, "dir": "elsewhere"}], repo_root=tmp_path
        ).anchors_checked
        == 0
    )
    for path in directory.glob("chain-*.jsonl"):
        path.unlink()
    other = tmp_path / "other"
    chain(other)
    assert cli(tmp_path, other, "--anchors", str(files[0]), "--scan", "missing") == 1
    assert "FAIL anchor dir=audit code=anchor_dir_missing" in capsys.readouterr().out
    assert "no_chain" in codes(verify_chain(directory))


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("newline", "unterminated_tail"),
        ("genesis", "genesis_missing"),
        ("event", "genesis_missing"),
        ("json", "invalid_json"),
        ("field", "invalid_record"),
    ],
)
def test_structural_errors(tmp_path, damage, expected):
    records = chain(tmp_path)
    if damage == "genesis":
        records.pop(0)
    elif damage == "event":
        records[0]["event"] = "chain.anchor"
    elif damage == "field":
        del records[1]["kid"]
    write_rows(tmp_path, records)
    path = next(tmp_path.glob("chain-*.jsonl"))
    if damage == "newline":
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
    elif damage == "json":
        path.write_text(path.read_text() + "not-json\n")
    assert expected in codes(verify_chain(tmp_path))


@pytest.mark.parametrize("damage", [None, "add", "modify", "delete"])
def test_legacy_seal(tmp_path, damage):
    legacy = tmp_path / "2026-01-01.jsonl"
    legacy.write_text('{"seq":7}\n{"seq":8}\n')
    chain(tmp_path)
    report = verify_chain(tmp_path)
    assert report.ok and (report.legacy_files, report.legacy_lines) == (1, 2)
    if damage == "add":
        (tmp_path / "2026-01-02.jsonl").write_text('{"seq":9}\n')
    elif damage == "modify":
        legacy.write_text('{"seq":6}\n{"seq":8}\n')
    elif damage == "delete":
        legacy.unlink()
    if damage:
        assert "legacy_sha256_mismatch" in codes(verify_chain(tmp_path))


@pytest.mark.parametrize(
    "field,value",
    [
        ("seq", True),
        ("hash", "bad"),
        ("sig", "bad"),
        ("kid", "bad"),
        ("label", "BAD"),
        ("dir", "../escape"),
        ("ts", None),
    ],
)
def test_invalid_anchor_fields(tmp_path, capsys, field, value):
    directory = tmp_path / "audit"
    records = chain(directory)
    fixed = anchor(tmp_path, directory, records[2])
    file = tmp_path / "anchors"
    file.write_text(canonical_json({**fixed, field: value}) + "\n")
    with pytest.raises(ValueError, match="^anchor_file_invalid$"):
        read_anchor_lines([file])
    assert cli(tmp_path, directory, "--anchors", str(file)) == 1
    assert "anchor_file_invalid" in capsys.readouterr().out
    file.write_text("not-json\n")
    assert cli(tmp_path, directory, "--anchors", str(file)) == 1


def test_scan(tmp_path, capsys):
    good = tmp_path / "results/good/audit"
    bad = tmp_path / "results/bad/audit"
    legacy = tmp_path / "results/old/audit"
    chain(good)
    records = chain(bad)
    records[1]["task_id"] = "b" * 32
    write_rows(bad, records)
    legacy.mkdir(parents=True)
    (legacy / "2026-01-01.jsonl").write_text("legacy\nlegacy\n")
    (legacy / "unrelated.jsonl").write_text("ignored\n")
    assert cli(tmp_path, good, "--scan", "results") == 1
    output = capsys.readouterr().out
    assert "PASS chain dir=results/good/audit" in output
    assert "FAIL chain dir=results/bad/audit" in output
    assert "INFO legacy_unverified dirs=1 files=1 lines=2" in output
    assert "chains_pass=1 chains_fail=1 legacy_unverified_files=1" in output
    assert cli(tmp_path, good, "--scan", "absent") == 0


def worker(directory, private, public):
    os.environ["AUDIT_KEY_FILE"] = private
    os.environ["AUDIT_PUBLIC_KEY_FILE"] = public
    log = AuditLog(directory)
    for _ in range(10):
        emit(log)


def test_process_chain(tmp_path):
    context = multiprocessing.get_context("spawn")
    processes = [
        context.Process(
            target=worker,
            args=(
                tmp_path,
                os.environ["AUDIT_KEY_FILE"],
                os.environ["AUDIT_PUBLIC_KEY_FILE"],
            ),
        )
        for _ in range(2)
    ]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=20)
            assert process.exitcode == 0
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
    report = verify_chain(tmp_path)
    assert report.ok and report.records == 21


def test_outside_repo(tmp_path, capsys):
    directory = tmp_path / "outside"
    root = tmp_path / "repo"
    root.mkdir()
    records = chain(directory)
    fixed = {
        "dir": "../outside",
        **{k: records[2][k] for k in ("seq", "hash", "sig", "kid", "label", "ts")},
    }
    report = verify_chain(directory, anchors=[fixed], repo_root=root)
    assert report.ok and report.anchors_checked == 0
    assert cli(root, directory) == 0
    output = capsys.readouterr().out
    assert "dir=<outside-repo>" in output and str(tmp_path) not in output


def test_selected_deleted_anchor_directory(tmp_path, capsys):
    directory = tmp_path / "audit"
    records = chain(directory)
    file = tmp_path / "anchors"
    file.write_text(canonical_json(anchor(tmp_path, directory, records[2])) + "\n")
    for path in directory.glob("chain-*.jsonl"):
        path.unlink()
    assert cli(tmp_path, directory, "--anchors", str(file)) == 1
    output = capsys.readouterr().out
    assert "code=no_chain" in output
    assert "FAIL anchor dir=audit code=anchor_dir_missing" in output


def test_anchor_missing_and_bad_signature(tmp_path):
    directory = tmp_path / "audit"
    records = chain(directory)
    fixed = anchor(tmp_path, directory, records[2])
    bad_signature = {**fixed, "sig": "0" * 128}
    report = verify_chain(directory, anchors=[bad_signature], repo_root=tmp_path)
    assert {"anchor_hash_mismatch", "anchor_sig_invalid"} <= codes(report)
    del records[2]
    write_rows(directory, records)
    assert "anchor_missing" in codes(
        verify_chain(directory, anchors=[fixed], repo_root=tmp_path)
    )


def test_legacy_metadata_tampering(tmp_path):
    records = chain(tmp_path)
    records[0]["legacy_lines"] = 1
    write_rows(tmp_path, records)
    assert "legacy_sha256_mismatch" in codes(verify_chain(tmp_path))


def test_valid_signature_of_wrong_event_is_not_an_anchor(tmp_path):
    directory = tmp_path / "audit"
    records = chain(directory)
    fixed = anchor(tmp_path, directory, records[2])
    records[2]["event"] = "auth.failed"
    del records[2]["label"]
    records[2]["reason"] = "invalid_token"
    resign(records, load_signing_key())
    fixed.update({k: records[2][k] for k in ("hash", "sig", "kid")})
    write_rows(directory, records)
    assert "anchor_hash_mismatch" in codes(
        verify_chain(directory, anchors=[fixed], repo_root=tmp_path)
    )

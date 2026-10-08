"""Audit fixtures isolate writes and guard repository artifacts."""

import os
from pathlib import Path

import pytest
from conftest import assert_audit_artifacts_unchanged, audit_artifact_snapshot

from govops.audit.crypto import load_signing_key

ROOT = Path(__file__).resolve().parent.parent
_PREVIOUS = set()


@pytest.mark.parametrize("iteration", range(2))
def test_isolated_environment(iteration, audit_test_directory):
    paths = {
        name: Path(os.environ[name]).resolve()
        for name in (
            "AUDIT_DIR",
            "AUDIT_KEY_FILE",
            "AUDIT_PUBLIC_KEY_FILE",
            "AUDIT_ANCHORS_PENDING_FILE",
        )
    }
    assert all(not path.is_relative_to(ROOT) for path in paths.values())
    assert all(path.parent == audit_test_directory for path in paths.values())
    assert paths["AUDIT_DIR"].parent == audit_test_directory
    assert paths["AUDIT_ANCHORS_PENDING_FILE"].parent == audit_test_directory
    current = set(paths.values())
    assert current.isdisjoint(_PREVIOUS)
    _PREVIOUS.update(current)
    assert load_signing_key().kid
    assert paths["AUDIT_KEY_FILE"].stat().st_mode & 0o777 == 0o600
    assert paths["AUDIT_PUBLIC_KEY_FILE"].stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("change", ["add", "resize", "delete", "mtime", "anchor"])
def test_snapshot_detects_changes(tmp_path, change):
    directory = tmp_path / "data/interim/audit"
    directory.mkdir(parents=True)
    source = directory / "synthetic.jsonl"
    source.write_text("{}\n")
    before = audit_artifact_snapshot(tmp_path)
    if change == "add":
        (directory / "added.jsonl").write_text("{}\n")
    elif change == "resize":
        source.write_text("{}\n{}\n")
    elif change == "delete":
        source.unlink()
    elif change == "mtime":
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1))
    else:
        anchor = tmp_path / "docs/results/audit-anchors.jsonl"
        anchor.parent.mkdir(parents=True)
        anchor.write_text("{}\n")
    with pytest.raises(AssertionError, match="Audit artifacts changed"):
        assert_audit_artifacts_unchanged(before, audit_artifact_snapshot(tmp_path))


def test_snapshot_unchanged_and_missing(tmp_path):
    before = audit_artifact_snapshot(tmp_path)
    assert before == {
        "data/interim/audit": None,
        "docs/results/audit-anchors.jsonl": None,
    }
    assert_audit_artifacts_unchanged(before, audit_artifact_snapshot(tmp_path))


def test_fixture_paths_differ_between_invocations(tmp_path, audit_key_pair):
    from conftest import isolated_audit_environment

    snapshots = []
    for number in range(2):
        directory = tmp_path / str(number)
        directory.mkdir()
        with pytest.MonkeyPatch.context() as patch:
            isolated_audit_environment.__wrapped__(directory, patch, audit_key_pair)
            snapshots.append(
                {
                    os.environ[name]
                    for name in (
                        "AUDIT_DIR",
                        "AUDIT_KEY_FILE",
                        "AUDIT_PUBLIC_KEY_FILE",
                        "AUDIT_ANCHORS_PENDING_FILE",
                    )
                }
            )
            assert load_signing_key().kid == audit_key_pair.kid
    assert len(snapshots[0]) == len(snapshots[1]) == 4
    assert snapshots[0].isdisjoint(snapshots[1])

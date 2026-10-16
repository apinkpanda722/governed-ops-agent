"""All gates run against temporary repositories and synthetic documents."""

import hashlib
import json
import subprocess

import pytest

from govops.grounding import registration as reg


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, check=True, timeout=30
    ).stdout


def repository(root):
    git(root, "init")
    git(root, "config", "user.name", "Synthetic Reviewer")
    git(root, "config", "user.email", "synthetic@example.invalid")
    for name in reg.HYPOTHESES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Synthetic registration\n")
    (root / "scripts").mkdir()
    (root / "scripts/exempt_paths.json").write_text(
        '{"ledger_paths":["data/manifest/costs.jsonl"]}'
    )
    commit(root)


def test_committed_history_cannot_rewrite_old_entries(tmp_path):
    repository(tmp_path)
    path = tmp_path / reg.REGISTRATION_PATH
    reg.append_registration(path, {"evidence_max_chars": 80000}, repo_root=tmp_path)
    commit(tmp_path)
    raw = json.loads(path.read_text())
    raw["entries"][0]["evidence_max_chars"] = 70000
    path.write_text(json.dumps(raw))
    commit(tmp_path)
    with pytest.raises(reg.RegistrationError, match="registration_history_modified"):
        reg.require_registration(path, repo_root=tmp_path)
    with pytest.raises(reg.RegistrationError, match="registration_history_modified"):
        reg.append_registration(
            path, {"calibration_labels_sha256": "a" * 64}, repo_root=tmp_path
        )


def test_splitter_mismatch_is_refused_after_commit(tmp_path):
    repository(tmp_path)
    path = tmp_path / reg.REGISTRATION_PATH
    reg.append_registration(path, {"evidence_max_chars": 80000}, repo_root=tmp_path)
    raw = json.loads(path.read_text())
    raw["entries"][0]["splitter_rules_sha256"] = "f" * 64
    path.write_text(json.dumps(raw))
    commit(tmp_path)
    with pytest.raises(reg.RegistrationError, match="splitter_rules_changed"):
        reg.require_registration(path, repo_root=tmp_path)


def commit(root):
    git(root, "add", ".")
    git(root, "commit", "-m", "test: synthetic registration")


def test_hypotheses_not_committed_changed_and_reverted(tmp_path):
    with pytest.raises(reg.RegistrationError, match="hypotheses_not_committed"):
        reg.require_hypotheses(tmp_path)
    repository(tmp_path)
    reg.require_hypotheses(tmp_path)
    path = tmp_path / reg.HYPOTHESES[0]
    path.write_text("changed\n")
    with pytest.raises(reg.RegistrationError, match="hypotheses_not_committed"):
        reg.require_hypotheses(tmp_path)
    commit(tmp_path)
    path.write_text("Synthetic registration\n")
    commit(tmp_path)
    with pytest.raises(reg.RegistrationError, match="hypotheses_not_committed"):
        reg.require_hypotheses(tmp_path)


def test_append_only_committed_gates_hash_and_second_grade(tmp_path):
    repository(tmp_path)
    path = tmp_path / reg.REGISTRATION_PATH
    labels = tmp_path / "labels.csv"
    labels.write_text("synthetic labels")
    digest = hashlib.sha256(labels.read_bytes()).hexdigest()
    with pytest.raises(reg.RegistrationError):
        reg.require_calibration(path, labels, repo_root=tmp_path)
    reg.append_registration(path, {"evidence_max_chars": 80000}, repo_root=tmp_path)
    reg.append_registration(
        path, {"calibration_labels_sha256": digest}, repo_root=tmp_path
    )
    with pytest.raises(reg.RegistrationError):
        reg.require_calibration(path, labels, repo_root=tmp_path)
    commit(tmp_path)
    reg.require_calibration(path, labels, repo_root=tmp_path)
    with pytest.raises(reg.RegistrationError):
        reg.require_adopted(path, repo_root=tmp_path)
    reg.append_registration(
        path,
        {"adopted_prompt_version": "v3.0", "adopted_prompt_sha256": "a" * 64},
        repo_root=tmp_path,
    )
    with pytest.raises(reg.RegistrationError):
        reg.require_adopted(path, repo_root=tmp_path)
    commit(tmp_path)
    reg.require_adopted(path, repo_root=tmp_path)
    reg.append_registration(
        path,
        {"heldout_labels_sha256": digest, "heldout_task_list_sha256": "b" * 64},
        repo_root=tmp_path,
    )
    commit(tmp_path)
    result = tmp_path / "heldout-result.json"
    reg.require_heldout(path, labels, result, repo_root=tmp_path)
    labels.write_text("altered")
    with pytest.raises(reg.RegistrationError, match="labels_hash_mismatch"):
        reg.require_heldout(path, labels, result, repo_root=tmp_path)
    labels.write_text("synthetic labels")
    result.write_text("{}")
    with pytest.raises(reg.RegistrationError, match="already_graded"):
        reg.require_heldout(path, labels, result, repo_root=tmp_path)
    before = path.read_bytes()
    with pytest.raises(reg.RegistrationError, match="registration_overwrite"):
        reg.append_registration(path, {"evidence_max_chars": 1}, repo_root=tmp_path)
    assert path.read_bytes() == before
    value = json.loads(path.read_text())
    value["entries"][0]["evidence_max_chars"] = 12
    path.write_text(json.dumps(value))
    with pytest.raises(reg.RegistrationError):
        reg.append_registration(
            path, {"heldout_citations_sha256": "c" * 64}, repo_root=tmp_path
        )


def test_registration_dirty_splitter_and_cost_only_exception(tmp_path):
    repository(tmp_path)
    path = tmp_path / reg.REGISTRATION_PATH
    reg.append_registration(path, {"evidence_max_chars": 80000}, repo_root=tmp_path)
    commit(tmp_path)
    path.write_text(path.read_text() + "\n")
    with pytest.raises(reg.RegistrationError):
        reg.require_registration(path, repo_root=tmp_path)
    git(tmp_path, "checkout", "--", str(reg.REGISTRATION_PATH))
    ledger = tmp_path / "data/manifest/costs.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("synthetic")
    reg.require_clean(tmp_path, ["data/manifest", "docs/results"])
    other = ledger.with_name("other.json")
    other.write_text("{}")
    with pytest.raises(reg.RegistrationError, match="dirty_worktree"):
        reg.require_clean(tmp_path, ["data/manifest", "docs/results"])


def test_git_calls_are_bounded_scoped_and_hidden_changes_detected(
    tmp_path, monkeypatch
):
    repository(tmp_path)
    calls = []
    original = subprocess.run

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return original(args, **kwargs)

    monkeypatch.setattr(reg.subprocess, "run", run)
    reg.require_hypotheses(tmp_path)
    assert calls and all(kw["timeout"] == 30 for _, kw in calls)
    assert all(
        "--" in args and args.index("--") < len(args) - 1
        for args, _ in calls
        if "--porcelain" in args
    )
    path = tmp_path / reg.HYPOTHESES[0]
    git(tmp_path, "update-index", "--assume-unchanged", str(reg.HYPOTHESES[0]))
    path.write_text("hidden modification")
    with pytest.raises(reg.RegistrationError):
        reg.require_hypotheses(tmp_path)

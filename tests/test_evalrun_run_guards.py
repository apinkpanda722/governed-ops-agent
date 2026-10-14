"""Run guards use only temporary git repositories and synthetic file bytes."""

import hashlib
import subprocess

import pytest

from govops.evalrun.run_guards import (
    GUARD_CODES,
    REDTEAM_RUN_REGISTRATION,
    RUN_CONFIGS,
    TEST_TARGETS_REGISTRATION,
    guard_clean_worktree,
    guard_fresh_status_counts,
    guard_redteam_registration,
    guard_redteam_set,
    guard_switch,
    guard_test_targets,
    parse_test_targets,
    registered_values,
    registration_state,
)

SENTINEL = "synthetic-sentinel-private-content"
HASH = "abcdef01" * 8
FILES = {
    "set_sha256": "data/redteam/set.json",
    "confirmed_sha256": "data/redteam/review/confirmed.json",
    "allocation_sha256": "data/redteam/allocation.json",
    "overlay_sha256": "data/redteam/overlay/jira_tickets.json",
}


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()


def write(root, path, content):
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def commit(root, path):
    git(root, "add", "--", path)
    git(root, "-c", "commit.gpgsign=false", "commit", "-m", "synthetic fixture")
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Synthetic Fixture")
    git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    write(
        tmp_path,
        "scripts/exempt_paths.json",
        '{"ledger_paths": ["data/manifest/costs.jsonl"]}',
    )
    commit(tmp_path, "scripts/exempt_paths.json")
    write(tmp_path, "data/manifest/costs.jsonl", "")
    commit(tmp_path, "data/manifest/costs.jsonl")
    return tmp_path


def fails(code, call):
    with pytest.raises(ValueError) as caught:
        call()
    message = str(caught.value)
    assert message == code
    assert message in GUARD_CODES
    assert SENTINEL not in message
    assert HASH not in message
    assert "data/" not in message
    assert "docs/" not in message


def registration_text():
    return SENTINEL + "\n" + "".join(f"{key}: {HASH}\n" for key in FILES)


def targets_text(config="v1+v2"):
    return f"{SENTINEL}\n공식 구성 판정: {config}\nverdict_json_sha256: {HASH}\n"


def test_registration_committed_and_unchanged(repo):
    write(repo, REDTEAM_RUN_REGISTRATION, registration_text())
    registered_commit = commit(repo, REDTEAM_RUN_REGISTRATION)
    write(repo, "unrelated.txt", "synthetic")
    commit(repo, "unrelated.txt")
    assert registration_state(repo, REDTEAM_RUN_REGISTRATION) == {
        "committed": True,
        "unchanged": True,
        "commit": registered_commit,
    }
    guard_redteam_registration(repo)


@pytest.mark.parametrize(
    "state",
    ["missing", "untracked", "staged", "modified", "modified_staged", "deleted"],
)
def test_registration_rejects_uncommitted_or_modified(repo, state):
    if state != "missing":
        target = write(repo, REDTEAM_RUN_REGISTRATION, registration_text())
        if state == "staged":
            git(repo, "add", "--", REDTEAM_RUN_REGISTRATION)
        elif state not in {"untracked", "staged"}:
            commit(repo, REDTEAM_RUN_REGISTRATION)
            if state == "deleted":
                target.unlink()
            else:
                target.write_text(registration_text() + SENTINEL)
                if state == "modified_staged":
                    git(repo, "add", "--", REDTEAM_RUN_REGISTRATION)
    fails(
        "redteam_registration_not_committed", lambda: guard_redteam_registration(repo)
    )


def test_registered_values(tmp_path):
    write(tmp_path, REDTEAM_RUN_REGISTRATION, registration_text())
    assert registered_values(tmp_path) == dict.fromkeys(FILES, HASH)


@pytest.mark.parametrize("key", FILES)
@pytest.mark.parametrize(
    "problem", ["missing", "duplicate", "short", "invalid_duplicate"]
)
def test_registered_values_require_one_well_formed_line(tmp_path, key, problem):
    line = f"{key}: {HASH}\n"
    text = registration_text()
    if problem == "missing":
        text = text.replace(line, "")
    elif problem == "duplicate":
        text += line
    elif problem == "short":
        text = text.replace(line, f"{key}: {HASH[:-1]}\n")
    else:
        text += f"{key}: {SENTINEL}\n"
    write(tmp_path, REDTEAM_RUN_REGISTRATION, text)
    fails("redteam_registration_not_committed", lambda: registered_values(tmp_path))


def test_registered_values_missing_or_unreadable(tmp_path):
    fails("redteam_registration_not_committed", lambda: registered_values(tmp_path))
    target = write(tmp_path, REDTEAM_RUN_REGISTRATION, "")
    target.write_bytes(b"\xff")
    fails("redteam_registration_not_committed", lambda: registered_values(tmp_path))


@pytest.mark.parametrize("changed", [None, *FILES])
@pytest.mark.parametrize("missing", [False, True])
def test_redteam_file_byte_hashes(tmp_path, changed, missing):
    lines = []
    for key, path in FILES.items():
        target = write(tmp_path, path, SENTINEL + key)
        lines.append(f"{key}: {hashlib.sha256(target.read_bytes()).hexdigest()}\n")
    write(tmp_path, REDTEAM_RUN_REGISTRATION, "".join(lines))
    guard_redteam_set(tmp_path)
    if changed is not None:
        target = tmp_path / FILES[changed]
        if missing:
            target.unlink()
        else:
            target.write_bytes(target.read_bytes() + b"\n")
        fails("redteam_set_unconfirmed", lambda: guard_redteam_set(tmp_path))


@pytest.mark.parametrize("config", RUN_CONFIGS)
def test_targets_parse_and_guard(repo, config):
    text = targets_text(config)
    expected = {"official_config": config, "verdict_sha256": HASH}
    assert parse_test_targets(text) == expected
    write(repo, TEST_TARGETS_REGISTRATION, text)
    commit(repo, TEST_TARGETS_REGISTRATION)
    assert guard_test_targets(repo, config=config, role="official") == expected
    other = "v1" if config == "v1+v2" else "v1+v2"
    fails(
        "official_config_mismatch",
        lambda: guard_test_targets(repo, config=other, role="official"),
    )
    assert guard_test_targets(repo, config=other, role="reference") == expected


@pytest.mark.parametrize(
    "text",
    [
        f"verdict_json_sha256: {HASH}\n",
        targets_text() + "공식 구성 판정: v1\n",
        targets_text().replace("판정: v1+v2", f"판정: {SENTINEL}"),
        "공식 구성 판정: v1\n",
        targets_text() + f"verdict_json_sha256: {HASH}\n",
        targets_text().replace(HASH, HASH[:-1]),
        targets_text() + f"verdict_json_sha256: {SENTINEL}\n",
    ],
)
def test_targets_malformed_lines(repo, text):
    fails("hypotheses_test_targets_not_committed", lambda: parse_test_targets(text))
    write(repo, TEST_TARGETS_REGISTRATION, text)
    commit(repo, TEST_TARGETS_REGISTRATION)
    fails(
        "hypotheses_test_targets_not_committed",
        lambda: guard_test_targets(repo, config="v1+v2", role="official"),
    )


@pytest.mark.parametrize(
    "state", ["missing", "untracked", "staged", "modified", "modified_staged"]
)
def test_targets_registration_is_required(repo, state):
    if state != "missing":
        target = write(repo, TEST_TARGETS_REGISTRATION, targets_text())
        if state == "staged":
            git(repo, "add", "--", TEST_TARGETS_REGISTRATION)
        elif state.startswith("modified"):
            commit(repo, TEST_TARGETS_REGISTRATION)
            target.write_text(targets_text() + SENTINEL)
            if state == "modified_staged":
                git(repo, "add", "--", TEST_TARGETS_REGISTRATION)
    fails(
        "hypotheses_test_targets_not_committed",
        lambda: guard_test_targets(repo, config="v1+v2", role="reference"),
    )


@pytest.mark.parametrize(
    "config,switch",
    [("v1", "off"), ("v1", ""), ("v1", " \t"), ("v1+v2", "on"), ("v1+v2", " on ")],
)
def test_switch_matching(config, switch):
    guard_switch(config, switch)


@pytest.mark.parametrize(
    "config,switch",
    [("v1", "on"), ("v1+v2", "off"), ("v1+v2", " "), ("v1+v2", SENTINEL)],
)
def test_switch_mismatch(config, switch):
    fails("guardrail_v2_switch_mismatch", lambda: guard_switch(config, switch))


def test_invalid_config():
    fails("invalid_config", lambda: guard_switch(SENTINEL, "off"))


def test_fresh_status_counts():
    counts = dict.fromkeys(
        ("ok", "timeout", "rate_limited", "error", "parse", "v1_blocked"), 0
    )
    guard_fresh_status_counts(counts)
    guard_fresh_status_counts({})
    for key in counts:
        fails(
            "status_counts_not_fresh",
            lambda key=key: guard_fresh_status_counts(counts | {key: 1}),
        )


def test_clean_worktree_and_ledger_exemption(repo):
    guard_clean_worktree(repo)
    write(repo, "data/manifest/costs.jsonl", SENTINEL)
    guard_clean_worktree(repo)
    write(repo, "untracked-sentinel.txt", SENTINEL)
    fails("dirty_worktree", lambda: guard_clean_worktree(repo))


def test_dirty_tracked_file(repo):
    write(repo, "tracked.txt", "synthetic")
    commit(repo, "tracked.txt")
    write(repo, "tracked.txt", SENTINEL)
    fails("dirty_worktree", lambda: guard_clean_worktree(repo))


def test_worktree_guard_fails_closed_outside_git(tmp_path):
    fails("dirty_worktree", lambda: guard_clean_worktree(tmp_path))

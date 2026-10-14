"""Shared offline guards for the user-run M6 live runners."""

import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

from govops.eval.v2_validity import v2_validity, validity_label
from govops.evalrun import worktree
from govops.evalrun.dev import _git

__all__ = [
    "GUARD_CODES",
    "REDTEAM_RUN_REGISTRATION",
    "RUN_CONFIGS",
    "TEST_TARGETS_REGISTRATION",
    "guard_clean_worktree",
    "guard_fresh_status_counts",
    "guard_redteam_registration",
    "guard_redteam_set",
    "guard_switch",
    "guard_test_targets",
    "parse_test_targets",
    "registered_values",
    "registration_state",
    "v2_validity",
    "validity_label",
]

REDTEAM_RUN_REGISTRATION = "docs/results/hypotheses-m6-redteam-run.md"
TEST_TARGETS_REGISTRATION = "docs/results/hypotheses-test-targets.md"
RUN_CONFIGS = ("v1", "v1+v2")
GUARD_CODES = frozenset(
    {
        "redteam_registration_not_committed",
        "redteam_set_unconfirmed",
        "hypotheses_test_targets_not_committed",
        "official_config_mismatch",
        "invalid_config",
        "guardrail_v2_switch_mismatch",
        "status_counts_not_fresh",
        "dirty_worktree",
    }
)


def registration_state(root: Path, path: str) -> dict:
    """Check tracking, HEAD, the last registration commit, and local changes."""
    try:
        registration = _git(root, "log", "-1", "--format=%H", "--", path)[1]
        committed = bool(registration) and all(
            code == 0
            for code in (
                _git(root, "ls-files", "--error-unmatch", path)[0],
                _git(root, "cat-file", "-e", f"HEAD:{path}")[0],
                _git(root, "cat-file", "-e", f"{registration}:{path}")[0],
            )
        )
        code, delta = (
            _git(root, "diff", f"{registration}..HEAD", "--", path)
            if registration
            else (1, "")
        )
        unchanged = (
            code == 0
            and not delta
            and _git(root, "diff", "HEAD", "--quiet", "--", path)[0] == 0
        )
    except OSError:
        return {"committed": False, "unchanged": False, "commit": ""}
    return {"committed": committed, "unchanged": unchanged, "commit": registration}


def _single_value(text: str, name: str, pattern: str, code: str) -> str:
    lines = re.findall(rf"^{re.escape(name)}(?=:|\s|$).*$", text, re.MULTILINE)
    match = (
        re.fullmatch(rf"{re.escape(name)}: ({pattern})", lines[0])
        if len(lines) == 1
        else None
    )
    if match is None:
        raise ValueError(code)
    return match.group(1)


def registered_values(root: Path) -> dict:
    code = "redteam_registration_not_committed"
    try:
        text = (root / REDTEAM_RUN_REGISTRATION).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError(code) from None
    return {
        name: _single_value(text, name, "[0-9a-f]{64}", code)
        for name in (
            "set_sha256",
            "confirmed_sha256",
            "allocation_sha256",
            "overlay_sha256",
        )
    }


def guard_redteam_registration(root: Path) -> None:
    state = registration_state(root, REDTEAM_RUN_REGISTRATION)
    if not state["committed"] or not state["unchanged"]:
        raise ValueError("redteam_registration_not_committed")


def guard_redteam_set(root: Path) -> None:
    values = registered_values(root)
    for name, path in (
        ("set_sha256", "data/redteam/set.json"),
        ("confirmed_sha256", "data/redteam/review/confirmed.json"),
        ("allocation_sha256", "data/redteam/allocation.json"),
        ("overlay_sha256", "data/redteam/overlay/jira_tickets.json"),
    ):
        try:
            digest = hashlib.sha256((root / path).read_bytes()).hexdigest()
        except OSError:
            raise ValueError("redteam_set_unconfirmed") from None
        if digest != values[name]:
            raise ValueError("redteam_set_unconfirmed")


def parse_test_targets(text: str) -> dict:
    code = "hypotheses_test_targets_not_committed"
    return {
        "official_config": _single_value(text, "공식 구성 판정", r"v1\+v2|v1", code),
        "verdict_sha256": _single_value(
            text, "verdict_json_sha256", "[0-9a-f]{64}", code
        ),
    }


def guard_test_targets(root: Path, *, config: str, role: str) -> dict:
    code = "hypotheses_test_targets_not_committed"
    state = registration_state(root, TEST_TARGETS_REGISTRATION)
    if not state["committed"] or not state["unchanged"]:
        raise ValueError(code)
    try:
        text = (root / TEST_TARGETS_REGISTRATION).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ValueError(code) from None
    values = parse_test_targets(text)
    if role == "official" and config != values["official_config"]:
        raise ValueError("official_config_mismatch")
    return values


def guard_switch(config: str, env_value: str) -> None:
    if config not in RUN_CONFIGS:
        raise ValueError("invalid_config")
    switch = env_value.strip() or "off"
    if switch != ("on" if config == "v1+v2" else "off"):
        raise ValueError("guardrail_v2_switch_mismatch")


def guard_fresh_status_counts(counts: Mapping[str, int]) -> None:
    if any(value != 0 for value in counts.values()):
        raise ValueError("status_counts_not_fresh")


def guard_clean_worktree(root: Path) -> None:
    try:
        code, status = _git(root, "status", "--porcelain")
        if code != 0 or worktree.dirty_entries(status, root):
            raise ValueError("dirty_worktree")
    except (OSError, ValueError):
        raise ValueError("dirty_worktree") from None

"""Append-only M8 registrations and bounded, path-scoped git integrity gates."""

import fcntl
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

from govops.grounding.sentences import SPLITTER_RULES_VERSION, splitter_rules_sha256

REGISTRATION_PATH = Path("docs/results/m8-grader-registration.json")
HYPOTHESES = (
    *(
        f"docs/results/hypotheses-m8-{kind}.md"
        for kind in ("grader", "citation", "hallucination")
    ),
    # Addenda are registrations too: they must be committed and unchanged.
    "docs/results/hypotheses-m8-grader-addendum-1.md",
    "docs/results/hypotheses-m8-grader-addendum-2.md",
)
FIELDS = {
    "evidence_max_chars",
    "calibration_labels_sha256",
    "calibration_citations_sha256",
    "adopted_prompt_version",
    "adopted_prompt_sha256",
    "heldout_labels_sha256",
    "heldout_citations_sha256",
    "heldout_task_list_sha256",
    "splitter_rules_version",
    "splitter_rules_sha256",
}


class RegistrationError(ValueError):
    """A fixed gate code, never a path, command output, or source value."""


def sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _git(root, *args):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RegistrationError("git_check_failed") from None
    if result.returncode:
        raise RegistrationError("git_check_failed")
    return result.stdout


def _relative(path, root):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        raise RegistrationError("repository_path_invalid") from None


def _committed_bytes(root, path):
    relative = _relative(path, root)
    value = _git(root, "show", f"HEAD:{relative}")
    if _git(root, "status", "--porcelain", "--", relative):
        raise RegistrationError("committed_file_modified")
    current = _git(root, "hash-object", "--path=" + relative, "--", relative)
    committed = _git(root, "rev-parse", "HEAD:" + relative)
    if current != committed:
        raise RegistrationError("committed_file_modified")
    return value


def require_hypotheses(repo_root):
    try:
        for relative in HYPOTHESES:
            _committed_bytes(repo_root, Path(repo_root) / relative)
            commits = _git(
                repo_root,
                "log",
                "--reverse",
                "--diff-filter=A",
                "--format=%H",
                "HEAD",
                "--",
                relative,
            ).splitlines()
            if not commits:
                raise RegistrationError("hypotheses_not_committed")
            first = commits[0].decode("ascii")
            if _git(repo_root, "rev-list", first + "..HEAD", "--", relative).strip():
                raise RegistrationError("hypotheses_not_committed")
    except (RegistrationError, UnicodeError):
        raise RegistrationError("hypotheses_not_committed") from None


def require_clean(repo_root, paths):
    """Only declared targets are inspected; the central cost-ledger list is exempt."""
    if not paths:
        raise RegistrationError("empty_clean_scope")
    try:
        exempt = json.loads(
            (Path(repo_root) / "scripts/exempt_paths.json").read_text()
        )["ledger_paths"]
        exclusions = [f":(exclude){path}" for path in exempt]
        output = _git(
            repo_root, "status", "--porcelain", "--", *map(str, paths), *exclusions
        )
        if output:
            raise RegistrationError("dirty_worktree")
    except (OSError, KeyError, TypeError, ValueError):
        raise RegistrationError("dirty_worktree") from None


def _entries(raw):
    if (
        not isinstance(raw, dict)
        or set(raw) != {"entries"}
        or not isinstance(raw["entries"], list)
    ):
        raise RegistrationError("registration_invalid")
    combined = {}
    for entry in raw["entries"]:
        if (
            not isinstance(entry, dict)
            or not entry
            or set(entry) - FIELDS
            or set(entry) & set(combined)
        ):
            raise RegistrationError("registration_invalid")
        for key, value in entry.items():
            if key.endswith("sha256") and (
                not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            ):
                raise RegistrationError("registration_invalid")
            if key == "evidence_max_chars" and (type(value) is not int or value <= 0):
                raise RegistrationError("registration_invalid")
            if key == "adopted_prompt_version" and value not in (
                "v3.0",
                "v3.1",
                "v3.2",
                "v3.3",
            ):
                raise RegistrationError("registration_invalid")
        combined.update(entry)
    return combined


def _check_splitter(value):
    if (
        value.get("splitter_rules_version") != SPLITTER_RULES_VERSION
        or value.get("splitter_rules_sha256") != splitter_rules_sha256()
    ):
        raise RegistrationError("splitter_rules_changed")


def read_registration(path):
    try:
        value = _entries(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, UnicodeError, TypeError):
        raise RegistrationError("registration_invalid") from None
    _check_splitter(value)
    return value


def _committed_history(repo_root, relative):
    """Every committed generation must extend its predecessor without edits."""
    previous = {"entries": []}
    commits = _git(
        repo_root, "log", "--reverse", "--format=%H", "HEAD", "--", relative
    ).splitlines()
    try:
        for commit in commits:
            current = json.loads(
                _git(repo_root, "show", commit.decode("ascii") + ":" + relative)
            )
            _entries(current)
            if current["entries"][: len(previous["entries"])] != previous["entries"]:
                raise RegistrationError("registration_history_modified")
            previous = current
    except (UnicodeError, ValueError, TypeError, KeyError):
        raise RegistrationError("registration_history_modified") from None
    return previous


def append_registration(path, entry, *, repo_root):
    require_hypotheses(repo_root)
    path = Path(path)
    relative = _relative(path, repo_root)
    committed = _committed_history(repo_root, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as target:
        fcntl.flock(target.fileno(), fcntl.LOCK_EX)
        raw_text = target.read()
        raw = json.loads(raw_text) if raw_text else {"entries": []}
        previous = _entries(raw)
        if previous:
            _check_splitter(previous)
        # Existing committed history must remain a prefix even while appending.
        if raw["entries"][: len(committed["entries"])] != committed["entries"]:
            raise RegistrationError("registration_history_modified")
        if set(entry) & set(previous):
            raise RegistrationError("registration_overwrite")
        addition = dict(entry)
        if not previous:
            addition.update(
                {
                    "splitter_rules_version": SPLITTER_RULES_VERSION,
                    "splitter_rules_sha256": splitter_rules_sha256(),
                }
            )
        updated = {"entries": [*raw["entries"], addition]}
        _entries(updated)
        target.seek(0)
        target.write(json.dumps(updated, sort_keys=True, indent=2) + "\n")
        target.truncate()
        target.flush()
        os.fsync(target.fileno())
    return read_registration(path)


def require_registration(path, *, repo_root):
    require_hypotheses(repo_root)
    try:
        _committed_bytes(repo_root, path)
    except RegistrationError:
        raise RegistrationError("registration_not_committed") from None
    value = read_registration(path)
    _committed_history(repo_root, _relative(path, repo_root))
    return value


def _labels_gate(path, labels, split, repo_root, citations=None):
    value = require_registration(path, repo_root=repo_root)
    key = split + "_labels_sha256"
    if key not in value:
        raise RegistrationError("labels_not_registered")
    if sha256(labels) != value[key]:
        raise RegistrationError("labels_hash_mismatch")
    if citations is not None and value.get(split + "_citations_sha256") != sha256(
        citations
    ):
        raise RegistrationError("labels_hash_mismatch")
    return value


def require_calibration(path, labels, *, repo_root, citations=None):
    return _labels_gate(path, labels, "calibration", repo_root, citations)


def require_adopted(path, *, repo_root):
    value = require_registration(path, repo_root=repo_root)
    if not value.get("adopted_prompt_sha256") or not value.get(
        "adopted_prompt_version"
    ):
        raise RegistrationError("prompt_not_adopted")
    return value


def require_heldout(path, labels, result, *, repo_root, citations=None):
    if Path(result).exists():
        raise RegistrationError("already_graded")
    require_adopted(path, repo_root=repo_root)
    return _labels_gate(path, labels, "heldout", repo_root, citations)

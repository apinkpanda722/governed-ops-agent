"""Independent source and chronological boundaries for the generated corpus."""

import ast
import json
import subprocess
from pathlib import Path

import pytest

from govops.eval import redteam
from govops.guardrails.registry import rules_sha256

ROOT = Path(__file__).resolve().parents[1]


def history_mode(root):
    if redteam.git(root, "rev-parse", "--is-shallow-repository") == "true":
        raise ValueError("shallow_clone_cannot_verify")
    tracked = redteam.git(root, "ls-files", "--", "data/redteam/set.json")
    mode = "post_commit" if tracked else "pre_commit"
    for path in redteam.REGISTRATIONS:
        added = redteam.git(
            root, "log", "--diff-filter=A", "--format=%H", "--", path
        ).splitlines()
        if not added:
            raise ValueError("registration_history_missing")
        if mode == "pre_commit":
            redteam.git(root, "diff", "HEAD", "--quiet", "--", path)
        else:
            generated = redteam.git(
                root,
                "log",
                "--diff-filter=A",
                "--format=%H",
                "--",
                "data/redteam/set.json",
            ).splitlines()
            if not generated or added[-1] == generated[-1]:
                raise ValueError("registration_not_before_generation")
            redteam.git(root, "merge-base", "--is-ancestor", added[-1], generated[-1])
    assert mode in {"pre_commit", "post_commit"}
    return mode


def test_actual_source_and_manifest_boundary():
    files = list((ROOT / "src/govops/eval").glob("redteam*.py"))
    assert {file.name for file in files} == {
        "redteam.py",
        "redteam_templates.py",
        "redteam_cards.py",
    }
    for file in files:
        source = file.read_text()
        assert "rules_v1" not in source
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
                if node.module == "govops.guardrails.registry":
                    assert [a.name for a in node.names] == ["rules_sha256"]
            else:
                continue
            assert all(
                m == "govops.guardrails.registry"
                for m in modules
                if m.startswith("govops")
            )
            assert all(
                part not in {"screen", "injection", "pii", "policy"}
                for m in modules
                for part in m.split(".")
            )
    for layer in ("guardrails", "gateway", "agent", "api"):
        for file in (ROOT / "src/govops" / layer).rglob("*.py"):
            source = file.read_text()
            assert all(
                word not in source
                for word in ("data/redteam", "eval.redteam", "redteam")
            )
    manifest = json.loads((ROOT / "data/redteam/manifest.json").read_text())
    assert manifest["rules_sha256"] == rules_sha256()
    assert history_mode(ROOT) in {"pre_commit", "post_commit"}


def run_git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def committed_repo(tmp_path):
    run_git(tmp_path, "init")
    run_git(tmp_path, "config", "user.email", "synthetic@example.invalid")
    run_git(tmp_path, "config", "user.name", "Synthetic role")
    for path in redteam.REGISTRATIONS:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("registered\n")
    run_git(tmp_path, "add", ".")
    run_git(tmp_path, "commit", "-m", "docs: register")


@pytest.mark.parametrize("mode", ["pre_commit", "post_commit"])
def test_history_modes(tmp_path, mode):
    committed_repo(tmp_path)
    file = tmp_path / "data/redteam/set.json"
    file.write_text("[]\n")
    if mode == "post_commit":
        run_git(tmp_path, "add", ".")
        run_git(tmp_path, "commit", "-m", "feat: generate")
    assert history_mode(tmp_path) == mode


@pytest.mark.parametrize(
    "failure", ["shallow", "changed", "missing", "same_commit", "unreadable"]
)
def test_history_failures(tmp_path, failure):
    if failure == "unreadable":
        with pytest.raises(ValueError):
            history_mode(tmp_path)
        return
    committed_repo(tmp_path)
    if failure == "shallow":
        head = redteam.git(tmp_path, "rev-parse", "HEAD")
        (tmp_path / ".git/shallow").write_text(head + "\n")
    elif failure == "changed":
        (tmp_path / redteam.REGISTRATIONS[0]).write_text("changed")
    elif failure == "missing":
        run_git(tmp_path, "rm", redteam.REGISTRATIONS[0])
        run_git(tmp_path, "commit", "-m", "docs: remove")
        run_git(tmp_path, "checkout", "--orphan", "empty-history")
        run_git(tmp_path, "commit", "-m", "docs: incomplete registration")
    else:
        (tmp_path / "data/redteam/set.json").write_text("[]")
        run_git(tmp_path, "add", ".")
        run_git(tmp_path, "commit", "--amend", "--no-edit")
    with pytest.raises(
        ValueError,
        match="shallow_clone_cannot_verify" if failure == "shallow" else None,
    ):
        history_mode(tmp_path)

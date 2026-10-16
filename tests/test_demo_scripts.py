"""User-run demo scripts exercised only in synthetic repositories, without uv IO."""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

from govops.audit.crypto import canonical_json
from govops.audit.log import AuditLog
from govops.audit.verify import verify_chain

ROOT = Path(__file__).resolve().parents[1]


def _emit(log):
    log.append("auth.failed", task_id=None, actor_sub="system", reason="invalid_token")


def _write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(canonical_json(row) + "\n" for row in rows))


def _snapshot(paths):
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}


@pytest.fixture
def demo_repo(tmp_path):
    root = tmp_path / "synthetic repo"
    script = root / "scripts/demo/scene5.sh"
    script.parent.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts/demo/scene5.sh", script)
    # Keep a previous run's evidence to catch overly broad trap cleanup.
    previous = root / "data/interim/demo-scene5-previous/keep.txt"
    previous.parent.mkdir(parents=True)
    previous.write_text("합성 이전 실행 흔적\n")
    return root


def _environment(root, *, verifier_output=None):
    """Replace only uv dispatch; execute inline Python and the real verifier CLI."""
    bin_dir = root / "bin"
    bin_dir.mkdir()
    runner = bin_dir / "uv"
    runner.write_text(
        f"#!{sys.executable}\n"
        + textwrap.dedent(
            """\
            import json
            import os
            import subprocess
            import sys
            from pathlib import Path

            assert sys.argv[1:3] == ["run", "python"]
            args = sys.argv[3:]
            if args[:2] == ["-m", "govops.audit.verify"]:
                directory = Path(args[args.index("--dir") + 1])
                anchors = Path(args[args.index("--anchors") + 1])
                rows = [json.loads(line) for line in anchors.read_text().splitlines()]
                files = sorted(directory.iterdir())
                probe = {
                    "directory": str(directory),
                    "anchors": [{"dir": r["dir"], "seq": r["seq"]} for r in rows],
                    "files": [p.name for p in files],
                    "sequences": {
                        p.name: [json.loads(s)["seq"] for s in p.read_text().splitlines()]
                        for p in files
                    },
                }
                Path("probe.json").write_text(json.dumps(probe))
                if "SYNTHETIC_VERIFY_OUTPUT" in os.environ:
                    print(os.environ["SYNTHETIC_VERIFY_OUTPUT"])
                    sys.exit(1)
            result = subprocess.run([sys.executable, *args], timeout=15, check=False)
            sys.exit(result.returncode)
            """
        )
    )
    runner.chmod(0o755)
    env = {
        "PATH": str(bin_dir) + os.pathsep + os.defpath,
        "PYTHONPATH": str(ROOT / "src"),
        "AUDIT_PUBLIC_KEY_FILE": os.environ["AUDIT_PUBLIC_KEY_FILE"],
    }
    if verifier_output is not None:
        env["SYNTHETIC_VERIFY_OUTPUT"] = verifier_output
    return env


def _run(root, env):
    # Run from outside the repository; all artifact paths must be root-relative.
    return subprocess.run(
        ["sh", str(root / "scripts/demo/scene5.sh")],
        cwd=root.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _assert_cleaned(root):
    assert sorted(p.name for p in (root / "data/interim").iterdir()) == [
        "audit",
        "demo-scene5-previous",
    ]


@pytest.mark.parametrize("anchor_source", ["pending", "committed"])
@pytest.mark.parametrize("env_style", ["explicit", "empty", "unset"])
def test_scene5_pass_before_cut_then_detects_cut_read_only(
    demo_repo, anchor_source, env_style
):
    root = demo_repo
    directory = root / "data/interim/audit"
    pending = directory / "anchors-pending.jsonl"
    committed = root / "docs/results/audit-anchors.jsonl"
    # Fix the dates to exercise two files and truncation of only the last file.
    now = datetime(2026, 1, 1, tzinfo=UTC)
    log = AuditLog(directory, clock=lambda: now)
    _emit(log)
    older = log.anchor("older", repo_root=root, pending_file=pending)
    now = datetime(2026, 1, 2, tzinfo=UTC)
    _emit(log)
    selected = log.anchor("selected", repo_root=root, pending_file=pending)
    _emit(log)
    # This newer in-chain anchor must NOT override the external anchor files.
    log.append("chain.anchor", task_id=None, actor_sub="system", label="unpublished")
    _emit(log)
    unrelated = {**selected, "dir": "another-audit", "seq": selected["seq"] + 100}
    # Neither file order nor line order is sequence order.
    _write_rows(
        pending, [selected, unrelated, older] if anchor_source == "pending" else [older]
    )
    _write_rows(
        committed, [selected, older] if anchor_source == "committed" else [older]
    )
    (directory / "not-a-chain.txt").write_text("합성 복사 제외\n")

    # smoke.scene5_tail_cut's `after`: retaining the external anchor passes.
    before_copy = root / "before-copy"
    before_copy.mkdir()
    originals = sorted(directory.glob("chain-*.jsonl"))
    for source in originals:
        shutil.copyfile(source, before_copy / source.name)
    last = before_copy / originals[-1].name
    rows = [json.loads(line) for line in last.read_text().splitlines()]
    _write_rows(last, [row for row in rows if row["seq"] <= selected["seq"]])
    after = verify_chain(
        before_copy,
        anchors=[{**selected, "dir": "before-copy"}],
        repo_root=root,
    )
    assert after.ok and after.anchors_checked == 1
    assert after.records == selected["seq"]

    protected = [
        *directory.iterdir(),
        committed,
        root / "data/interim/demo-scene5-previous/keep.txt",
    ]
    snapshot = _snapshot(protected)
    env = _environment(root)
    if env_style == "explicit":
        env["AUDIT_DIR"] = str(directory)
        env["AUDIT_ANCHORS_PENDING_FILE"] = str(pending)
    elif env_style == "empty":
        env["AUDIT_DIR"] = " "
        env["AUDIT_ANCHORS_PENDING_FILE"] = ""
    result = _run(root, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"code=tail_truncated seq={selected['seq']}" in result.stdout
    assert "anchors_checked=1" in result.stdout
    assert result.stderr == ""
    assert not re.search(r"[0-9a-f]{64}|[0-9a-f]{128}", result.stdout)
    assert "invalid_token" not in result.stdout
    assert "chain.anchor" not in result.stdout

    probe = json.loads((root / "probe.json").read_text())
    copied = Path(probe["directory"])
    assert copied.is_relative_to(root / "data/interim")
    assert not copied.exists()
    assert probe["anchors"] == [
        {"dir": copied.relative_to(root).as_posix(), "seq": selected["seq"]}
    ]
    assert probe["files"] == [p.name for p in originals]
    assert probe["sequences"][originals[0].name] == [1, 2, older["seq"]]
    assert probe["sequences"][originals[-1].name] == [selected["seq"] - 1]
    assert _snapshot(protected) == snapshot
    _assert_cleaned(root)


@pytest.mark.parametrize("anchor_source", ["missing", "other-directory"])
def test_scene5_without_matching_external_anchor_exits_two(demo_repo, anchor_source):
    root = demo_repo
    directory = root / "data/interim/audit"
    pending = directory / "anchors-pending.jsonl"
    log = AuditLog(directory)
    _emit(log)
    anchor = log.anchor("not-published", repo_root=root, pending_file=root / "unused")
    if anchor_source == "other-directory":
        _write_rows(pending, [{**anchor, "dir": "another-audit"}])
    protected = [*directory.iterdir(), root / "unused"]
    snapshot = _snapshot(protected)
    result = _run(root, _environment(root))
    assert result.returncode == 2
    assert "anchor" in (result.stdout + result.stderr).lower()
    assert not (root / "probe.json").exists()
    assert _snapshot(protected) == snapshot
    _assert_cleaned(root)


@pytest.mark.parametrize("output", ["PASS chain", "FAIL code=sig_invalid"])
def test_scene5_requires_tail_truncated_output_and_cleans_on_failure(demo_repo, output):
    root = demo_repo
    directory = root / "data/interim/audit"
    log = AuditLog(directory)
    _emit(log)
    log.anchor(
        "selected", repo_root=root, pending_file=directory / "anchors-pending.jsonl"
    )
    _emit(log)
    protected = list(directory.iterdir())
    snapshot = _snapshot(protected)
    result = _run(root, _environment(root, verifier_output=output))
    assert result.returncode == 1
    assert output in result.stdout
    assert _snapshot(protected) == snapshot
    _assert_cleaned(root)


def test_mock_faults_contract():
    rules = json.loads((ROOT / "scripts/demo/mock_faults.json").read_text())
    assert rules == [
        {"endpoint": "slack.send", "nth": [1], "action": {"type": "drop_after_apply"}},
        {
            "endpoint": "slack.send",
            "nth": [1],
            "action": {"type": "hide_for", "ms": 1000},
        },
    ]
    assert all(type(rule["nth"][0]) is int for rule in rules)
    assert type(rules[1]["action"]["ms"]) is int


def test_demo_make_targets_are_user_run_only():
    text = (ROOT / "Makefile").read_text()
    phony = {
        word
        for line in text.splitlines()
        if line.startswith(".PHONY:")
        for word in line.split()[1:]
    }
    verify = text.split("\nverify:\n", 1)[1].split("\n\n", 1)[0]
    for name in ("demo-scene5", "demo-faults-on", "demo-faults-off"):
        assert name in phony
        assert name not in verify
    assert (
        "# 사용자 실행, 복사본에서만 꼬리를 자른다\ndemo-scene5:\n\tsh scripts/demo/scene5.sh\n"
        in text
    )
    command = (
        "docker compose --env-file .env -f infra/docker-compose.yml "
        "exec -T mock-slack python -m govops.mocks.admin faults "
    )
    assert (
        f"\ndemo-faults-on:\n\t{command}set --file /dev/stdin < scripts/demo/mock_faults.json\n"
    ) in text
    assert f"\ndemo-faults-off:\n\t{command}clear\n" in text

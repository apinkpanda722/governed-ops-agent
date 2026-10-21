"""Offline operator CLI and frozen rollout command examples."""

import re
import shlex
from datetime import UTC, datetime
from pathlib import Path

import pytest

from govops.rollout import cli, store
from govops.rollout.policy import RolloutState

ROOT = Path(__file__).resolve().parents[1]
ZERO_COUNTS = "assigned_v1=0 assigned_v2=0 variant_missing=0 rollbacks=0"


@pytest.fixture(autouse=True)
def isolated_rollout(monkeypatch, tmp_path):
    monkeypatch.setenv("GOVOPS_ROLLOUT_DIR", str(tmp_path))


def output(capsys, tmp_path, expected):
    captured = capsys.readouterr()
    assert captured.out == expected + "\n"
    assert captured.err == ""
    assert str(tmp_path) not in captured.out


def test_status_without_state(tmp_path, capsys):
    assert cli.main(["status"]) == 0
    output(capsys, tmp_path, f"pct_bp=0 kill=false reason=none {ZERO_COUNTS}")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("pct_bp", [0, 500, 10000])
def test_set_then_status(tmp_path, capsys, pct_bp):
    assert cli.main(["set", "--pct-bp", str(pct_bp)]) == 0
    output(capsys, tmp_path, f"OK pct_bp={pct_bp}")
    assert store.read_state(tmp_path) == RolloutState(pct_bp, False, None, None)
    assert cli.main(["status"]) == 0
    output(capsys, tmp_path, f"pct_bp={pct_bp} kill=false reason=none {ZERO_COUNTS}")


@pytest.mark.parametrize("reason", [None, "rollback_classify_abnormal"])
def test_kill_then_set_clears_kill(tmp_path, capsys, reason):
    args = ["kill"] if reason is None else ["kill", "--reason", reason]
    before = datetime.now(UTC)
    assert cli.main(args) == 0
    after = datetime.now(UTC)
    expected_reason = reason or "manual_kill"
    output(capsys, tmp_path, f"OK kill reason={expected_reason}")
    state = store.read_state(tmp_path)
    assert state.pct_bp == 0 and state.kill is True
    assert state.reason == expected_reason
    timestamp = datetime.fromisoformat(state.rolled_back_at)
    assert timestamp.utcoffset().total_seconds() == 0
    assert before <= timestamp <= after
    assert cli.main(["status"]) == 0
    output(
        capsys, tmp_path, f"pct_bp=0 kill=true reason={expected_reason} {ZERO_COUNTS}"
    )
    assert cli.main(["set", "--pct-bp", "500"]) == 0
    output(capsys, tmp_path, "OK pct_bp=500")
    assert store.read_state(tmp_path) == RolloutState(500, False, None, None)


@pytest.mark.parametrize(
    "args",
    [
        ["set", "--pct-bp", "-1"],
        ["set", "--pct-bp", "10001"],
        ["set", "--pct-bp", "invalid"],
        ["kill", "--reason", "Invalid-reason"],
        ["kill", "--reason", "a" * 41],
        ["kill", "--reason", ""],
        [],
        ["unknown"],
        ["set"],
        ["status", "--unknown"],
    ],
)
def test_invalid_arguments_are_opaque(tmp_path, capsys, args):
    assert cli.main(args) == 1
    output(capsys, tmp_path, "FAIL m8_rollout_invalid_arguments")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("args", [["set", "--pct-bp", "500"], ["kill"]])
def test_state_write_failure_is_opaque(monkeypatch, tmp_path, capsys, args):
    def fail_write(directory, state):
        raise OSError(f"private file content at {directory}")

    monkeypatch.setattr(store, "write_state", fail_write)
    assert cli.main(args) == 1
    output(capsys, tmp_path, "FAIL m8_rollout_state_write_failed")


def test_status_rejects_broken_exposures(tmp_path, capsys):
    (tmp_path / "exposures.jsonl").write_text(
        "private broken record\n", encoding="utf-8"
    )
    assert cli.main(["status"]) == 1
    output(capsys, tmp_path, "FAIL m8_rollout_records_invalid")


def test_status_preserves_fail_safe_state(tmp_path, capsys):
    (tmp_path / "state.json").write_text("private broken state", encoding="utf-8")
    assert cli.main(["status"]) == 0
    output(capsys, tmp_path, f"pct_bp=0 kill=true reason=state_invalid {ZERO_COUNTS}")


def test_status_counts_exposure_events(tmp_path, capsys):
    for index, variant in enumerate(["v1", "v1", "v2", "v2", "v2"]):
        store.append_exposure(
            tmp_path,
            {
                "event": "assigned",
                "ts": "2026-10-21T00:00:00+00:00",
                "conversation_id": f"{index:032x}",
                "user_sha256": "a" * 64,
                "bucket": index,
                "variant": variant,
                "pct_bp": 500,
                "kill": False,
            },
        )
    store.append_exposure(
        tmp_path,
        {"event": "variant_missing", "ts": "synthetic", "conversation_id": "b" * 32},
    )
    store.append_exposure(
        tmp_path,
        {
            "event": "rollback",
            "ts": "synthetic",
            "reason": "rollback_classify_abnormal",
            "pct_bp": 500,
        },
    )
    assert cli.main(["status"]) == 0
    output(
        capsys,
        tmp_path,
        "pct_bp=0 kill=false reason=none "
        "assigned_v1=2 assigned_v2=3 variant_missing=1 rollbacks=1",
    )


def test_registered_commands_match_cli_and_makefile(tmp_path, capsys):
    document = (ROOT / "docs/results/hypotheses-m8-rollout.md").read_text()
    commands = [
        line
        for block in re.findall(r"```bash\n(.*?)\n```", document, re.DOTALL)
        for line in block.splitlines()
        if line.strip()
    ]
    make_examples = [shlex.split(line) for line in commands if line.startswith("make ")]
    targets = {
        words[1] for words in make_examples if words[1].startswith("m8-rollout-")
    }
    assert targets == {
        "m8-rollout-status",
        "m8-rollout-set",
        "m8-rollout-kill",
        "m8-rollout-exercise",
    }
    expected = {"m8-rollout-status", "m8-rollout-set", "m8-rollout-kill"}
    makefile = (ROOT / "Makefile").read_text()
    for target in expected:
        assert f"\n{target}:\n" in makefile

    pct_examples = [
        word.split("=", 1)[1]
        for words in make_examples
        if words[1] == "m8-rollout-set"
        for word in words[2:]
        if word.startswith("PCT_BP=")
    ]
    assert pct_examples
    set_recipe = makefile.split("\nm8-rollout-set:\n", 1)[1].split("\n\n", 1)[0]
    assert "$(PCT_BP)" in set_recipe
    marker = "python -m govops.rollout.cli "
    cli_examples = [line.split(marker, 1)[1] for line in commands if marker in line]
    assert cli_examples
    for example in cli_examples:
        for pct_bp in pct_examples:
            args = shlex.split(example.replace("$(PCT_BP)", pct_bp))
            assert cli.main(args) == 0
            captured = capsys.readouterr()
            assert captured.err == ""
            assert str(tmp_path) not in captured.out
            assert len(captured.out.splitlines()) == 1

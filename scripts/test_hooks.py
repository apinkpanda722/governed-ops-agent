"""verify-before-stop.sh: phase 실행 중 표식(.harness/executing) 처리 테스트."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).parent / "hooks" / "verify-before-stop.sh"


@pytest.fixture
def project(tmp_path):
    """verify가 항상 실패하고 실행 흔적(ran)을 남기는 더러운 git 저장소."""
    (tmp_path / "Makefile").write_text("verify:\n\ttouch ran\n\texit 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "dirty.txt").write_text("x")
    (tmp_path / ".harness").mkdir()
    return tmp_path


def run_hook(project: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k != "HARNESS_EXECUTOR_PID"}
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"cwd": str(project)}),
        capture_output=True,
        text=True,
        check=False,
        env={**base, **(env or {})},
        cwd=project,
    )


def write_lock(project: Path, pid: int) -> None:
    (project / ".harness" / "executing").write_text(f"{pid}\n")


def test_without_lock_verifies(project):
    result = run_hook(project)
    assert result.returncode == 2 and (project / "ran").exists()


def test_live_foreign_pid_skips_verification(project):
    # 이 훅의 조상이 아닌 살아 있는 프로세스(= 다른 터미널의 execute.py 역할).
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        write_lock(project, other.pid)
        result = run_hook(project)
    finally:
        other.kill()
        other.wait()
    assert result.returncode == 0
    assert not (project / "ran").exists()


def test_dead_pid_verifies(project):
    other = subprocess.Popen([sys.executable, "-c", "pass"])
    other.wait()
    write_lock(project, other.pid)
    result = run_hook(project)
    assert result.returncode == 2 and (project / "ran").exists()


def test_executor_descendant_still_verifies(project):
    # execute.py가 물려 준 HARNESS_EXECUTOR_PID가 잠금 PID와 같으면 step을 실행하는
    # Codex 세션이므로 계속 검증해 스스로 고치게 한다.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        write_lock(project, other.pid)
        result = run_hook(project, {"HARNESS_EXECUTOR_PID": str(other.pid)})
    finally:
        other.kill()
        other.wait()
    assert result.returncode == 2 and (project / "ran").exists()


def test_other_executor_env_does_not_count(project):
    # 환경 변수가 다른 PID를 가리키면(옛 실행기) 건너뛰기 대상이다.
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        write_lock(project, other.pid)
        result = run_hook(project, {"HARNESS_EXECUTOR_PID": "1"})
    finally:
        other.kill()
        other.wait()
    assert result.returncode == 0 and not (project / "ran").exists()


def test_does_not_depend_on_ps(project, tmp_path):
    # Codex 샌드박스처럼 ps가 막혀 있어도 판정이 같다.
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "ps").write_text("#!/bin/sh\nexit 1\n")
    (stub / "ps").chmod(0o755)
    path = f"{stub}:{os.environ['PATH']}"
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        write_lock(project, other.pid)
        skipped = run_hook(project, {"PATH": path})
        verified = run_hook(
            project, {"PATH": path, "HARNESS_EXECUTOR_PID": str(other.pid)}
        )
    finally:
        other.kill()
        other.wait()
    assert skipped.returncode == 0
    assert verified.returncode == 2


def test_garbage_lock_verifies(project):
    (project / ".harness" / "executing").write_text("not-a-pid\n")
    result = run_hook(project)
    assert result.returncode == 2 and (project / "ran").exists()


@pytest.mark.parametrize(
    "extra",
    [
        "",
        ".bak",
        "docs/a",
        "data/results/a",
        "data/replay/a",
        "data/manifest/costs_grading.jsonl",
    ],
)
def test_ledger_only_skips(tmp_path, extra):
    import shutil

    config = tmp_path / "scripts/exempt_paths.json"
    config.parent.mkdir()
    shutil.copyfile(HOOK.parents[1] / "exempt_paths.json", config)
    ledger = json.loads(config.read_text())["ledger_paths"][0]
    (tmp_path / "Makefile").write_text("verify:\n\ttouch ran\n\texit 1\n")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    path = tmp_path / ledger
    path.parent.mkdir(parents=True)
    path.write_text("")
    subprocess.run(
        ["git", "add", "Makefile", "scripts", ledger], cwd=tmp_path, check=True
    )
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        cwd=tmp_path,
        check=True,
    )
    path = tmp_path / ledger
    path.write_text("{}\n")
    if extra:
        other = tmp_path / (ledger + extra if extra == ".bak" else extra)
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("synthetic")
    result = run_hook(tmp_path)
    assert result.returncode == (2 if extra else 0)
    assert (tmp_path / "ran").exists() == bool(extra)


@pytest.mark.parametrize("config", ["missing", "broken", "wrong_type"])
def test_invalid_exempt_config_verifies(project, config):
    target = project / "scripts/exempt_paths.json"
    target.parent.mkdir()
    if config != "missing":
        target.write_text("{" if config == "broken" else '{"ledger_paths":"x"}')
    result = run_hook(project)
    assert result.returncode == 2 and (project / "ran").exists()


def test_missing_jq_verifies(project):
    stub = project / "bin"
    stub.mkdir()
    jq = stub / "jq"
    jq.write_text("#!/bin/sh\nexit 127\n")
    jq.chmod(0o755)
    result = run_hook(project, {"PATH": f"{stub}:{os.environ['PATH']}"})
    assert result.returncode == 2 and (project / "ran").exists()

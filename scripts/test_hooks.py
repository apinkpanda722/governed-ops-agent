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


def run_hook(project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps({"cwd": str(project)}),
        capture_output=True,
        text=True,
        check=False,
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
    # 잠금 PID가 이 훅의 조상(테스트 프로세스)이면 step을 실행하는 Codex 세션이므로
    # 계속 검증해 스스로 고치게 한다.
    write_lock(project, os.getpid())
    result = run_hook(project)
    assert result.returncode == 2 and (project / "ran").exists()


def test_garbage_lock_verifies(project):
    (project / ".harness" / "executing").write_text("not-a-pid\n")
    result = run_hook(project)
    assert result.returncode == 2 and (project / "ran").exists()

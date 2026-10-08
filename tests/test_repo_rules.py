"""비밀 값 보관·비용 설정·커밋 가능한 디렉토리의 저장소 규칙."""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_env_example_has_only_empty_assignments() -> None:
    invalid_lines = [
        number
        for number, line in enumerate(
            (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines(),
            start=1,
        )
        if line.strip()
        and not line.lstrip().startswith("#")
        and re.fullmatch(r"[A-Z][A-Z0-9_]*=", line) is None
    ]
    # 실패 메시지에는 줄 번호만 남기고 변수 값은 노출하지 않는다.
    assert invalid_lines == [], f".env.example 형식 위반 줄 번호: {invalid_lines}"


def test_env_example_has_no_cost_limit_variables() -> None:
    exceptions = {"GOVOPS_EXPLORATION_BUDGET"}
    assignments = {
        line.partition("=")[0]: line.partition("=")[2]
        for line in (REPO_ROOT / ".env.example")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {name: assignments[name] for name in exceptions} == {
        "GOVOPS_EXPLORATION_BUDGET": ""
    }
    invalid_lines = [
        number
        for number, line in enumerate(
            (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines(),
            start=1,
        )
        if line.strip()
        and not line.lstrip().startswith("#")
        and line.partition("=")[0] not in exceptions
        and re.search(r"COST|BUDGET|LIMIT|CAP", line.partition("=")[0])
    ]
    assert invalid_lines == [], (
        f"ADR-007: 비용 상한은 코드 설정으로 둔다. 위반 줄 번호: {invalid_lines}"
    )


@pytest.mark.parametrize(
    ("path", "expected_code"),
    [
        (".env", 0),
        ("data/raw/x", 0),
        ("data/interim/x", 0),
        ("data/interim/redteam_review/cards.html", 0),
        ("data/interim/eval/cards/index.html", 0),
        ("web/node_modules", 0),
        ("web/test-results/example", 0),
        ("web/playwright-report/example", 0),
        ("web/AGENTS.md", 0),
        ("web/CLAUDE.md", 0),
        (".venv", 0),
        (".env.example", 1),
    ],
)
def test_git_ignore_rules(path: str, expected_code: int) -> None:
    # .env와 비밀 데이터는 열지 않고 경로의 ignore 여부만 확인한다.
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == expected_code, f"gitignore 규칙 위반: {path}"


@pytest.mark.parametrize(
    "path",
    ["data/contract", "data/seed", "data/replay", "data/eval", "docs/results"],
)
def test_committable_directory_skeleton_exists(path: str) -> None:
    assert (REPO_ROOT / path).is_dir(), f"디렉토리 골격 누락: {path}"

"""Offline integrity checks for the M3 preregistration bundle."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import pytest

from govops.evalrun.dev_m3 import FIRST_REGISTRATION_FILES
from govops.evalrun.observe import STATUS_MAP
from govops.golden import GOLDEN_TASKS, golden_expectations, load_golden_specs

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "8c18df863b2949f58d9c3550fec3b8cf"


def test_registration_paths():
    assert FIRST_REGISTRATION_FILES == (
        "docs/results/hypotheses-m3.md",
        "data/replay/golden/expectations.json",
        "docs/results/m3-dev-supersedes.json",
    )
    assert all((ROOT / path).is_file() for path in FIRST_REGISTRATION_FILES)


def test_frozen_expectations():
    path = ROOT / FIRST_REGISTRATION_FILES[1]
    text = path.read_text()
    expected = golden_expectations(load_golden_specs().values())
    assert len(expected) == 14
    assert json.loads(text) == expected
    assert (
        text
        == json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    original_commit = subprocess.check_output(
        ["git", "log", "--diff-filter=A", "-1", "--format=%H", "--", str(path)],
        cwd=ROOT,
        text=True,
    ).strip()
    old = json.loads(
        subprocess.check_output(
            ["git", "show", f"{original_commit}:data/replay/golden/expectations.json"],
            cwd=ROOT,
        )
    )
    # The first seven IDs are the original read-only contract.
    assert {key: expected[key] for key in GOLDEN_TASKS[:7]} == {
        key: old[key] for key in GOLDEN_TASKS[:7]
    }


def test_superseded_run_evidence():
    row = json.loads((ROOT / FIRST_REGISTRATION_FILES[2]).read_text())
    assert row["previous_run_id"] == RUN_ID
    directory = ROOT / "data/results/dev" / RUN_ID
    assert directory.is_dir()
    names = ("manifest.json", "grades.json", "observations.jsonl")
    report = "docs/results/2026-10-05-m2-dev-live.md"
    assert set(row["previous_files"]) == {*names, report}
    for name, digest in row["previous_files"].items():
        path = ROOT / name if name == report else directory / name
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
        commit = subprocess.check_output(
            ["git", "log", "--diff-filter=A", "-1", "--format=%H", "--", str(path)],
            cwd=ROOT,
            text=True,
        ).strip()
        if name != report:
            assert row["previous_commit"] == commit
    assert "WITH_WRITES" in row["reason"]


@pytest.mark.parametrize(
    ("number", "keywords"),
    [
        (
            1,
            (
                "20",
                "WITH_WRITES",
                "READ_ONLY",
                "m3-dev-supersedes.json",
                "고정 문장 5",
                "§10",
            ),
        ),
        (2, ("프롬프트", "도구 정의", "dev 전체", "재실행")),
        (
            3,
            (
                "14",
                "expectations.json",
                "같은 커밋",
                "record-through",
                "비합성",
                "recorded_fail",
            ),
        ),
        (
            4,
            (
                "STATUS_MAP",
                "awaiting_approval",
                "failed",
                "approval_rejected",
                "reject_not_recorded",
                "2xx",
            ),
        ),
        (
            5,
            (
                "same_team_g4",
                "4급",
                "label",
                "사전순",
                "demo-legal-g4",
                "demo-platform-g4",
                "requester",
            ),
        ),
        (6, ("중복", "누락", "미승인", "마커 없음", "task별")),
        (
            7,
            (
                "V15",
                "approved_executed",
                "scope_wider_than_evidence",
                "evidence_scope_unknown",
                "1건",
                "run_sql",
                "TCR 실패",
            ),
        ),
        (
            8,
            (
                "preflight",
                "stale_mock_image",
                "model_info.id",
                "litellm_config_stale",
                "service_down:",
                "A MCP",
            ),
        ),
        (9, ("Postgres", "GOVOPS_DATABASE_URL", "postgres_required", "테스트 전용")),
        (10, ("models_seen", "TCR", "장애 주입", "unmapped_models")),
        (11, ("in-process ASGI", "서버 프로세스", "세션 TTL", "수백 건", "M4")),
        (12, ("§10", "1회", "예산 중단", "선택적 재실행")),
        (
            13,
            (
                "tool_trace_mismatch",
                "checkpoint",
                "N 계산",
                "첫머리",
                "n_registration_blocked",
                "task ID별",
                "늦은 호출",
            ),
        ),
        (
            14,
            (
                "F1~F11",
                "hypotheses-m3-faults.md",
                "목표 수치",
                "dev 실행 뒤",
                "test 전",
            ),
        ),
    ],
)
def test_registration_topics(number, keywords):
    text = (ROOT / FIRST_REGISTRATION_FILES[0]).read_text()
    sections = dict(
        re.findall(
            r"^## (\d+)\.[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
        )
    )
    assert all(keyword in sections[str(number)] for keyword in keywords)
    if number == 3:
        assert all(task in sections["3"] for task in GOLDEN_TASKS)
    if number == 4:
        for source, target in STATUS_MAP.items():
            assert f"| `{source}` | `{target}` |" in sections["4"]
    assert "작성 시점: dev 실행 전" in text
    assert "결과를 본 뒤 바꾸지 않는다" in text


def test_prompt_registration_paths():
    from govops.evalrun.dev_m3 import PROMPT_REGISTRATION_FILES

    assert PROMPT_REGISTRATION_FILES == (
        "docs/results/hypotheses-m3-prompt.md",
        "docs/results/m3-dev2-supersedes.json",
    )
    assert all((ROOT / path).is_file() for path in PROMPT_REGISTRATION_FILES)


def test_prompt_superseded_run_evidence():
    row = json.loads((ROOT / "docs/results/m3-dev2-supersedes.json").read_text())
    run_id = "da337bb7471c4bceae14a2e9b3ce5884"
    assert row["previous_run_id"] == run_id
    directory = ROOT / "data/results/dev" / run_id
    report = "docs/results/2026-10-06-m3-dev-live.md"
    assert set(row["previous_files"]) == {
        "manifest.json",
        "grades.json",
        "observations.jsonl",
        report,
    }
    for name, digest in row["previous_files"].items():
        path = ROOT / name if name == report else directory / name
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
        # CI uses fetch-depth: 0; the evidence's addition must exist in history.
        if name != report:
            commit = subprocess.check_output(
                ["git", "log", "--diff-filter=A", "-1", "--format=%H", "--", str(path)],
                cwd=ROOT,
                text=True,
            ).strip()
            assert commit and row["previous_commit"] == commit
            committed = subprocess.check_output(
                ["git", "show", f"{commit}:{path.relative_to(ROOT)}"], cwd=ROOT
            )
            assert committed == path.read_bytes()
    manifest = json.loads((directory / "manifest.json").read_text())
    grades = json.loads((directory / "grades.json").read_text())
    assert row["previous_official"]["grading_rules"] == manifest.get(
        "grading_rules", "registered"
    )
    passed = sum(grade["tcr"]["passed"] for grade in grades.values())
    assert row["previous_official"]["tcr"] == f"{passed}/{len(grades)}" == "17/20"
    assert all(word in row["reason"] for word in ("조직", "§10", "T4-06", "0행"))


@pytest.mark.parametrize(
    ("number", "keywords"),
    [
        (
            1,
            (
                "writes_org",
                "writes",
                "read_only",
                "조직 정보",
                "T4-06",
                "run_sql.v1.json",
                "tickets.json",
                "c34f0ece3d9e4fc081b01a2339d6adda.jsonl",
            ),
        ),
        (
            2,
            (
                "src/kodocs/synth/templates/system.md",
                "읽기 전용",
                "8행",
                "owner_team",
                "project_key",
                "status",
                "priority",
                "ticket_type",
                "data/eval/",
                "예시 SQL",
                "상수",
                "테스트",
            ),
        ),
        (3, ("test 문항을 실행하거나 결과를 보지 않았다", "먼저 커밋", "dev 전체")),
        (4, ("바꾼 횟수", "1회", "새 등록", "이전 결과 보존")),
        (
            5,
            (
                "20",
                "§10",
                "da337bb7471c4bceae14a2e9b3ce5884",
                "m3-dev2-supersedes.json",
                "registered",
                "17/20",
            ),
        ),
        (
            6,
            (
                "m3-grading",
                "grading_rules_sha256",
                "등록 커밋",
                "registered",
                "14개",
                "status.json",
                "expectations.json",
            ),
        ),
        (7, ("N 등록값", "writes_org", "tool_results", "ADR-014", "참고값")),
        (8, ("writes_org", "GOVOPS_AGENT_PROFILE=writes_org", "status.json")),
        (9, ("TCR", "정확도", "지연", "test 전", "별도로 등록")),
    ],
)
def test_prompt_registration_topics(number, keywords):
    text = (ROOT / "docs/results/hypotheses-m3-prompt.md").read_text()
    sections = dict(
        re.findall(
            r"^## (\d+)\.[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
        )
    )
    assert all(keyword in sections[str(number)] for keyword in keywords)
    assert "작성 시점: dev 두 번째 실행 전" in text
    assert "결과를 본 뒤 바꾸지 않는다" in text
    assert "같은 커밋" in text

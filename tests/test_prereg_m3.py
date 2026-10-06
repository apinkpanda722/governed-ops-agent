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

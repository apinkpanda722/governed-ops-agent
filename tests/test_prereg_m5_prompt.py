"""Offline integrity of the requester prompt preregistration."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_ID = "d2cd927c073447728c76c040d7bfb3bc"


def test_prompt_v2_supersedes_evidence():
    row = json.loads((ROOT / "docs/results/m5-dev5-supersedes.json").read_text())
    assert set(row) == {
        "previous_commit",
        "previous_files",
        "previous_official",
        "previous_run_id",
        "reason",
    }
    assert row["previous_run_id"] == RUN_ID
    assert row["previous_official"] == {"grading_rules": "m3-grading", "tcr": "17/20"}
    report = "docs/results/2026-10-08-m5-dev4-live.md"
    assert set(row["previous_files"]) == {
        report,
        "manifest.json",
        "grades.json",
        "observations.jsonl",
    }
    for name, digest in row["previous_files"].items():
        path = (
            ROOT / name if name == report else ROOT / "data/results/dev" / RUN_ID / name
        )
        assert path.is_file() and path.stat().st_size > 0
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
        committed = subprocess.check_output(
            ["git", "show", f"HEAD:{path.relative_to(ROOT)}"], cwd=ROOT, timeout=30
        )
        assert committed == path.read_bytes()
    subprocess.check_call(
        ["git", "cat-file", "-e", row["previous_commit"] + "^{commit}"],
        cwd=ROOT,
        timeout=30,
    )
    first = subprocess.check_output(
        [
            "git",
            "log",
            "--diff-filter=A",
            "-1",
            "--format=%H",
            "--",
            f"data/results/dev/{RUN_ID}/manifest.json",
        ],
        cwd=ROOT,
        text=True,
        timeout=30,
    ).strip()
    assert row["previous_commit"] == first
    assert all(word in row["reason"] for word in ("요청자 절", "dev 20", "처음부터"))


def test_prompt_v2_registration_topics_and_no_current_results():
    from govops.evalrun.dev_m3 import PROMPT_V2_REGISTRATION_FILES

    assert PROMPT_V2_REGISTRATION_FILES == (
        "docs/results/hypotheses-m5-prompt.md",
        "docs/results/m5-dev5-supersedes.json",
    )
    text = (ROOT / PROMPT_V2_REGISTRATION_FILES[0]).read_text()
    sections = dict(
        re.findall(
            r"^## (\d+)\.[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
        )
    )
    topics = {
        "1": ("SEND_RULES", "요청자 절", "사용자 화면", "원자료 파일이 없다"),
        "2": ("allowed_channels", "writes_org", "0a82447c", "정답 값", "직급"),
        "3": ("test", "데모 관찰"),
        "4": ("새 등록", "dev 전체 재실행", "2회", "hypotheses-m3-prompt.md"),
        "5": (RUN_ID, "보존", "17/20", "8c18df863b2949f58d9c3550fec3b8cf"),
        "6": (
            "m3-grading",
            "Guardrail v1",
            "MAX_COST",
            "N=4",
            "regrade --n",
            "참고",
            "최댓값 + 1",
            "별도 파일",
        ),
        "7": ("GOVOPS_AGENT_PROFILE=writes_team", "14개", "status.json", "writes_org"),
        "8": ("prompt_hash", "템플릿", "requester_section_sha256", "request_key"),
        "9": (
            "마지막 시도",
            "check_reason",
            "recipient_not_allowed",
            "초안 없이",
            "announce",
            "TCR",
            "정확도",
            "별도로 등록",
        ),
        "10": (
            "manifest.json",
            "observations.jsonl",
            "grades.json",
            "T5-11",
            "T4-06",
            "산출 불가",
            "reason_codes",
            "check.reason",
            "17건",
            "2건",
        ),
    }
    assert set(sections) == set(topics)
    for number, words in topics.items():
        assert all(word in sections[number] for word in words)
    assert "같은 커밋으로 등록한다(FP-043)" in text
    assert "결과를 본 뒤 바꾸지 않는다" in text
    assert "writes_team" in text and "writes_org" in text
    # Previous official values and measured baselines are explicitly scoped.
    assert "이전 공식" in sections["5"] and "이전 공식" in sections["9"]
    assert "이번 결과가 아님" in text
    for number, body in sections.items():
        if number != "10":
            assert not re.search(r"\d+(?:\.\d+)?\s*(?:%|ms|초)\b", body)
        assert not re.search(
            r"(?:이번 실행|dev5)의?\s*(?:TCR|정확도|지연)\s*[:=]?\s*\d", body
        )
        assert not re.search(r"(?:목표|목표치)\s*[:=]?\s*\d", body)

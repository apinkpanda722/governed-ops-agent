"""키 없이 실행하는 PR 검증 워크플로의 저장소 계약."""

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
ACTIONS = {
    "actions/checkout": ("3d3c42e5aac5ba805825da76410c181273ba90b1", "v7.0.1"),
    "astral-sh/setup-uv": ("c18668ad3cf93ea998bef934396af7bb5c839dc7", "v10.2.0"),
    "actions/setup-node": ("820762786026740c76f36085b0efc47a31fe5020", "v7.0.0"),
    "pnpm/action-setup": ("ea17c68df8912ef543352723c149a84f56e3d413", "v6.1.0"),
}


@pytest.fixture
def workflow():
    assert WORKFLOW.is_file()
    text = WORKFLOW.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert isinstance(parsed, dict)
    return text, parsed


def test_ci_yaml_triggers_permissions_and_single_job(workflow):
    _, parsed = workflow
    # PyYAML의 YAML 1.1에서는 따옴표 없는 on을 bool로 해석한다.
    triggers = parsed.get("on", parsed.get(True))
    assert triggers == {"pull_request": None, "push": {"branches": ["main"]}}
    assert parsed["permissions"] == {"contents": "read"}
    assert set(parsed["jobs"]) == {"verify"}
    assert parsed["jobs"]["verify"]["runs-on"] == "ubuntu-latest"


def test_ci_cancels_superseded_runs_of_the_same_pull_request(workflow):
    _, parsed = workflow
    assert parsed["concurrency"] == {
        "group": "ci-${{ github.event.pull_request.number || github.ref }}",
        "cancel-in-progress": True,
    }


def test_ci_actions_are_approved_sha_pins_with_tags(workflow):
    text, parsed = workflow
    steps = parsed["jobs"]["verify"]["steps"]
    uses = [step["uses"] for step in steps if "uses" in step]
    assert uses == [f"{action}@{sha}" for action, (sha, _) in ACTIONS.items()]
    for use in uses:
        assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", use)
        action = use.split("@")[0]
        assert re.search(
            rf"uses:\s*{re.escape(use)}\s+# {re.escape(ACTIONS[action][1])}$",
            text,
            re.MULTILINE,
        )
    assert len(re.findall(r"\buses:", text)) == len(uses)


def test_ci_tool_versions_follow_repository(workflow):
    _, parsed = workflow
    steps = parsed["jobs"]["verify"]["steps"]
    inputs = {
        step["uses"].split("@")[0]: step.get("with", {})
        for step in steps
        if "uses" in step
    }
    package = json.loads((REPO_ROOT / "web/package.json").read_text())
    assert inputs["astral-sh/setup-uv"] == {"version": "0.11.24"}
    assert (REPO_ROOT / ".python-version").read_text().strip() == "3.12"
    node = inputs["actions/setup-node"]["node-version"]
    assert node == (REPO_ROOT / "web/.nvmrc").read_text().strip() == "26.4.0"
    assert package["engines"]["node"] == f">={node} <27"
    assert inputs["pnpm/action-setup"] == {
        "version": package["packageManager"].split("@")[1],
        "run_install": False,
    }


def test_ci_has_no_credentials_or_environment_injection(workflow):
    text, _ = workflow
    assert not re.search(
        r"secrets\.|OPENAI|ANTHROPIC|GEMINI|LITELLM|LANGFUSE|API_KEY|\.env",
        text,
        re.IGNORECASE,
    )
    assert not re.search(r"\benv\s*:", text)


def test_ci_only_installs_dependencies_and_runs_verify(workflow):
    text, parsed = workflow
    steps = parsed["jobs"]["verify"]["steps"]
    commands = [step["run"] for step in steps if "run" in step]
    assert commands == [
        "uv sync --frozen",
        "pnpm -C web install --frozen-lockfile",
        "make verify",
    ]
    assert len(steps) == len(ACTIONS) + len(commands)
    assert not re.search(
        r"docker|compose|make serve|record|smoke|eval-run|eval-gemini", text
    )


def test_ci_checkout_fetches_full_history(workflow):
    _, parsed = workflow
    steps = parsed["jobs"]["verify"]["steps"]
    checkout = next(
        s for s in steps if s.get("uses", "").startswith("actions/checkout@")
    )
    # git 이력에 의존하는 테스트(`git log --diff-filter=A`)는 얕은 복제에서 다른 커밋을 돌려준다.
    assert checkout.get("with") == {"fetch-depth": 0}


def test_ci_verify_job_has_a_timeout(workflow):
    _, parsed = workflow
    # 테스트가 끝나지 않으면 job이 기본 6시간까지 매달린다(PR #63에서 13분 넘게 정지).
    assert parsed["jobs"]["verify"]["timeout-minutes"] == 20


def test_pytest_per_test_timeout_is_configured():
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    options = config["tool"]["pytest"]["ini_options"]
    # thread 방식은 멈춘 테스트의 스택을 출력하고 워커를 끝내 xdist가 테스트 이름을 보고하게 한다.
    assert options["timeout"] == 120
    assert options["timeout_method"] == "thread"
    dev = config["dependency-groups"]["dev"]
    assert any(item.startswith("pytest-timeout") for item in dev)

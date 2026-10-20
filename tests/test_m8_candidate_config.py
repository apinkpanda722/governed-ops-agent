"""M8 후보 설정의 등록 구조만 검사한다. 실제 라우팅은 R-3 smoke에서 확인한다."""

import hashlib
import re
from pathlib import Path

import pytest
import yaml

from govops.costs import CLASSIFIER_DEPLOYMENTS, DEPLOYMENT_MODELS
from govops.evalrun.preflight import required_deployments
from govops.gateway.agent_key import AGENT_KEY_MODELS

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_ROOT = REPO_ROOT / "infra/litellm"
CANDIDATE_PATH = CONFIG_ROOT / "config.candidate.yaml"
CANDIDATE_CONFIG_SHA256 = (
    "76b201d05748edb4bfd9652da3237411b5eb889904935d940a69ecd649ae561e"
)
CORRESPONDING_BASELINE = {
    "agent-primary": "agent-fallback",
    "agent-fallback": "agent-primary",
    "agent-primary-only": "agent-primary-only",
    "agent-primary-down": "agent-primary-down",
    "guardrail-v2": "guardrail-v2",
}


@pytest.fixture
def baseline():
    return yaml.safe_load((CONFIG_ROOT / "config.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def candidate():
    return yaml.safe_load(CANDIDATE_PATH.read_text(encoding="utf-8"))


def test_deployment_ids_and_preflight_requirements(baseline, candidate):
    baseline_ids = {row["model_info"]["id"] for row in baseline["model_list"]}
    candidate_ids = {row["model_info"]["id"] for row in candidate["model_list"]}
    assert candidate_ids == baseline_ids
    assert required_deployments() <= candidate_ids
    assert required_deployments(v2=True) <= candidate_ids
    # 순서도 고정해 집합 비교에서 숨겨지는 중복 ID를 거부한다.
    assert [row["model_info"]["id"] for row in candidate["model_list"]] == [
        "anthropic-claude-sonnet-5-5",
        "openai-gpt-6-luna",
        "openai-gpt-6-luna-only",
        "openai-gpt-6-luna-down",
        "anthropic-claude-haiku-5-5",
    ]


def test_alias_mapping_and_complete_entries_match_baseline(baseline, candidate):
    original = {row["model_name"]: row for row in baseline["model_list"]}
    models = {row["model_name"]: row for row in candidate["model_list"]}
    assert set(models) == set(original) == set(AGENT_KEY_MODELS)
    assert {name: row["model_info"]["id"] for name, row in models.items()} == {
        "agent-primary": "anthropic-claude-sonnet-5-5",
        "agent-fallback": "openai-gpt-6-luna",
        **{
            name: original[name]["model_info"]["id"]
            for name in ("agent-primary-only", "agent-primary-down", "guardrail-v2")
        },
    }
    # 전체 항목·순서를 대조해 이름 외의 키·값 변경과 중복 별칭도 거부한다.
    assert candidate["model_list"] == [
        {**original[source], "model_name": name}
        for name, source in CORRESPONDING_BASELINE.items()
    ]


def test_timeouts_and_retries(candidate):
    assert {
        row["model_name"]: row["litellm_params"]["timeout"]
        for row in candidate["model_list"]
    } == {
        "agent-primary": 30,
        "agent-fallback": 15,
        "agent-primary-only": 15,
        "agent-primary-down": 15,
        "guardrail-v2": 8,
    }
    assert {
        row["model_name"]: row["litellm_params"]["num_retries"]
        for row in candidate["model_list"]
    } == dict.fromkeys(CORRESPONDING_BASELINE, 0)


def test_router_and_shared_settings(baseline, candidate):
    assert candidate["router_settings"] == {
        "fallbacks": [{"agent-primary-down": ["agent-primary"]}]
    }
    assert candidate["general_settings"] == baseline["general_settings"]
    assert candidate["guardrails"] == baseline["guardrails"]
    assert set(candidate) == set(baseline)
    assert "litellm_settings" not in candidate


def test_credentials_are_only_environment_references(baseline, candidate):
    original = {row["model_name"]: row for row in baseline["model_list"]}
    for row in candidate["model_list"]:
        name = row["model_name"]
        params = row["litellm_params"]
        assert re.fullmatch(r"os\.environ/[A-Z][A-Z0-9_]*", params["api_key"])
        assert params == original[CORRESPONDING_BASELINE[name]]["litellm_params"]
    for key in ("master_key", "database_url"):
        assert re.fullmatch(
            r"os\.environ/[A-Z][A-Z0-9_]*", candidate["general_settings"][key]
        )
    text = CANDIDATE_PATH.read_text(encoding="utf-8")
    assert "sk-" not in text
    assert "Bearer " not in text


def test_cost_mapping_and_deliberately_unreachable_deployment(baseline, candidate):
    dead_id = "openai-gpt-6-luna-down"
    candidate_ids = {row["model_info"]["id"] for row in candidate["model_list"]}
    assert candidate_ids - {dead_id} <= (
        set(DEPLOYMENT_MODELS) | set(CLASSIFIER_DEPLOYMENTS)
    )
    assert dead_id not in DEPLOYMENT_MODELS
    assert dead_id not in CLASSIFIER_DEPLOYMENTS
    assert dead_id in required_deployments()
    original = {row["model_name"]: row for row in baseline["model_list"]}
    models = {row["model_name"]: row for row in candidate["model_list"]}
    assert original["agent-primary-down"]["model_info"]["id"] == dead_id
    assert models["agent-primary-down"]["model_info"]["id"] == dead_id
    assert (
        models["agent-primary-down"]["litellm_params"]
        == original["agent-primary-down"]["litellm_params"]
    )
    assert models["agent-primary-down"]["litellm_params"]["api_base"] == (
        "http://127.0.0.1:9"
    )


def test_candidate_digest_is_frozen():
    assert hashlib.sha256(CANDIDATE_PATH.read_bytes()).hexdigest() == (
        CANDIDATE_CONFIG_SHA256
    )


def test_registration_contains_candidate_structure():
    # 원 등록 5절의 agent-primary-reference 서술은 추가 등록 1 §3이 대체한다.
    registration = (
        REPO_ROOT / "docs/results/hypotheses-m8-regression-addendum-1.md"
    ).read_text(encoding="utf-8")
    structure = registration.split("## 3. 변경", 1)[1].split("## 4.", 1)[0]
    for value in (
        "agent-fallback",
        "agent-primary-reference",
        "openai-gpt-6-luna",
        "AGENT_KEY_MODELS",
    ):
        assert value in structure


def test_candidate_aliases_are_agent_key_models(candidate):
    # 러너 사전 점검은 에이전트 키로 /model/info 를 조회하므로 키 허용 모델 밖
    # 별칭의 배포는 보이지 않는다(추가 등록 1).
    assert {row["model_name"] for row in candidate["model_list"]} == set(
        AGENT_KEY_MODELS
    )

"""분류기 별칭·단가는 에이전트 응답 매핑과 분리한다."""

from pathlib import Path

import pytest
import yaml

from govops import costs
from govops.gateway.agent_key import AGENT_KEY_MODELS
from govops.obs import mask

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "infra/litellm"


@pytest.fixture(params=["config.yaml", "config.obs.yaml"])
def config(request):
    return yaml.safe_load((CONFIG_ROOT / request.param).read_text())


def test_classifier_alias_configuration(config):
    deployments = [
        row for row in config["model_list"] if row["model_name"] == "guardrail-v2"
    ]
    assert len(deployments) == 1
    assert deployments[0] == {
        "model_name": "guardrail-v2",
        "model_info": {"id": "anthropic-claude-haiku-5-5"},
        "litellm_params": {
            "model": "anthropic/claude-haiku-5-5",
            "api_key": "os.environ/ANTHROPIC_API_KEY",
            "timeout": 4,
            "num_retries": 0,
        },
    }
    assert config["model_list"][-1] == deployments[0]


def test_classifier_alias_has_no_fallback(config):
    for fallback in config["router_settings"]["fallbacks"]:
        assert "guardrail-v2" not in fallback
        for targets in fallback.values():
            assert "guardrail-v2" not in targets


def test_model_lists_match():
    configs = [
        yaml.safe_load((CONFIG_ROOT / name).read_text())
        for name in ("config.yaml", "config.obs.yaml")
    ]
    assert configs[0]["model_list"] == configs[1]["model_list"]


def test_key_models_match_aliases(config):
    assert "guardrail-v2" in AGENT_KEY_MODELS
    assert AGENT_KEY_MODELS[-1] == "guardrail-v2"
    assert set(AGENT_KEY_MODELS) == {row["model_name"] for row in config["model_list"]}


def test_classifier_mapping_is_separate_from_agent_models():
    assert dict(costs.CLASSIFIER_DEPLOYMENTS) == {
        "anthropic-claude-haiku-5-5": "claude-haiku-5-5"
    }
    assert set(costs.DEPLOYMENT_MODELS) == {
        "openai-gpt-6-luna",
        "openai-gpt-6-luna-only",
        "anthropic-claude-sonnet-5-5",
    }
    assert set(costs.REGISTERED_MODELS) == {"gpt-6-luna", "claude-sonnet-5-5"}
    assert costs.resolve_deployment("anthropic-claude-haiku-5-5").mapped is False
    assert costs.resolve_model("claude-haiku-5-5").mapped is False


def test_classifier_cost_usd():
    assert costs.classifier_cost_usd(
        input_tokens=1_000_000, output_tokens=1_000_000
    ) == pytest.approx(0.60)
    assert costs.classifier_cost_usd(input_tokens=0, output_tokens=0) == 0
    assert "anthropic.claude-haiku-5-5.cached_input" not in costs.PRICES


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
@pytest.mark.parametrize("value", [-1, 1.5, True, "1", None])
def test_classifier_cost_rejects_invalid_tokens(field, value):
    counts = {"input_tokens": 0, "output_tokens": 0, field: value}
    with pytest.raises(ValueError, match="token counts must be non-negative integers"):
        costs.classifier_cost_usd(**counts)


def test_classifier_deployment_identifier_is_preserved():
    assert "anthropic-claude-haiku-5-5" in mask.DEPLOYMENT_IDS

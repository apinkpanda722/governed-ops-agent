import json
import logging
from pathlib import Path

import httpx
import pytest

from govops.costs import CostLedger, cost_report, init_ledgers
from govops.gateway import smoke
from govops.gateway.hook_digest import compute_digests
from govops.gateway.hook_header import GuardrailInfo, format_header

DIGESTS = compute_digests(Path(__file__).resolve().parents[1])
HEADER = format_header(GuardrailInfo(DIGESTS.rules, DIGESTS.code, team="platform"))


@pytest.mark.parametrize("attempt_headers", [True, False])
@pytest.mark.parametrize(
    "down,backend,code",
    [(200, "down_alias", 0), (400, "none", 1), (500, "none", 1)],
)
def test_fallback(
    tmp_path, monkeypatch, capsys, caplog, down, backend, code, attempt_headers
):
    init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", "sentinel-smoke-key")
    caplog.set_level(logging.DEBUG)
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["tools"] == []
        assert body["max_completion_tokens"] == 16
        stage = "down_alias" if body["model"] == "agent-primary-down" else "normal"
        status = {"normal": 200, "down_alias": down}[stage]
        if status != 200:
            return httpx.Response(status, text="Authorization sentinel-smoke-key")
        return httpx.Response(
            200,
            headers={
                "x-govops-guardrail": HEADER,
                "x-litellm-model-id": "openai-gpt-6-luna"
                if stage == "normal"
                else "anthropic-claude-sonnet-5-5",
                "x-litellm-model-group": "agent-primary"
                if stage == "normal"
                else "agent-fallback",
                **(
                    {"x-litellm-attempted-fallbacks": "0" if stage == "normal" else "1"}
                    if attempt_headers
                    else {}
                ),
            },
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "agent-primary" if stage == "normal" else "agent-fallback",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "secret-response"},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    assert (
        smoke.main(
            ["fallback"],
            transport=httpx.MockTransport(handle),
            ledger=CostLedger(tmp_path),
        )
        == code
    )
    out = capsys.readouterr().out
    assert "PASS normal family=openai mapped=true" in out
    assert out.endswith(f"BACKEND={backend}\n")
    assert len(seen) == 2
    assert (
        "INFO normal body_model=agent-primary model_id=openai-gpt-6-luna model_group=agent-primary"
        in out
    )
    assert (
        f"attempted_fallbacks={0 if attempt_headers else None} resolved=gpt-6-luna unmapped=false"
        in out
    )
    if down == 200:
        assert (
            f"attempted_fallbacks={1 if attempt_headers else None} resolved=claude-sonnet-5-5 unmapped=false"
            in out
        )
    total = sum(row.settled_usd for row in cost_report(tmp_path))
    assert f"INFO cost total_cents={total * 100:.6f}" in out
    delta = next(
        r.settled_usd for r in cost_report(tmp_path) if r.purpose == "agent_fallback"
    )
    assert ("PASS ledger agent_fallback_delta>0" in out) == (delta > 0)
    for value in [
        "sentinel-smoke-key",
        "Authorization",
        smoke.MESSAGE,
        "secret-response",
        "http://",
    ]:
        assert value not in out + caplog.text


@pytest.mark.parametrize("model", ["gpt-6-luna", "unknown-model"])
def test_unsupported_or_unmapped(tmp_path, monkeypatch, capsys, model):
    init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", "sentinel-smoke-key")

    def handle(request):
        return httpx.Response(
            200,
            headers={"x-govops-guardrail": HEADER},
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "synthetic"},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    assert (
        smoke.main(
            ["fallback"],
            transport=httpx.MockTransport(handle),
            ledger=CostLedger(tmp_path),
        )
        == 1
    )
    out = capsys.readouterr().out
    assert out.endswith("BACKEND=none\n")
    if model == "gpt-6-luna":
        assert (
            "FAIL down_alias reason=model_mismatch error=ModelCheckError status=model_mismatch"
            in out
        )


def test_missing_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", "sentinel-smoke-key")

    def handle(request):
        pytest.fail("preflight must prevent calls")

    assert (
        smoke.main(
            ["fallback"],
            transport=httpx.MockTransport(handle),
            ledger=CostLedger(tmp_path),
        )
        == 1
    )
    assert (
        capsys.readouterr().out
        == "FAIL ledger reason=ledger_delta error=LedgerMissingError status=error\nBACKEND=none\n"
    )


def test_connection_failures_are_safe(tmp_path, monkeypatch, capsys, caplog):
    init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", "sentinel-smoke-key")
    caplog.set_level(logging.DEBUG)

    def handle(request):
        raise httpx.ConnectError("Authorization sentinel-smoke-key", request=request)

    assert (
        smoke.main(
            ["fallback"],
            transport=httpx.MockTransport(handle),
            ledger=CostLedger(tmp_path),
        )
        == 1
    )
    out = capsys.readouterr().out
    for stage in ("normal", "down_alias"):
        assert f"FAIL {stage} reason=http error=GatewayError status=connection" in out
    assert out.endswith("BACKEND=none\n")
    assert "Authorization" not in out + caplog.text
    assert "sentinel-smoke-key" not in out + caplog.text


def test_missing_key_prevents_calls(tmp_path, monkeypatch, capsys):
    init_ledgers(tmp_path)
    monkeypatch.delenv("LITELLM_TEAM_KEY_PLATFORM", raising=False)

    def handle(request):
        pytest.fail("missing key must prevent calls")

    assert (
        smoke.main(
            ["fallback"],
            transport=httpx.MockTransport(handle),
            ledger=CostLedger(tmp_path),
        )
        == 1
    )
    assert capsys.readouterr().out == ("FAIL LITELLM_TEAM_KEY_PLATFORM\nBACKEND=none\n")


@pytest.mark.parametrize(
    "stage,mode,reason",
    [
        ("down_alias", "unmapped", "unmapped"),
        ("normal", "unmapped", "unmapped"),
        ("down_alias", "extra_charge", "ledger_delta"),
        ("normal", "extra_charge", "ledger_delta"),
        ("normal", "wrong_supplier", "model_mismatch"),
        ("down_alias", "wrong_supplier", "model_mismatch"),
        ("normal", "attempts", "model_mismatch"),
        ("down_alias", "attempts", "model_mismatch"),
    ],
)
def test_stage_checks(tmp_path, monkeypatch, capsys, stage, mode, reason):
    init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", "sentinel-smoke-key")

    def handle(request):
        current = (
            "down_alias"
            if json.loads(request.content)["model"] == "agent-primary-down"
            else "normal"
        )
        if current == stage and mode == "extra_charge":
            purpose = "agent_fallback" if current == "normal" else "agent_primary"
            model = "claude-sonnet-5-5" if current == "normal" else "gpt-6-luna"
            CostLedger(tmp_path).record(
                purpose=purpose,
                model=model,
                task_id="synthetic",
                input_tokens=1,
                output_tokens=0,
                cost_usd=0.00000001,
            )
        supplier = "openai" if current == "normal" else "anthropic"
        if current == stage and mode == "wrong_supplier":
            supplier = "anthropic" if supplier == "openai" else "openai"
        deployment = {
            "openai": "openai-gpt-6-luna",
            "anthropic": "anthropic-claude-sonnet-5-5",
        }[supplier]
        if current == stage and mode == "unmapped":
            deployment = "unknown-deployment"
        attempts = 0 if current == "normal" else 1
        if current == stage and mode == "attempts":
            attempts = 1 - attempts
        return httpx.Response(
            200,
            headers={
                "x-govops-guardrail": HEADER,
                "x-litellm-model-id": deployment,
                "x-litellm-model-group": "agent-primary"
                if current == "normal"
                else "agent-fallback",
                "x-litellm-attempted-fallbacks": str(attempts),
            },
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "agent-primary" if current == "normal" else "agent-fallback",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "secret-response"},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    assert (
        smoke.main(
            ["fallback"],
            transport=httpx.MockTransport(handle),
            ledger=CostLedger(tmp_path),
        )
        == 1
    )
    out = capsys.readouterr().out
    assert f"FAIL {stage} reason={reason}" in out
    assert f"INFO {stage} body_model=" in out
    assert out.endswith("BACKEND=none\n")
    for value in (
        "sentinel-smoke-key",
        "Authorization",
        "secret-response",
        smoke.MESSAGE,
        "http://",
    ):
        assert value not in out
    if stage == "down_alias" and mode == "unmapped":
        assert (
            next(
                r.settled_usd
                for r in cost_report(tmp_path)
                if r.purpose == "agent_fallback"
            )
            > 0
        )

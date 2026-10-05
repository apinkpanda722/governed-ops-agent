import json
import logging

import httpx
import pytest

from govops.costs import CostLedger, cost_report, init_ledgers
from govops.gateway import smoke


@pytest.mark.parametrize(
    "mock,down,backend,code",
    [
        (200, 200, "mock_param", 0),
        (400, 200, "down_alias", 0),
        (500, 500, "none", 1),
        (200, 500, "mock_param", 0),
    ],
)
def test_fallback(tmp_path, monkeypatch, capsys, caplog, mock, down, backend, code):
    init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_AGENT_KEY", "sentinel-smoke-key")
    caplog.set_level(logging.DEBUG)
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["tools"] == []
        assert body["max_completion_tokens"] == 16
        stage = (
            "down_alias"
            if body["model"] == "agent-primary-down"
            else "mock_param"
            if body.get("mock_testing_fallbacks")
            else "normal"
        )
        status = {"normal": 200, "mock_param": mock, "down_alias": down}[stage]
        if status != 200:
            return httpx.Response(status, text="Authorization sentinel-smoke-key")
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-6-luna" if stage == "normal" else "claude-sonnet-5-5",
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
    assert len(seen) == 3
    total = sum(row.settled_usd for row in cost_report(tmp_path))
    assert f"INFO cost total_cents={total * 100:.6f}" in out
    if mock == 400:
        assert "INFO mock_param unsupported status=400" in out
    if mock == 500:
        assert "FAIL mock_param error=GatewayError status=500" in out
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
    monkeypatch.setenv("LITELLM_AGENT_KEY", "sentinel-smoke-key")

    def handle(request):
        return httpx.Response(
            200,
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
        assert "INFO mock_param unsupported status=openai" in out
        assert "FAIL ledger error=LedgerCheckError status=no_increase" in out


def test_missing_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LITELLM_AGENT_KEY", "sentinel-smoke-key")

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
        == "FAIL ledger error=LedgerMissingError status=error\nBACKEND=none\n"
    )


def test_connection_failures_are_safe(tmp_path, monkeypatch, capsys, caplog):
    init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_AGENT_KEY", "sentinel-smoke-key")
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
    for stage in ("normal", "mock_param", "down_alias"):
        assert f"FAIL {stage} error=GatewayError status=connection" in out
    assert out.endswith("BACKEND=none\n")
    assert "Authorization" not in out + caplog.text
    assert "sentinel-smoke-key" not in out + caplog.text


def test_missing_key_prevents_calls(tmp_path, monkeypatch, capsys):
    init_ledgers(tmp_path)
    monkeypatch.delenv("LITELLM_AGENT_KEY", raising=False)

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
    assert capsys.readouterr().out == (
        "FAIL normal error=GatewayError status=missing_key\nBACKEND=none\n"
    )

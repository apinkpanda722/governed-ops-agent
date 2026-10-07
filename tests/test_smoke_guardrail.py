import asyncio
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from govops.gateway.hook_digest import compute_digests
from govops.gateway.hook_header import GuardrailInfo, format_header

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "name,code",
    [
        ("SMOKE_INJECTION", "injection_direct"),
        ("SMOKE_PII", "pii_input"),
        ("SMOKE_BENIGN", None),
    ],
)
def test_smoke_sentences(name, code):
    from govops.gateway import smoke_guardrail as smoke
    from govops.guardrails.screen import screen_request

    result = screen_request([{"role": "user", "content": getattr(smoke, name)}])
    assert result.verdict.code == code


def test_smoke_tool_sentence():
    from govops.gateway.smoke_guardrail import SMOKE_TOOL_INJECTION
    from govops.guardrails.screen import screen_request

    result = screen_request(
        [
            {
                "role": "tool",
                "tool_call_id": "call_smoke",
                "content": SMOKE_TOOL_INJECTION,
            }
        ]
    )
    assert tuple(item.tool_call_id for item in result.items if item.quarantined) == (
        "call_smoke",
    )


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("", {}),
        ("rules", {"hook_digests": "FAIL"}),
        ("code", {"hook_digests": "FAIL"}),
        ("alias", {"allowed_models": "FAIL"}),
        ("control", {"allowed_models": "FAIL"}),
        ("q", {"tool_screen": "FAIL"}),
        ("m", {"tool_screen": "FAIL"}),
        ("post", {"post_fail_open": "FAIL"}),
        ("obs_raw", {"langfuse_no_raw_input": "FAIL"}),
        ("obs_absent", {"langfuse_no_raw_input": "INCONCLUSIVE"}),
    ],
)
def test_smoke_signals(fault, expected, capsys):
    from govops.gateway.smoke_guardrail import run_smoke

    digests = compute_digests(ROOT)
    sentinel = []
    info = GuardrailInfo(
        digests.rules, digests.code, team="platform", pre="probe", c="probe"
    )

    def handler(request):
        if request.url.path == "/api/public/health":
            return httpx.Response(200)
        if request.url.path.startswith("/api/public/traces/"):
            if fault == "obs_absent":
                return httpx.Response(404)
            return httpx.Response(
                200, json={"input": sentinel if fault == "obs_raw" else "masked"}
            )
        body = json.loads(request.content)
        probe = body.get("metadata", {}).get("govops_probe")
        model = body["model"]
        text = body["messages"][0]["content"]
        if "GOVOPS_SMOKE_SENTINEL_" in text:
            sentinel.append(text)
        code = "probe"
        header = info
        status = 400
        error_type = "guardrail"
        if model.startswith("not-allowed-") and fault != "control":
            status, code, error_type = 403, "403", "key_model_access_denied"
        elif probe in {"slow", "raise"}:
            code, header = (
                "guardrail_unavailable",
                replace(info, pre="unavailable", c="guardrail_unavailable"),
            )
        elif probe != "digest":
            code = "pii_input" if "010-" in text else "injection_direct"
            header = replace(info, pre="block", c=code)
        elif fault == "alias" and model == "agent-fallback":
            code = "wrong"
        if fault == "rules":
            header = replace(header, rules="0" * 12)
        if fault == "code":
            header = replace(header, code="0" * 12)
        return httpx.Response(
            status,
            headers={"x-govops-guardrail": format_header(header)},
            json={
                "error": {
                    "type": error_type,
                    "provider_specific_fields": {"code": code},
                }
            },
        )

    class Chat:
        def __init__(self, **kwargs):
            self.fallback = kwargs.get("fault_mode") == "down_alias"

        async def complete(self, **kwargs):
            tool_ids = [
                m["tool_call_id"] for m in kwargs["messages"] if m["role"] == "tool"
            ]
            guardrail = replace(info, pre="allow", c="-", post="ok")
            if tool_ids:
                guardrail = replace(
                    guardrail,
                    q=0 if fault == "q" else 1,
                    m=0 if fault == "m" else 1,
                    ids=(tool_ids[0],) if fault != "q" else (),
                )
            if kwargs.get("guardrail_probe"):
                guardrail = replace(
                    guardrail, post="ok" if fault == "post" else "error"
                )
            return SimpleNamespace(
                guardrail=guardrail,
                attempted_fallbacks=int(self.fallback),
                fallback_used=self.fallback,
                model="claude-sonnet-5-5" if self.fallback else "gpt-6-luna",
            )

    async def no_sleep(_):
        pass

    env = lambda name, default="": {
        "SMOKE_USER": "demo-platform-g3",
        "LITELLM_TEAM_KEY_PLATFORM": "synthetic-test-key",
        "LANGFUSE_HOST": "http://obs.invalid",
        "LANGFUSE_PUBLIC_KEY": "synthetic-public",
        "LANGFUSE_SECRET_KEY": "synthetic-secret",
    }.get(name, default)

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await run_smoke(
                root=ROOT,
                http=http,
                chat_factory=Chat,
                env=env,
                sleep=no_sleep,
                obs_enabled=fault.startswith("obs_"),
                poll_attempts=2,
            )

    report = asyncio.run(invoke())
    for stage, result in expected.items():
        assert report["stages"][stage] == result
    if not fault.startswith("obs_"):
        assert report["stages"]["langfuse_no_raw_input"] == "SKIP"
    assert report["counts"]["PASS"] == sum(
        v == "PASS" for v in report["stages"].values()
    )
    assert report["exit_code"] == int("FAIL" in report["stages"].values())
    output = capsys.readouterr().out
    assert "synthetic-test-key" not in output
    assert "synthetic-secret" not in output
    assert "INCONCLUSIVE=" in output and "SKIP=" in output

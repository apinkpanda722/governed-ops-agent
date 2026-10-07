"""Counter-only mock contract and fake gateway; no live entry point runs."""

import asyncio
from functools import wraps
from types import SimpleNamespace

import pytest

from govops.costs import CostLedger, init_ledgers
from govops.evalrun import faults_smoke as smoke


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class Admin:
    def __init__(self, failure=None):
        self.failure = failure
        self.stats = {"jira": {}, "slack": {}}
        self.rules = []
        self.resets = 0

    async def reset(self):
        self.resets += 1
        if self.failure == "reset":
            raise RuntimeError("Authorization secret-key")
        self.stats = {"jira": {}, "slack": {}}
        self.rules = []

    async def faults_stats(self):
        return self.stats

    async def faults_set(self, service, rules):
        assert service == "mock-slack"
        self.rules = rules
        self.stats = {"jira": {}, "slack": {}}

    async def faults_clear(self):
        self.rules = []
        self.stats = {"jira": {}, "slack": {}}
        if self.failure == "clear":
            self.stats["slack"] = {
                "slack.channels": {"requests": 1, "applied_rules": 0}
            }

    async def read(self):
        row = self.stats["slack"].setdefault(
            "slack.channels", {"requests": 0, "applied_rules": 0}
        )
        row["requests"] += 1
        row["applied_rules"] += bool(self.rules) and self.failure != "apply"


class Gateway:
    def __init__(self):
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        assert kwargs["max_output_tokens"] == 16
        return SimpleNamespace(
            deployment_id="anthropic-claude-sonnet-5-5",
            model="claude-sonnet-5-5",
            attempted_fallbacks=1,
            unmapped_model=False,
        )


@run_async
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "reset",
        "apply",
        "clear",
        "lookups",
        "resends",
        "wait",
        "effects",
        "injections",
        "succeeded",
        "gateway",
    ],
)
async def test_stages(tmp_path, capsys, failure):
    init_ledgers(tmp_path)
    admin, gateway = Admin(failure), Gateway()

    async def preflight():
        return []

    async def effect():
        return {
            "lookups": 0 if failure == "lookups" else 2,
            "resends": int(failure == "resends"),
            "wait_seconds": 0 if failure == "wait" else 2,
            "effects": 2 if failure == "effects" else 1,
            "injections": 0 if failure == "injections" else 2,
            "succeeded": failure != "succeeded",
        }

    if failure == "gateway":
        gateway.complete = bad_gateway
    ticks = iter([10.0, 12.25])
    code = await smoke.run_smoke(
        admin=admin,
        preflight=preflight,
        read=admin.read,
        effect=effect,
        gateway=gateway,
        ledger=CostLedger(tmp_path),
        clock=lambda: next(ticks),
    )
    assert code == (0 if failure is None else 1)
    out = capsys.readouterr().out
    assert "남은 규칙은 reset으로 제거함(목록 조회 불가)" in out
    assert "chore:" in out and "data/manifest/costs.jsonl" in out
    assert "caffeinate -i make eval-faults" in out
    for secret in ("Authorization", "secret-key", "secret-response", smoke.MESSAGE):
        assert secret not in out
    if failure is None:
        assert "INFO duration_s=2.250" in out
        assert "model_id=anthropic-claude-sonnet-5-5 attempted_fallbacks=1" in out
        assert admin.resets == 3
        assert gateway.calls == 1
    elif failure != "gateway":
        assert gateway.calls == 0


async def bad_gateway(**kwargs):
    raise RuntimeError("Authorization secret-key secret-response")


@run_async
@pytest.mark.parametrize(
    "reason", ["stale_mock_image", "litellm_config_stale", "service_down:mock-slack"]
)
async def test_preflight_stops(tmp_path, capsys, reason):
    init_ledgers(tmp_path)
    admin, gateway = Admin(), Gateway()

    async def preflight():
        return [reason]

    async def unused():
        pytest.fail("must stop")

    assert (
        await smoke.run_smoke(
            admin=admin,
            preflight=preflight,
            read=unused,
            effect=unused,
            gateway=gateway,
            ledger=CostLedger(tmp_path),
        )
        == 1
    )
    assert f"FAIL environment reason={reason}" in capsys.readouterr().out
    assert admin.resets == gateway.calls == 0


@run_async
@pytest.mark.parametrize(
    "purpose,model,cost",
    [
        ("agent_primary", "gpt-6-luna", 9.13),
        ("agent_fallback", "claude-sonnet-5-5", 11.47),
    ],
)
async def test_budget_prevents_gateway(tmp_path, capsys, purpose, model, cost):
    init_ledgers(tmp_path)
    ledger = CostLedger(tmp_path)
    ledger.record(
        purpose=purpose,
        model=model,
        task_id="synthetic",
        input_tokens=1,
        output_tokens=0,
        cost_usd=cost,
    )
    gateway = Gateway()
    assert await smoke.measure_gateway(gateway, ledger) is None
    assert gateway.calls == 0
    assert "FAIL down_alias reason=budget_exceeded" in capsys.readouterr().out


@run_async
async def test_effect_probe_uses_approval_outbox_and_two_lookups(tmp_path, monkeypatch):
    # Slow state I/O must not suppress the real two-stage lookup wait.
    import asyncio
    import time

    from govops.tools.rest import ToolHttpError

    class Slack:
        def __init__(self):
            self.posts = 0
            self.lookups = 0
            self.applied = 0
            self.messages = []

        async def send_message(self, user, channel, text, *, metadata):
            assert user == smoke.USER
            self.posts += 1
            self.applied = time.monotonic()
            self.messages.append({"message_id": "syn-message-1", "metadata": metadata})
            raise ToolHttpError(0, "transport_error", kind="transport")

        async def channel_history(self, user, channel):
            self.lookups += 1
            return self.messages if time.monotonic() - self.applied >= 1 else []

    slack = Slack()

    class StateAdmin:
        async def faults_stats(self):
            await asyncio.sleep(0.02)
            return {
                "slack": {"slack.send": {"requests": slack.posts, "applied_rules": 2}}
            }

        async def state(self):
            await asyncio.sleep(0.02)
            return {"slack": {"channels": [{"messages": slack.messages}]}}

    result = await smoke.effect_probe(StateAdmin(), slack)
    assert result["succeeded"]
    assert result["lookups"] == slack.lookups == 2
    assert result["wait_seconds"] >= 1.9
    assert result["effects"] == 1
    assert result["resends"] == 0
    assert slack.posts == 1
    assert slack.messages[0]["metadata"]["govops_approval"]


@run_async
@pytest.mark.parametrize("failure", ["reset_stats", "still_applied", "cleanup"])
async def test_remaining_counter_and_cleanup_failures(tmp_path, capsys, failure):
    init_ledgers(tmp_path)

    class BrokenAdmin(Admin):
        async def reset(self):
            await super().reset()
            if failure == "reset_stats":
                self.stats["jira"] = {
                    "jira.ticket.get": {"requests": 1, "applied_rules": 0}
                }
            if failure == "cleanup" and self.resets == 2:
                raise RuntimeError("secret-key")

        async def read(self):
            await super().read()
            if failure == "still_applied" and not self.rules:
                self.stats["slack"]["slack.channels"]["applied_rules"] = 1

    admin = BrokenAdmin()
    gateway = Gateway()

    async def preflight():
        return []

    async def unused():
        pytest.fail("must stop before effect")

    assert (
        await smoke.run_smoke(
            admin=admin,
            preflight=preflight,
            read=admin.read,
            effect=unused,
            gateway=gateway,
            ledger=CostLedger(tmp_path),
        )
        == 1
    )
    assert gateway.calls == 0
    out = capsys.readouterr().out
    assert "FAIL" in out and "secret-key" not in out


@run_async
@pytest.mark.parametrize(
    "model_id,model,attempts,unmapped",
    [
        ("Authorization secret-key", "unknown", 1, True),
        ("anthropic-claude-sonnet-5-5", "gpt-6-luna", 1, False),
        ("anthropic-claude-sonnet-5-5", "claude-sonnet-5-5", 0, False),
        ("anthropic-claude-sonnet-5-5", "claude-sonnet-5-5", "secret-key", False),
    ],
)
async def test_gateway_metadata_validation(
    tmp_path, capsys, model_id, model, attempts, unmapped
):
    init_ledgers(tmp_path)

    class BadResult:
        async def complete(self, **kwargs):
            return SimpleNamespace(
                deployment_id=model_id,
                model=model,
                attempted_fallbacks=attempts,
                unmapped_model=unmapped,
            )

    assert await smoke.measure_gateway(BadResult(), CostLedger(tmp_path)) is None
    out = capsys.readouterr().out
    assert "FAIL down_alias reason=model_mismatch" in out
    assert "secret-key" not in out and "Authorization" not in out


@run_async
async def test_down_alias_client_records_both_supplier_reservations(
    tmp_path, monkeypatch, capsys
):
    import json

    import httpx

    from govops.gateway.client import LiteLLMChatClient
    from govops.gateway.faults import ModelFaultScript

    init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_AGENT_KEY", "sentinel-faults-smoke-key")
    calls = []

    def handle(request):
        body = json.loads(request.content)
        assert body["model"] == "agent-primary-down"
        calls.append(body)
        return httpx.Response(
            200,
            headers={
                "x-litellm-model-id": "anthropic-claude-sonnet-5-5",
                "x-litellm-attempted-fallbacks": "1",
            },
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "agent-fallback",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "secret-response"},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    ledger = CostLedger(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = LiteLLMChatClient(
            ledger=ledger,
            http_client=http,
            fault_mode="down_alias",
            fault_script=ModelFaultScript("all"),
        )
        assert await smoke.measure_gateway(client, ledger) is not None
    assert len(calls) == 1
    rows = [
        json.loads(line) for line in (tmp_path / "costs.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 4
    assert {r["purpose"] for r in rows if r.get("kind") == "reservation"} == {
        "agent_primary",
        "agent_fallback",
    }
    assert ledger.total_usd("openai") == 0
    assert ledger.total_usd("anthropic") > 0
    out = capsys.readouterr().out
    for value in (
        "sentinel-faults-smoke-key",
        "secret-response",
        "Authorization",
        smoke.MESSAGE,
    ):
        assert value not in out

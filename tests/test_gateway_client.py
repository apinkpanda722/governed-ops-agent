"""LiteLLM 호출 계약·비용·비밀 비노출을 mock transport로 검증한다."""

import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from govops import costs
from govops.gateway.client import (
    ChatClient,
    ChatResult,
    GatewayError,
    LiteLLMChatClient,
    Usage,
)

KEY = "synthetic-agent-secret"
MESSAGES = [{"role": "user", "content": "합성 티켓 요약"}]
TOOLS = [
    {
        "type": "function",
        "function": {"name": "lookup", "parameters": {"type": "object"}},
    }
]


def response(*, usage=True, calls=False):
    body = {
        "id": "synthetic-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-6-luna",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": None if calls else "합성 요약",
                },
            }
        ],
    }
    if calls:
        body["choices"][0]["message"]["tool_calls"] = [
            {
                "id": f"call-{i}",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"id":"syn-1"}'},
            }
            for i in range(2)
        ]
    if usage:
        body["usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "prompt_tokens_details": {"cached_tokens": 40},
        }
    return body


def run(tmp_path, handler, **kwargs):
    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteLLMChatClient(
                api_key=KEY,
                ledger=costs.CostLedger(tmp_path),
                http_client=http,
                **kwargs,
            )
            return await client.complete(
                messages=MESSAGES,
                tools=TOOLS,
                task_id="task-1",
                user_id="synthetic-user",
                max_output_tokens=128,
            )

    return asyncio.run(invoke())


@pytest.mark.parametrize("calls", [False, True])
def test_request_and_result(tmp_path, monkeypatch, calls, caplog):
    costs.init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_MASTER_KEY", "synthetic-master-secret")
    caplog.set_level(logging.DEBUG)
    seen = []

    def handler(request):
        seen.append(request)
        body = json.loads(request.content)
        assert request.url.path == "/chat/completions"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert body["model"] == "agent-primary"
        assert body["user"] == "synthetic-user"
        assert body["metadata"] == {"task_id": "task-1"}
        assert body["messages"] == MESSAGES
        assert "synthetic-user" not in json.dumps(body["messages"])
        assert body["tools"] == TOOLS
        assert body["max_completion_tokens"] == 128
        assert (
            "synthetic-master-secret"
            not in str(request.headers) + request.content.decode()
        )
        return httpx.Response(
            200, json=response(calls=calls), headers={"x-litellm-response-cost": "999"}
        )

    result = run(tmp_path, handler)
    assert len(seen) == 1
    assert result.usage == Usage(100, 40, 20)
    assert result.model == "gpt-6-luna"
    assert result.message == {
        "role": "assistant",
        "content": None if calls else "합성 요약",
        "tool_calls": [
            {"id": f"call-{i}", "name": "lookup", "arguments": '{"id":"syn-1"}'}
            for i in range(2)
        ]
        if calls
        else [],
    }
    expected = costs.token_cost_usd(
        "gpt-6-luna", input_tokens=100, cached_input_tokens=40, output_tokens=20
    )
    assert result.cost_usd == expected
    rows = (tmp_path / "costs.jsonl").read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["purpose"] == "agent_primary"
    assert row["model"] == "gpt-6-luna"
    assert row["task_id"] == "task-1"
    assert row["cost_usd"] == expected
    assert KEY not in caplog.text + rows[0]
    assert MESSAGES[0]["content"] not in caplog.text
    assert "LITELLM_MASTER_KEY" not in Path("src/govops/gateway/client.py").read_text()


def test_missing_usage_records_estimate(tmp_path):
    costs.init_ledgers(tmp_path)
    estimates = []
    ledger_check = costs.CostLedger.check_before_call

    def check(self, **kwargs):
        estimates.append(kwargs["estimate_usd"])
        ledger_check(self, **kwargs)

    from unittest.mock import patch

    with patch.object(costs.CostLedger, "check_before_call", check):
        result = run(
            tmp_path, lambda _: httpx.Response(200, json=response(usage=False))
        )
    assert result.cost_usd == estimates[0] > 0
    row = json.loads((tmp_path / "costs.jsonl").read_text())
    assert row["cost_usd"] == result.cost_usd
    assert (
        costs.token_cost_usd(
            "gpt-6-luna",
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
        )
        == result.cost_usd
    )


@pytest.mark.parametrize("missing", [False, True])
def test_budget_blocks_network(tmp_path, missing):
    if not missing:
        costs.init_ledgers(tmp_path)
        costs.CostLedger(tmp_path).record(
            purpose="agent_primary",
            model="gpt-6-luna",
            task_id="old",
            input_tokens=0,
            output_tokens=0,
            cost_usd=9.129999,
        )
    seen = []
    with pytest.raises(
        costs.LedgerMissingError if missing else costs.BudgetExceededError
    ):
        run(tmp_path, lambda request: seen.append(request))
    assert seen == []


@pytest.mark.parametrize("failure", [401, 429, 500, "connection", "timeout"])
def test_safe_errors_no_retry_or_record(tmp_path, failure, caplog):
    costs.init_ledgers(tmp_path)
    caplog.set_level(logging.DEBUG)
    for name in ("openai", "httpx", "httpcore"):
        caplog.set_level(logging.DEBUG, logger=name)
    seen = []

    def handler(request):
        seen.append(request)
        if failure == "connection":
            raise httpx.ConnectError(KEY, request=request)
        if failure == "timeout":
            raise httpx.ReadTimeout(KEY, request=request)
        return httpx.Response(
            failure, json={"error": {"message": KEY}}, headers={"x-request-id": KEY}
        )

    with pytest.raises(GatewayError) as caught:
        run(tmp_path, handler)
    assert len(seen) == 1
    assert (
        KEY
        not in str(caught.value)
        + repr(caught.value)
        + repr(caught.value.args)
        + caplog.text
    )
    assert caught.value.__cause__ is None
    assert (tmp_path / "costs.jsonl").read_text() == ""


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_key(tmp_path, monkeypatch, key):
    monkeypatch.setenv("LITELLM_AGENT_KEY", key or "")
    seen = []

    async def invoke():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: seen.append(r))
        ) as http:
            client = LiteLLMChatClient(
                http_client=http, ledger=costs.CostLedger(tmp_path)
            )
            await client.complete(messages=MESSAGES, tools=[], task_id="t", user_id="u")

    with pytest.raises(GatewayError, match="LITELLM_AGENT_KEY가 설정되지 않았습니다"):
        asyncio.run(invoke())
    assert seen == []


def test_protocol_fake():
    class Fake:
        async def complete(
            self, *, messages, tools, task_id, user_id, max_output_tokens=1024
        ):
            return ChatResult(
                {"role": "assistant", "content": "합성", "tool_calls": []},
                "synthetic-model",
                Usage(0, 0, 0),
                0,
            )

    client: ChatClient = Fake()
    assert (
        asyncio.run(
            client.complete(messages=[], tools=[], task_id="t", user_id="u")
        ).cost_usd
        == 0
    )


def test_environment_defaults(tmp_path, monkeypatch):
    costs.init_ledgers(tmp_path)
    monkeypatch.setenv("LITELLM_AGENT_KEY", KEY)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://synthetic-gateway:4000")
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "synthetic-gateway"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(200, json=response())

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteLLMChatClient(
                ledger=costs.CostLedger(tmp_path), http_client=http
            )
            await client.complete(messages=MESSAGES, tools=[], task_id="t", user_id="u")

    asyncio.run(invoke())
    assert len(seen) == 1


@pytest.mark.parametrize("missing", ["prompt_tokens", "completion_tokens"])
def test_partial_usage_uses_estimate(tmp_path, missing):
    costs.init_ledgers(tmp_path)
    body = response()
    del body["usage"][missing]
    result = run(tmp_path, lambda _: httpx.Response(200, json=body))
    assert result.usage.output_tokens == 128
    assert result.usage.cached_input_tokens == 0
    assert result.usage.input_tokens > 100
    assert result.cost_usd == costs.estimate_call_usd(
        "gpt-6-luna",
        input_tokens_upper=result.usage.input_tokens,
        max_output_tokens=128,
    )
    assert (
        json.loads((tmp_path / "costs.jsonl").read_text())["cost_usd"]
        == result.cost_usd
    )

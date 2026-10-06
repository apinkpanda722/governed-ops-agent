"""LiteLLM 호출 계약·비용·비밀 비노출을 mock transport로 검증한다."""

import asyncio
import json
import logging
import re
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


def ledger_rows(tmp_path):
    return [
        json.loads(line) for line in (tmp_path / "costs.jsonl").read_text().splitlines()
    ]


def settled_rows(tmp_path):
    return [row for row in ledger_rows(tmp_path) if row.get("kind") != "reservation"]


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
        metadata = dict(body["metadata"])
        # generation_id는 LiteLLM이 만드는 긴 ID 대신 우리가 정하는 짧은 hex다.
        assert re.fullmatch(r"[0-9a-f]{16}", metadata.pop("generation_id"))
        assert metadata == {
            "task_id": "task-1",
            "trace_user_id": "synthetic-user",
            "generation_name": "agent-primary",
            "tags": ["govops"],
        }
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
    # 호출 직전 예약 행 + 호출 뒤 정산 행(같은 예약 ID)
    assert len(rows) == 4
    reservation, row = (
        json.loads(r) for r in rows if json.loads(r)["purpose"] == "agent_primary"
    )
    assert reservation["kind"] == "reservation"
    assert row["reservation_id"] == reservation["reservation_id"]
    assert row["purpose"] == "agent_primary"
    assert row["model"] == "gpt-6-luna"
    assert row["task_id"] == "task-1"
    assert row["cost_usd"] == expected
    assert KEY not in caplog.text + "".join(rows)
    assert MESSAGES[0]["content"] not in caplog.text
    assert "LITELLM_MASTER_KEY" not in Path("src/govops/gateway/client.py").read_text()


def test_missing_usage_records_estimate(tmp_path):
    costs.init_ledgers(tmp_path)
    estimates = []
    reserve = costs.CostLedger.reserve

    def spy(self, **kwargs):
        estimates.append(kwargs["estimate_usd"])
        return reserve(self, **kwargs)

    from unittest.mock import patch

    with patch.object(costs.CostLedger, "reserve", spy):
        result = run(
            tmp_path, lambda _: httpx.Response(200, json=response(usage=False))
        )
    assert result.cost_usd == estimates[0] > 0
    row = settled_rows(tmp_path)[0]
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
def test_safe_errors_no_retry_and_settle_zero(tmp_path, failure, caplog):
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
    # 실패해도 같은 예약 ID로 0원 정산 행이 남고 미정산 예약이 없다.
    reservation, settlement = [
        r for r in ledger_rows(tmp_path) if r["purpose"] == "agent_primary"
    ]
    assert reservation["kind"] == "reservation"
    assert settlement["reservation_id"] == reservation["reservation_id"]
    assert settlement["cost_usd"] == 0
    assert len(settled_rows(tmp_path)) == 2
    assert all(row["cost_usd"] == 0 for row in settled_rows(tmp_path))
    assert costs.CostLedger(tmp_path).committed_usd("anthropic") == 0
    assert costs.CostLedger(tmp_path).committed_usd("openai") == 0


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
    assert settled_rows(tmp_path)[0]["cost_usd"] == result.cost_usd


def test_ledger_io_runs_off_the_event_loop_thread(tmp_path):
    import threading
    from unittest.mock import patch

    costs.init_ledgers(tmp_path)
    threads = {}
    reserve = costs.CostLedger.reserve
    settle = costs.CostLedger.settle

    def spy_reserve(self, **kwargs):
        threads["reserve"] = threading.current_thread()
        return reserve(self, **kwargs)

    def spy_settle(self, *args, **kwargs):
        threads["settle"] = threading.current_thread()
        return settle(self, *args, **kwargs)

    with (
        patch.object(costs.CostLedger, "reserve", spy_reserve),
        patch.object(costs.CostLedger, "settle", spy_settle),
    ):
        run(tmp_path, lambda _: httpx.Response(200, json=response()))
    assert threads["reserve"] is not threading.main_thread()
    assert threads["settle"] is not threading.main_thread()


def test_empty_choices_settles_actual_usage_then_fails_safely(tmp_path):
    costs.init_ledgers(tmp_path)
    body = response()
    body["choices"] = []
    with pytest.raises(GatewayError) as caught:
        run(tmp_path, lambda _: httpx.Response(200, json=body))
    assert KEY not in str(caught.value) + repr(caught.value)
    reservation, settlement = [
        r for r in ledger_rows(tmp_path) if r["purpose"] == "agent_primary"
    ]
    assert settlement["reservation_id"] == reservation["reservation_id"]
    assert settlement["cost_usd"] == costs.token_cost_usd(
        "gpt-6-luna", input_tokens=100, cached_input_tokens=40, output_tokens=20
    )
    assert costs.CostLedger(tmp_path).committed_usd("openai") == settlement["cost_usd"]


def test_concurrent_calls_near_the_cap_cannot_exceed_it(tmp_path):
    costs.init_ledgers(tmp_path)
    cap = costs.SUPPLIER_CAPS_USD["openai"]
    estimate = costs.estimate_call_usd(
        "gpt-6-luna", input_tokens_upper=2 * 400 + 1024, max_output_tokens=128
    )
    # 예약 가능한 호출이 정확히 2개이도록 잔여 상한을 맞춘다.
    costs.CostLedger(tmp_path).record(
        purpose="agent_primary",
        model="gpt-6-luna",
        task_id="old",
        input_tokens=0,
        output_tokens=0,
        cost_usd=round(cap - 2.5 * estimate, 10),
    )
    seen = []
    release = asyncio.Event()

    async def invoke():
        async def handler(request):
            seen.append(request)
            await release.wait()
            return httpx.Response(200, json=response())

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteLLMChatClient(
                api_key=KEY, ledger=costs.CostLedger(tmp_path), http_client=http
            )

            async def call(index):
                return await client.complete(
                    messages=MESSAGES,
                    tools=[],
                    task_id=f"t{index}",
                    user_id="u",
                    max_output_tokens=128,
                )

            tasks = [asyncio.create_task(call(i)) for i in range(8)]
            for _ in range(200):  # 요청이 모두 대기 상태가 될 때까지 양보
                await asyncio.sleep(0.01)
                if len(seen) + sum(task.done() for task in tasks) >= 8:
                    break
            committed_in_flight = costs.CostLedger(tmp_path).committed_usd("openai")
            release.set()
            return committed_in_flight, await asyncio.gather(
                *tasks, return_exceptions=True
            )

    in_flight, outcomes = asyncio.run(invoke())
    rejected = [o for o in outcomes if isinstance(o, costs.BudgetExceededError)]
    passed = [o for o in outcomes if isinstance(o, ChatResult)]
    assert len(passed) == 2 and len(rejected) == 6
    assert len(seen) == 2  # 거절된 호출은 네트워크 요청을 보내지 않았다
    assert in_flight <= cap + 1e-9  # 진행 중인 예약까지 합산해도 상한 이내


def test_valid_trace_metadata(tmp_path):
    costs.init_ledgers(tmp_path)

    def handler(request):
        body = json.loads(request.content)
        metadata = dict(body["metadata"])
        assert re.fullmatch(r"[0-9a-f]{16}", metadata.pop("generation_id"))
        assert metadata == {
            "task_id": "a" * 32,
            "existing_trace_id": "a" * 32,
            "trace_user_id": "synthetic-user",
            "generation_name": "agent-primary",
            "tags": ["govops"],
        }
        return httpx.Response(200, json=response())

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteLLMChatClient(
                base_url="http://gateway.test",
                api_key=KEY,
                ledger=costs.CostLedger(tmp_path),
                http_client=http,
            )
            await client.complete(
                messages=MESSAGES,
                tools=TOOLS,
                task_id="a" * 32,
                user_id="synthetic-user",
                max_output_tokens=128,
            )

    asyncio.run(invoke())


@pytest.mark.parametrize("task_id", ["a" * 32, "task-1", "A" * 32])
def test_body_and_sdk_metadata_match(tmp_path, monkeypatch, task_id):
    from unittest.mock import AsyncMock, Mock

    sdk = Mock()
    sdk.close = AsyncMock()
    raw = Mock()
    raw.headers = {}
    raw.parse = Mock(return_value=object())
    sdk.chat.completions.with_raw_response.create = AsyncMock(return_value=raw)
    monkeypatch.setattr("govops.gateway.client.AsyncOpenAI", Mock(return_value=sdk))
    client = LiteLLMChatClient(
        base_url="http://gateway.test",
        api_key=KEY,
        ledger=costs.CostLedger(tmp_path),
    )
    captured = []
    original = client._send

    async def send(body, **kwargs):
        captured.append(body)
        return await original(body, **kwargs)

    client._send = send
    costs.init_ledgers(tmp_path)
    # A sentinel response exercises transport only, then fails normal parsing.
    with pytest.raises(GatewayError):
        asyncio.run(
            client.complete(
                messages=MESSAGES,
                tools=TOOLS,
                task_id=task_id,
                user_id="synthetic-user",
            )
        )
    metadata = sdk.chat.completions.with_raw_response.create.call_args.kwargs[
        "extra_body"
    ]["metadata"]
    assert metadata == captured[0]["metadata"]
    assert metadata["task_id"] == task_id
    assert metadata["trace_user_id"] == "synthetic-user"
    assert metadata["tags"] == ["govops"]
    assert metadata["generation_name"] == "agent-primary"
    assert ("existing_trace_id" in metadata) == (task_id == "a" * 32)
    assert "trace_id" not in metadata and "parent_observation_id" not in metadata
    assert re.fullmatch(r"[0-9a-f]{16}", metadata["generation_id"])


def test_generation_id_is_short_unique_and_recorded_on_the_span(tmp_path):
    from govops.obs import trace as obs

    costs.init_ledgers(tmp_path)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["metadata"]["generation_id"])
        return httpx.Response(200, json=response())

    class Capture:
        def __init__(self):
            self.records = []

        def emit(self, record):
            self.records.append(record)

        def close_trace(self, trace_id):
            pass

    sink = Capture()

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteLLMChatClient(
                api_key=KEY,
                base_url="http://litellm.test",
                ledger=costs.CostLedger(tmp_path),
                http_client=http,
            )
            for _ in range(2):
                await client.complete(
                    messages=MESSAGES, tools=TOOLS, task_id="a" * 32, user_id="u"
                )

    with obs.trace("a" * 32, sinks=[sink]):
        asyncio.run(invoke())
    assert len(set(seen)) == 2 and all(re.fullmatch(r"[0-9a-f]{16}", g) for g in seen)
    root = sink.records[-1]
    assert root.attributes["generation_id"] == seen[-1]


def test_generation_is_attached_under_the_current_model_span(tmp_path):
    from govops.obs import trace as obs

    costs.init_ledgers(tmp_path)
    seen = {}

    def handler(request):
        seen["metadata"] = json.loads(request.content)["metadata"]
        return httpx.Response(200, json=response())

    class Sink:
        def emit(self, record):
            pass

        def close_trace(self, trace_id):
            pass

    async def invoke():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteLLMChatClient(
                api_key=KEY,
                base_url="http://litellm.test",
                ledger=costs.CostLedger(tmp_path),
                http_client=http,
            )
            return await client.complete(
                messages=MESSAGES, tools=TOOLS, task_id="a" * 32, user_id="u"
            )

    with (
        obs.trace("a" * 32, sinks=[Sink()]),
        obs.span(obs.SpanKind.MODEL_CALL, "agent-primary"),
    ):
        expected = obs.current_span_id()
        asyncio.run(invoke())
    assert seen["metadata"]["parent_observation_id"] == expected
    assert re.fullmatch(r"[0-9a-f]{16}", expected)
    assert "trace_id" not in seen["metadata"]


@pytest.mark.parametrize(
    "model",
    [
        "gpt-6-luna",
        "gpt-6-luna-20261001",
        "claude-sonnet-5-5",
        "claude-sonnet-5-5-20261001",
        "unknown-model",
    ],
)
def test_supplier_reservations_and_response_settlement(tmp_path, model):
    costs.init_ledgers(tmp_path)
    body = response()
    body["model"] = model
    result = run(tmp_path, lambda _: httpx.Response(200, json=body))
    expected, resolved = costs.response_cost_usd(
        model, input_tokens=100, cached_input_tokens=40, output_tokens=20
    )
    assert result.model == model
    assert result.cost_usd == expected
    assert result.fallback_used == (resolved.supplier == "anthropic")
    assert result.unmapped_model == (not resolved.mapped)
    rows = settled_rows(tmp_path)
    assert {r["purpose"] for r in rows} == {"agent_primary", "agent_fallback"}
    for row in rows:
        assert row["cost_usd"] == (
            expected
            if not resolved.mapped or row["supplier"] == resolved.supplier
            else 0
        )
    assert len(ledger_rows(tmp_path)) == 4


def test_anthropic_cap_disables_fallback(tmp_path):
    costs.init_ledgers(tmp_path)
    costs.CostLedger(tmp_path).record(
        purpose="agent_fallback",
        model="claude-sonnet-5-5",
        task_id="old",
        input_tokens=0,
        output_tokens=0,
        cost_usd=costs.SUPPLIER_CAPS_USD["anthropic"],
    )

    def handler(request):
        assert json.loads(request.content)["model"] == "agent-primary-only"
        return httpx.Response(200, json=response())

    run(tmp_path, handler)
    assert [
        r["purpose"] for r in ledger_rows(tmp_path) if r.get("kind") == "reservation"
    ] == ["agent_primary"]


def test_unmapped_model_attribute_on_model_span(tmp_path):
    from govops.obs import trace as obs

    class Sink:
        def __init__(self):
            self.records = []

        def emit(self, record):
            self.records.append(record)

        def close_trace(self, trace_id):
            pass

    costs.init_ledgers(tmp_path)
    body = response()
    body["model"] = "unknown-model"
    sink = Sink()
    with (
        obs.trace("a" * 32, sinks=[sink]),
        obs.span(obs.SpanKind.MODEL_CALL, "agent-primary"),
    ):
        run(tmp_path, lambda _: httpx.Response(200, json=body))
    assert any(
        record.attributes.get("unmapped_model") is True for record in sink.records
    )


@pytest.mark.parametrize("mode", ["mock_param", "down_alias"])
def test_fault_backend(tmp_path, mode):
    from govops.gateway.faults import ModelFaultScript

    costs.init_ledgers(tmp_path)
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        body = response()
        body["model"] = "claude-sonnet-5-5"
        return httpx.Response(200, json=body)

    script = ModelFaultScript({2})
    for _ in range(3):
        result = run(tmp_path, handler, fault_script=script, fault_mode=mode)
        assert result.fallback_used
    assert [body["model"] for body in seen] == (
        ["agent-primary", "agent-primary-down", "agent-primary"]
        if mode == "down_alias"
        else ["agent-primary"] * 3
    )
    assert [body.get("mock_testing_fallbacks") for body in seen] == (
        [None, True, None] if mode == "mock_param" else [None] * 3
    )
    for body in seen:
        body.pop("mock_testing_fallbacks", None)
        body["model"] = "agent-primary"
        body["metadata"].pop("generation_id")
    assert seen[0] == seen[1] == seen[2]


@pytest.mark.parametrize(
    "calls,expected",
    [({1}, [True, False, False]), ({2}, [False, True, False]), ("all", [True] * 3)],
)
def test_fault_script(calls, expected):
    from govops.gateway.faults import ModelFaultScript

    script = ModelFaultScript(calls)
    assert [script.should_fail("a") for _ in range(3)] == expected
    assert [script.should_fail("b") for _ in range(3)] == expected


@pytest.mark.parametrize("value", [None, "", "mock_param", "down_alias", "invalid"])
def test_fault_environment(monkeypatch, value):
    from govops.gateway.faults import model_fault_mode

    monkeypatch.delenv("GOVOPS_MODEL_FAULT_MODE", raising=False)
    if value is not None:
        monkeypatch.setenv("GOVOPS_MODEL_FAULT_MODE", value)
    if value == "invalid":
        with pytest.raises(ValueError):
            model_fault_mode()
    else:
        assert model_fault_mode() == (value or None)


@pytest.mark.parametrize("mode", ["mock_param", "down_alias"])
def test_fault_mode_without_script_is_normal(tmp_path, mode):
    costs.init_ledgers(tmp_path)

    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "agent-primary"
        assert "mock_testing_fallbacks" not in body
        return httpx.Response(200, json=response())

    run(tmp_path, handler, fault_mode=mode)


@pytest.mark.parametrize("mode", ["mock_param", "down_alias"])
def test_fault_respects_supplier_cap(tmp_path, mode):
    from govops.gateway.faults import ModelFaultScript

    costs.init_ledgers(tmp_path)
    costs.CostLedger(tmp_path).record(
        purpose="agent_fallback",
        model="claude-sonnet-5-5",
        task_id="old",
        input_tokens=0,
        output_tokens=0,
        cost_usd=costs.SUPPLIER_CAPS_USD["anthropic"],
    )

    def handler(request):
        body = json.loads(request.content)
        assert body["model"] == "agent-primary-only"
        assert "mock_testing_fallbacks" not in body
        return httpx.Response(200, json=response())

    run(tmp_path, handler, fault_script=ModelFaultScript("all"), fault_mode=mode)
    assert (
        costs.CostLedger(tmp_path).committed_usd("anthropic")
        == costs.SUPPLIER_CAPS_USD["anthropic"]
    )


@pytest.mark.parametrize(
    "deployment,alias,model,attempts",
    [
        ("openai-gpt-6-luna", "agent-primary", "gpt-6-luna", "0"),
        ("openai-gpt-6-luna-only", "agent-primary-only", "gpt-6-luna", "invalid"),
        ("anthropic-claude-sonnet-5-5", "agent-fallback", "claude-sonnet-5-5", "1"),
        ("unknown-deployment", "gpt-6-luna", "gpt-6-luna", "0"),
        ("openai-gpt-6-luna-down", "gpt-6-luna", "gpt-6-luna", "0"),
        ("", "gpt-6-luna", "gpt-6-luna", "1"),
    ],
)
def test_deployment_headers_override_body(
    tmp_path, monkeypatch, deployment, alias, model, attempts
):
    attributes = {}
    monkeypatch.setattr(
        "govops.gateway.client.set_attributes",
        lambda **values: attributes.update(values),
    )
    costs.init_ledgers(tmp_path)
    body = response()
    body["model"] = alias
    result = run(
        tmp_path,
        lambda _: httpx.Response(
            200,
            json=body,
            headers={
                "x-litellm-model-id": deployment,
                "x-litellm-model-group": alias,
                "x-litellm-attempted-fallbacks": attempts,
            },
        ),
    )
    resolved = costs.resolve_deployment(deployment)
    assert result.model == model
    assert result.body_model == alias
    assert result.deployment_id == deployment
    assert result.model_group == alias
    assert attributes["deployment_id"] == deployment
    assert attributes["model_group"] == alias
    assert attributes["unmapped_model"] == (not resolved.mapped)
    assert result.attempted_fallbacks == (
        None if attempts == "invalid" else int(attempts)
    )
    assert attributes["attempted_fallbacks"] == result.attempted_fallbacks
    assert result.unmapped_model == (not resolved.mapped)
    assert result.fallback_used == (resolved.supplier == "anthropic" or attempts == "1")
    expected, _ = costs.response_cost_usd(
        resolved.family or "unregistered",
        input_tokens=100,
        cached_input_tokens=40,
        output_tokens=20,
    )
    assert result.cost_usd == expected
    for row in settled_rows(tmp_path):
        assert row["cost_usd"] == (
            expected
            if not resolved.mapped or row["supplier"] == resolved.supplier
            else 0
        )


def test_deployment_configuration_ids():
    import yaml

    expected = dict(costs.DEPLOYMENT_MODELS)
    expected["openai-gpt-6-luna-down"] = "gpt-6-luna"
    configs = [
        yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "infra/litellm" / name).read_text()
        )
        for name in ("config.yaml", "config.obs.yaml")
    ]
    for config in configs:
        assert {row["model_info"]["id"] for row in config["model_list"]} == set(
            expected
        )
        for row in config["model_list"]:
            assert (
                expected[row["model_info"]["id"]]
                == row["litellm_params"]["model"].split("/")[1]
            )
    assert configs[0]["model_list"] == configs[1]["model_list"]

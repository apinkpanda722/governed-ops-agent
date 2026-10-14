"""Classifier transport, accounting and content-free failures with synthetic SDKs."""

import asyncio
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, OpenAIError

from govops import costs
from govops.gateway.client import ClassifyResult, LiteLLMChatClient
from govops.gateway.hook_digest import HookDigests
from govops.gateway.hook_header import GuardrailInfo, format_header
from govops.guardrails import semantic

KEY = "synthetic-classifier-key-sentinel"
TEXT = "합성 분류 대상 센티널 문장입니다."
ERROR_TEXT = "synthetic-exception-sentinel"
DEPLOYMENT = "anthropic-claude-haiku-5-5"
STATUSES = {"ok", "timeout", "rate_limited", "error", "parse", "v1_blocked"}


def request_for(kind="user"):
    return semantic.build_request(
        semantic.Item(kind, TEXT, None, (), False), semantic.load_config()
    )


def response(label="safe", *, finish_reason="stop"):
    return {
        "id": "synthetic-classifier-response",
        "object": "chat.completion",
        "created": 0,
        "model": "guardrail-v2",
        "choices": [
            {
                "index": 0,
                "finish_reason": finish_reason,
                "message": {
                    "role": "assistant",
                    "content": semantic.load_config()["labels"].get(label, label),
                },
            }
        ],
        "usage": {"prompt_tokens": 123, "completion_tokens": 4, "total_tokens": 127},
    }


def rows(root):
    return [
        json.loads(line) for line in (root / "costs.jsonl").read_text().splitlines()
    ]


def invoke(root, handler, *, request=None, **options):
    async def call():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = LiteLLMChatClient(
                api_key=KEY,
                base_url="http://classifier.invalid",
                ledger=costs.CostLedger(root),
                http_client=http,
                **options,
            )
            before = client.status_counts()
            result = await client.classify(
                request=request or request_for(), task_id="task", user_id="user"
            )
            after = client.status_counts()
            assert set(after) == STATUSES
            assert after == {
                status: count + int(status == result.status)
                for status, count in before.items()
            }
            after[result.status] = -100
            assert client.status_counts()[result.status] >= 1
            return result

    return asyncio.run(call())


@pytest.mark.parametrize(
    ("scenario", "status", "label", "charge"),
    [
        ("safe", "ok", "safe", "usage"),
        ("unsafe", "ok", "unsafe", "usage"),
        ("outside", "parse", None, "usage"),
        ("length", "parse", None, "usage"),
        (429, "rate_limited", None, "zero"),
        (500, "error", None, "estimate"),
        ("timeout", "timeout", None, "estimate"),
        ("connection", "error", None, "estimate"),
        ("injection_direct", "v1_blocked", None, "zero"),
        ("pii_input", "v1_blocked", None, "zero"),
        ("guardrail_unavailable", "error", None, "zero"),
        ("team_unknown", "error", None, "zero"),
        ("key_model_access_denied", "error", None, "zero"),
        ("wrong_deployment", "error", None, "usage"),
        ("missing_deployment", "error", None, "usage"),
        ("empty_choices", "error", None, "usage"),
        ("invalid_json", "error", None, "estimate"),
        ("invalid_content", "error", None, "usage"),
    ],
)
def test_status_mapping_and_settlement(
    tmp_path, caplog, scenario, status, label, charge
):
    costs.init_ledgers(tmp_path)
    caplog.set_level(logging.DEBUG)
    seen = []

    def handle(request):
        seen.append(request)
        logging.getLogger("openai._base_client").debug(
            "%s %s %s", KEY, TEXT, ERROR_TEXT
        )
        if scenario == "timeout":
            raise httpx.ReadTimeout(ERROR_TEXT, request=request)
        if scenario == "connection":
            raise httpx.ConnectError(ERROR_TEXT, request=request)
        if scenario in {429, 500} or scenario in {
            "injection_direct",
            "pii_input",
            "guardrail_unavailable",
            "team_unknown",
            "key_model_access_denied",
        }:
            code = scenario if isinstance(scenario, str) else "other"
            return httpx.Response(
                scenario
                if isinstance(scenario, int)
                else 403
                if code == "key_model_access_denied"
                else 400,
                json={
                    "error": {
                        "message": ERROR_TEXT,
                        "provider_specific_fields": {"code": code},
                    }
                },
            )
        if scenario == "invalid_json":
            return httpx.Response(
                200, content=ERROR_TEXT, headers={"content-type": "application/json"}
            )
        payload = response(scenario if scenario in {"safe", "unsafe"} else "safe")
        if scenario == "outside":
            payload["choices"][0]["message"]["content"] = ERROR_TEXT
        if scenario == "length":
            payload["choices"][0]["finish_reason"] = "length"
        if scenario == "empty_choices":
            payload["choices"] = []
        if scenario == "invalid_content":
            payload["choices"][0]["message"]["content"] = {"sentinel": TEXT}
        headers = {"x-litellm-model-id": DEPLOYMENT}
        if scenario == "wrong_deployment":
            headers["x-litellm-model-id"] = "openai-gpt-6-luna"
        if scenario == "missing_deployment":
            headers = {}
        return httpx.Response(200, json=payload, headers=headers)

    result = invoke(tmp_path, handle)
    assert isinstance(result, ClassifyResult)
    assert (result.status, result.label) == (status, label)
    assert len(seen) == 1
    reserved, settled = rows(tmp_path)
    assert reserved["kind"] == "reservation"
    assert (reserved["purpose"], reserved["model"], reserved["task_id"]) == (
        "guardrail_v2",
        "claude-haiku-5-5",
        "task",
    )
    assert settled["reservation_id"] == reserved["reservation_id"]
    assert (settled["purpose"], settled["model"]) == (
        "guardrail_v2",
        "claude-haiku-5-5",
    )
    expected = (
        costs.classifier_cost_usd(input_tokens=123, output_tokens=4)
        if charge == "usage"
        else reserved["estimate_usd"]
        if charge == "estimate"
        else 0
    )
    assert result.cost_usd == settled["cost_usd"] == expected
    assert (result.input_tokens, result.output_tokens) == (
        settled["input_tokens"],
        settled["output_tokens"],
    )
    if charge == "usage":
        assert (result.input_tokens, result.output_tokens) == (123, 4)
    if charge == "zero":
        assert (result.input_tokens, result.output_tokens) == (0, 0)
    assert costs.CostLedger(tmp_path).committed_usd("anthropic") == expected
    for sentinel in (KEY, TEXT, ERROR_TEXT, "classifier.invalid"):
        assert sentinel not in repr(result) + caplog.text
    assert set(asdict(result)) == {
        "label",
        "status",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    }
    with pytest.raises(FrozenInstanceError):
        result.label = "unsafe"


@pytest.mark.parametrize("kind", ["user", "tool"])
@pytest.mark.parametrize("task_id", ["a" * 32, "task", "A" * 32])
def test_sdk_request_timeout_metadata_and_estimate(
    tmp_path, monkeypatch, kind, task_id
):
    costs.init_ledgers(tmp_path)
    config = semantic.load_config()
    request = request_for(kind)
    sdk = Mock()
    sdk.close = AsyncMock()
    sdk.chat.completions.with_raw_response.create = AsyncMock(
        return_value=SimpleNamespace(
            headers={"x-litellm-model-id": DEPLOYMENT},
            parse=lambda: SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=123, completion_tokens=4),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content=config["labels"]["safe"]),
                    )
                ],
            ),
        )
    )
    constructor = Mock(return_value=sdk)
    monkeypatch.setattr("govops.gateway.client.AsyncOpenAI", constructor)
    monkeypatch.setattr("govops.gateway.client.current_span_id", lambda: "parent-id")
    resolver = Mock(return_value=KEY)
    client = LiteLLMChatClient(
        key_resolver=resolver, ledger=costs.CostLedger(tmp_path), timeout_s=60
    )
    result = asyncio.run(
        client.classify(request=request, task_id=task_id, user_id="user")
    )
    assert result.status == "ok"
    resolver.assert_called_once_with("user")
    assert constructor.call_args.kwargs["max_retries"] == 0
    timeout = constructor.call_args.kwargs["timeout"]
    assert timeout == httpx.Timeout(
        config["timeout_s"], connect=config["connect_timeout_s"]
    )
    metadata = {
        "task_id": task_id,
        "trace_user_id": "user",
        "generation_name": "guardrail-v2",
        "parent_observation_id": "parent-id",
    }
    if task_id == "a" * 32:
        metadata["existing_trace_id"] = task_id
    expected = {
        "model": config["model"],
        "messages": request.messages,
        "user": "user",
        "extra_body": {"metadata": metadata},
        "max_completion_tokens": config["max_completion_tokens"],
    }
    body = {
        "model": config["model"],
        "messages": request.messages,
        "user": "user",
        "metadata": metadata,
        "max_completion_tokens": config["max_completion_tokens"],
    }
    if kind == "tool":
        expected.update(tools=request.tools, tool_choice="none")
        body.update(tools=request.tools, tool_choice="none")
        assert len(request.tools) == 1
    sdk.chat.completions.with_raw_response.create.assert_awaited_once_with(**expected)
    assert rows(tmp_path)[0]["estimate_usd"] == costs.classifier_cost_usd(
        input_tokens=2 * len(json.dumps(body, ensure_ascii=True).encode("utf-8"))
        + 1024,
        output_tokens=config["max_completion_tokens"],
    )
    sdk.close.assert_awaited_once()
    assert client._timeout_s == 60


@pytest.mark.parametrize("header", ["valid", "missing", "invalid", "rules", "code"])
def test_header_validation_without_exposing_quarantine(tmp_path, header):
    costs.init_ledgers(tmp_path)
    expected = HookDigests("a" * 12, "b" * 12)
    info = GuardrailInfo(
        "c" * 12 if header == "rules" else expected.rules,
        "c" * 12 if header == "code" else expected.code,
        q=2,
        m=3,
        ids=("call_synthetic",),
    )
    headers = {"x-litellm-model-id": DEPLOYMENT}
    if header != "missing":
        headers["x-govops-guardrail"] = (
            "bad" if header == "invalid" else format_header(info)
        )
    result = invoke(
        tmp_path,
        lambda _: httpx.Response(200, json=response(), headers=headers),
        require_guardrail=True,
        expected_digests=expected,
    )
    assert result.status == ("ok" if header == "valid" else "error")
    assert result.label == ("safe" if header == "valid" else None)
    assert result.cost_usd == rows(tmp_path)[1]["cost_usd"] > 0
    assert set(asdict(result)) == {
        "label",
        "status",
        "input_tokens",
        "output_tokens",
        "cost_usd",
    }
    assert "call_synthetic" not in repr(result)


@pytest.mark.parametrize("missing", [False, True])
def test_budget_and_missing_ledger_raise_without_sdk_call(
    tmp_path, monkeypatch, missing
):
    if not missing:
        costs.init_ledgers(tmp_path)
        costs.CostLedger(tmp_path).record(
            purpose="agent_fallback",
            model="claude-sonnet-5-5",
            task_id="old",
            input_tokens=0,
            output_tokens=0,
            cost_usd=11.47,
        )
    constructor = Mock()
    monkeypatch.setattr("govops.gateway.client.AsyncOpenAI", constructor)
    client = LiteLLMChatClient(api_key=KEY, ledger=costs.CostLedger(tmp_path))
    before = client.status_counts()
    with pytest.raises(
        costs.LedgerMissingError if missing else costs.BudgetExceededError
    ):
        asyncio.run(
            client.classify(request=request_for(), task_id="task", user_id="user")
        )
    constructor.assert_not_called()
    assert client.status_counts() == before


@pytest.mark.parametrize("failure", ["timeout", "connection", "sdk", "unexpected"])
def test_direct_sdk_exceptions_are_safe(tmp_path, monkeypatch, caplog, failure):
    costs.init_ledgers(tmp_path)
    caplog.set_level(logging.DEBUG)
    req = httpx.Request("POST", "http://classifier.invalid", content=TEXT)
    errors = {
        "timeout": APITimeoutError(request=req),
        "connection": APIConnectionError(request=req, message=ERROR_TEXT),
        "sdk": OpenAIError(ERROR_TEXT),
        "unexpected": RuntimeError(ERROR_TEXT),
    }
    sdk = Mock()
    sdk.close = AsyncMock()
    sdk.chat.completions.with_raw_response.create = AsyncMock(
        side_effect=errors[failure]
    )
    monkeypatch.setattr("govops.gateway.client.AsyncOpenAI", Mock(return_value=sdk))
    client = LiteLLMChatClient(api_key=KEY, ledger=costs.CostLedger(tmp_path))
    result = asyncio.run(
        client.classify(request=request_for(), task_id="task", user_id="user")
    )
    assert result.status == ("timeout" if failure == "timeout" else "error")
    assert result.cost_usd == rows(tmp_path)[0]["estimate_usd"]
    sdk.chat.completions.with_raw_response.create.assert_awaited_once()
    for sentinel in (KEY, TEXT, ERROR_TEXT, "classifier.invalid"):
        assert sentinel not in repr(result) + caplog.text + repr(client.status_counts())


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": -1, "completion_tokens": 4},
        {"prompt_tokens": True, "completion_tokens": 4},
    ],
)
def test_malformed_usage_is_error_and_charges_estimate(tmp_path, usage):
    costs.init_ledgers(tmp_path)
    payload = response()
    payload["usage"] = usage
    result = invoke(
        tmp_path,
        lambda _: httpx.Response(
            200, json=payload, headers={"x-litellm-model-id": DEPLOYMENT}
        ),
    )
    assert result.status == "error" and result.label is None
    assert result.cost_usd == rows(tmp_path)[0]["estimate_usd"]


def test_ledger_io_is_off_loop_and_settlement_survives_cancellation(
    tmp_path, monkeypatch
):
    costs.init_ledgers(tmp_path)
    loop_thread = threading.get_ident()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_reserve, original_settle = (
        costs.CostLedger.reserve,
        costs.CostLedger.settle,
    )

    def reserve(self, **kwargs):
        assert threading.get_ident() != loop_thread
        return original_reserve(self, **kwargs)

    def settle(self, *args, **kwargs):
        assert threading.get_ident() != loop_thread
        started.set()
        assert release.wait(timeout=5)
        original_settle(self, *args, **kwargs)
        finished.set()

    monkeypatch.setattr(costs.CostLedger, "reserve", reserve)
    monkeypatch.setattr(costs.CostLedger, "settle", settle)

    async def call():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200, json=response(), headers={"x-litellm-model-id": DEPLOYMENT}
                )
            )
        ) as http:
            client = LiteLLMChatClient(
                api_key=KEY, ledger=costs.CostLedger(tmp_path), http_client=http
            )
            task = asyncio.create_task(
                client.classify(request=request_for(), task_id="task", user_id="user")
            )
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            assert await asyncio.to_thread(finished.wait, 5)

    asyncio.run(call())
    assert len(rows(tmp_path)) == 2


def test_status_counts_are_process_cumulative_and_thread_safe(tmp_path):
    costs.init_ledgers(tmp_path)
    client = LiteLLMChatClient(api_key=KEY, ledger=costs.CostLedger(tmp_path))
    before = client.status_counts()

    def call(_):
        async def classify():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(
                        200, json=response(), headers={"x-litellm-model-id": DEPLOYMENT}
                    )
                )
            ) as http:
                other = LiteLLMChatClient(
                    api_key=KEY, ledger=costs.CostLedger(tmp_path), http_client=http
                )
                return await other.classify(
                    request=request_for(), task_id="task", user_id="user"
                )

        return asyncio.run(classify())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(call, range(20), timeout=15))
    assert all(result.status == "ok" for result in results)
    assert client.status_counts() == {
        status: count + (20 if status == "ok" else 0)
        for status, count in before.items()
    }


def test_config_digest_is_a_thin_delegate(monkeypatch):
    digest = Mock(return_value="f" * 64)
    monkeypatch.setattr(semantic, "config_sha256", digest)
    assert LiteLLMChatClient.config_sha256() == "f" * 64
    digest.assert_called_once_with()


@pytest.mark.parametrize("stage", ["config", "key", "reserve", "settle"])
def test_local_failures_return_only_error_status(tmp_path, monkeypatch, caplog, stage):
    costs.init_ledgers(tmp_path)
    caplog.set_level(logging.DEBUG)
    request = request_for()
    failure = Mock(side_effect=RuntimeError(f"{KEY} {TEXT} {ERROR_TEXT}"))
    options = {}
    if stage == "config":
        monkeypatch.setattr(semantic, "load_config", failure)
    elif stage == "key":
        options["key_resolver"] = failure
    else:
        monkeypatch.setattr(costs.CostLedger, stage, failure)
    seen = []

    def handle(req):
        seen.append(req)
        return httpx.Response(
            200, json=response(), headers={"x-litellm-model-id": DEPLOYMENT}
        )

    result = invoke(tmp_path, handle, request=request, **options)
    assert result.status == "error" and result.label is None
    assert len(seen) == int(stage == "settle")
    assert len(rows(tmp_path)) == int(stage == "settle")
    if stage == "settle":
        assert (
            costs.CostLedger(tmp_path).committed_usd("anthropic")
            == rows(tmp_path)[0]["estimate_usd"]
        )
    else:
        assert result.cost_usd == 0
    for sentinel in (KEY, TEXT, ERROR_TEXT):
        assert sentinel not in repr(result) + caplog.text + repr(
            LiteLLMChatClient.status_counts()
        )

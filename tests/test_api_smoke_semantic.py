"""Semantic smoke contracts use only synthetic classifiers, services and traces."""

import asyncio
import json
from collections import defaultdict
from uuid import uuid4

import httpx
import pytest

from govops.api import smoke_semantic as smoke
from govops.gateway.client import ClassifyResult


class Ledger:
    def __init__(self):
        self.rows = []

    def read(self):
        return list(self.rows)


class Classifier:
    def __init__(self, ledger, label="unsafe", status="ok"):
        self.ledger, self.label, self.status = ledger, label, status
        self.results = defaultdict(list)
        self.calls = []
        self.finish_reasons = defaultdict(lambda: "stop")

    async def classify(self, *, request, task_id, user_id):
        self.calls.append(request)
        blocked = smoke.SYNTHETIC_PII in str(request.messages)
        status = "v1_blocked" if blocked else self.status
        result = ClassifyResult(
            None if blocked else self.label,
            status,
            0 if blocked else 10,
            0 if blocked else 1,
            0 if blocked else 0.001,
        )
        self.results[task_id].append(result)
        rid = uuid4().hex
        self.ledger.rows.extend(
            [
                {
                    "kind": "reservation",
                    "purpose": "guardrail_v2",
                    "task_id": task_id,
                    "reservation_id": rid,
                },
                {
                    "purpose": "guardrail_v2",
                    "task_id": task_id,
                    "reservation_id": rid,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": result.cost_usd,
                },
            ]
        )
        return result


class Service:
    def __init__(
        self, classifier, root, *, enabled=True, tool=None, broken=False, leak=False
    ):
        self.classifier, self.root = classifier, root
        self.enabled, self.tool, self.broken, self.leak = enabled, tool, broken, leak
        self.calls = []
        self.states = {}

    async def start(self, user, text):
        self.calls.append(text)
        task = uuid4().hex
        result = None
        if self.enabled:
            request = smoke.build_request(
                smoke.Item(
                    "tool" if self.tool else "user", self.tool or text, None, (), False
                ),
                smoke.load_config(),
            )
            result = await self.classifier.classify(
                request=request, task_id=task, user_id=user
            )
        unsafe = result is not None and result.label == "unsafe" and not self.broken
        blocked = unsafe and self.tool is None
        content = smoke.QUARANTINE_MARKER if unsafe else self.tool
        self.states[task] = {
            "status": "blocked_input" if blocked else "completed",
            "steps": 0 if blocked else 2,
            "messages": [{"role": "tool", "content": content}] if self.tool else [],
            "quarantined_ids": ["hash"] if unsafe and self.tool else [],
        }
        rows = (
            [
                {
                    "kind": "guardrail",
                    "name": "semantic",
                    "duration_ms": 10,
                    "attributes": {"guardrail.v2_n": 1},
                }
            ]
            if self.enabled
            else [{"kind": "task", "attributes": {}}]
        )
        if self.leak:
            rows[0]["leak"] = smoke.USER_SENTINEL
        (self.root / f"{task}.jsonl").write_text(json.dumps(rows[0]))
        return task

    async def get(self, user, task):
        return self.states[task]

    async def aclose(self):
        pass


@pytest.fixture
def runner(tmp_path):
    ledger = Ledger()
    classifier = Classifier(ledger)
    service = Service(classifier, tmp_path)
    off = Service(classifier, tmp_path, enabled=False)
    return smoke.Runner(
        service=service,
        off_service=off,
        classifier=classifier,
        read_ledger=ledger.read,
        trace_root=tmp_path,
        tool_service_factory=lambda text: Service(classifier, tmp_path, tool=text),
        user="demo-platform-g3",
        enabled=True,
        alias_probe=lambda: async_value(True),
        obs_enabled=False,
    )


async def async_value(value):
    return value


def stage(runner, name):
    return asyncio.run(getattr(runner, name)())


@pytest.mark.parametrize(
    "name",
    [
        "v2_off_switch",
        "v2_alias_access",
        "v2_format",
        "v2_user_block",
        "v2_tool_quarantine",
        "v2_v1_blocked_passthrough",
    ],
)
def test_basic_stages(runner, name):
    assert stage(runner, name)[0] == "PASS"


def test_v1_passthrough_calls_classifier_once_and_never_task(runner):
    assert stage(runner, "v2_v1_blocked_passthrough") == ("PASS", "settled_zero")
    assert len(runner.classifier.calls) == 1
    assert runner.service.calls == runner.off_service.calls == []


@pytest.mark.parametrize("name", ["v2_user_block", "v2_tool_quarantine"])
def test_safe_is_inconclusive_and_unsafe_without_defense_fails(runner, name):
    runner.classifier.label = "safe"
    assert stage(runner, name) == ("INCONCLUSIVE", "label_safe")
    runner.classifier.label = "unsafe"
    runner.service.broken = True
    runner.tool_service_factory = lambda text: Service(
        runner.classifier, runner.trace_root, tool=text, broken=True
    )
    assert stage(runner, name)[0] == "FAIL"


@pytest.mark.parametrize(
    "status", ["parse", "error", "timeout", "rate_limited", "v1_blocked"]
)
def test_format_errors_fail(runner, status):
    runner.classifier.status = status
    assert stage(runner, "v2_format")[0] == "FAIL"


@pytest.mark.parametrize("name", smoke.STAGES[1:])
def test_off_skips_without_calls(runner, name):
    runner.enabled = False
    assert asyncio.run(runner.step(name)) == ("SKIP", "switch_off")
    assert runner.classifier.calls == runner.service.calls == []


@pytest.mark.parametrize("name", ["v2_ledger_settled", "v2_ok_rate", "v2_latency"])
def test_zero_observations_fail(runner, name):
    assert stage(runner, name)[0] == "FAIL"


def test_empty_fixed_inputs_fail(runner, monkeypatch):
    monkeypatch.setattr(smoke, "NORMAL_INPUTS", ())
    monkeypatch.setattr(smoke, "INJECTION_INPUTS", ())
    for name in ("v2_format", "v2_user_block", "v2_tool_quarantine"):
        assert stage(runner, name)[0] == "FAIL"


def test_latency_and_status_thresholds(runner):
    async def run():
        await runner.collect_latency_tasks()
        await runner.v2_format()
        # Fake fixture services classify one item per task; fill to the minimum.
        while (
            sum(len(v) for v in runner.classifier.results.values()) < smoke.MIN_ATTEMPTS
        ):
            await runner.direct("user", smoke.NORMAL_INPUTS[0])
        assert (await runner.v2_ok_rate())[0] == "PASS"
        assert (await runner.v2_latency())[0] == "PASS"
        assert (await runner.v2_ledger_settled())[0] == "PASS"

    asyncio.run(run())
    for path in runner.trace_root.glob("*.jsonl"):
        row = json.loads(path.read_text())
        row["duration_ms"] = 9000
        path.write_text(json.dumps(row))
    assert stage(runner, "v2_latency")[0] == "FAIL"
    for values in runner.classifier.results.values():
        values[:] = [ClassifyResult(None, "error", 0, 0, 0) for _ in values]
    assert stage(runner, "v2_ok_rate")[0] == "FAIL"


def test_trace_missing_skip_and_positive_control(runner):
    runner.service.leak = True
    assert stage(runner, "v2_no_raw_in_trace") == ("FAIL", "raw_input_found")
    runner.service.leak = False
    for path in runner.trace_root.glob("*.jsonl"):
        path.unlink()
    assert stage(runner, "v2_no_raw_in_trace") == ("SKIP", "obs_off")
    runner.trace_root = runner.trace_root / "absent"
    assert stage(runner, "v2_no_raw_in_trace")[0] == "INCONCLUSIVE"


@pytest.mark.parametrize("where", [None, "generation", "recent", "missing"])
def test_remote_trace_all_observations_and_recent_list(runner, where):
    runner.obs_enabled = True
    runner.obs_poll_attempts = 1
    runner.obs_host, runner.obs_public, runner.obs_secret = (
        "http://synthetic",
        "public",
        "secret-test-key",
    )

    def handler(request):
        if where == "missing":
            return httpx.Response(404)
        if request.url.path.endswith("/traces"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "input": smoke.TOOL_SENTINEL
                            if where == "recent"
                            else "redacted"
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "id": request.url.path.split("/")[-1],
                "observations": [
                    {
                        "type": "GENERATION",
                        "name": "guardrail-v2",
                        "input": smoke.USER_SENTINEL
                        if where == "generation"
                        else "redacted",
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner.http = client
            return await runner.v2_no_raw_in_trace()

    expected = "INCONCLUSIVE" if where == "missing" else "FAIL" if where else "PASS"
    assert asyncio.run(run())[0] == expected


def test_output_is_only_codes_counts_and_summary(runner, capsys):
    asyncio.run(runner.run())
    text = capsys.readouterr().out
    assert "SUMMARY " in text
    for raw in (
        *smoke.NORMAL_INPUTS,
        *smoke.INJECTION_INPUTS,
        smoke.SYNTHETIC_PII,
        smoke.USER_SENTINEL,
        smoke.TOOL_SENTINEL,
        "secret-test-key",
    ):
        assert raw not in text
    assert all(
        line.split()[0] in {"PASS", "FAIL", "SKIP", "INCONCLUSIVE", "SUMMARY"}
        for line in text.splitlines()
    )


def test_fixed_inputs_pass_host_selection_without_copying_rules():
    from govops.guardrails.semantic import select_tool_items, select_user_item

    config = smoke.load_config()
    assert len(smoke.NORMAL_INPUTS) == 20
    assert len(smoke.INJECTION_INPUTS) == 5
    for text in (*smoke.NORMAL_INPUTS, *smoke.INJECTION_INPUTS, smoke.USER_SENTINEL):
        assert select_user_item(text, config) is not None
    for text in (*smoke.INJECTION_INPUTS, smoke.TOOL_NORMAL + smoke.TOOL_SENTINEL):
        items, _ = select_tool_items(
            [
                {
                    "role": "tool",
                    "tool_call_id": "call_smoke",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                }
            ],
            config,
        )
        assert len(items) == 1 and items[0].text == text


@pytest.mark.parametrize("finish", ["length", "tool_calls", None])
def test_format_requires_normal_finish_even_with_ok_label(runner, finish):
    runner.classifier.finish_reasons = defaultdict(lambda: finish)
    assert stage(runner, "v2_format")[0] == "FAIL"


def test_alias_denied_and_off_miswired_fail(runner):
    runner.alias_probe = lambda: async_value(False)
    assert stage(runner, "v2_alias_access")[0] == "FAIL"
    runner.off_service.enabled = True
    assert stage(runner, "v2_off_switch")[0] == "FAIL"


def test_unsettled_v1_reservation_and_empty_settlement_fail(runner):
    read = runner.read_ledger
    runner.read_ledger = lambda: [r for r in read() if r.get("kind") == "reservation"]
    assert stage(runner, "v2_v1_blocked_passthrough")[0] == "FAIL"
    assert stage(runner, "v2_ledger_settled")[0] == "FAIL"


def test_v2_ledger_requires_output_usage_and_matching_reservation(runner):
    stage(runner, "v2_format")
    for row in runner.classifier.ledger.rows:
        if row.get("kind") != "reservation":
            row["output_tokens"] = 0
    assert stage(runner, "v2_ledger_settled")[0] == "FAIL"


def test_recorder_caps_calls_before_transport():
    calls = []

    async def classify(**kwargs):
        calls.append(kwargs)
        return ClassifyResult("safe", "ok", 1, 1, 0.001)

    recorder = smoke.Recorder(classify)

    async def run():
        for _ in range(smoke.MAX_CLASSIFIER_CALLS):
            await recorder.classify(
                task_id="a" * 32, user_id="demo-platform-g3", request=None
            )
        with pytest.raises(smoke.BudgetExceededError):
            await recorder.classify(
                task_id="a" * 32, user_id="demo-platform-g3", request=None
            )

    asyncio.run(run())
    assert len(calls) == smoke.MAX_CLASSIFIER_CALLS


@pytest.mark.parametrize("enabled", [True, False])
def test_entrypoint_uses_real_graphs_and_only_fake_transports(
    tmp_path, monkeypatch, capsys, enabled
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from langgraph.checkpoint.memory import InMemorySaver

    from govops.api.tasks import TaskService
    from govops.costs import CostLedger, init_ledgers
    from govops.obs.jsonl import JsonlSink
    from govops.obs.trace import configure

    init_ledgers(tmp_path / "ledger")
    ledger = CostLedger(tmp_path / "ledger")
    sends = []

    class Gateway(smoke.LiteLLMChatClient):
        async def _send(self, body, **kwargs):
            sends.append(body["model"])
            if body["model"] == "guardrail-v2":
                content = body["messages"][-1]["content"]
                if smoke.SYNTHETIC_PII in content:
                    from govops.gateway.client import GuardrailBlocked

                    raise GuardrailBlocked("pii_input", None)
                label = "unsafe" if content in smoke.INJECTION_INPUTS else "safe"
                message = SimpleNamespace(
                    content=smoke.load_config()["labels"][label], tool_calls=[]
                )
                deployment = "anthropic-claude-haiku-5-5"
            else:
                message = SimpleNamespace(content="합성 점검 완료", tool_calls=[])
                deployment = "openai-gpt-6-luna"
            return (
                SimpleNamespace(
                    model="gpt-6-luna",
                    usage=SimpleNamespace(
                        prompt_tokens=10,
                        completion_tokens=1,
                        prompt_tokens_details=None,
                    ),
                    choices=[SimpleNamespace(message=message, finish_reason="stop")],
                ),
                deployment,
                body["model"],
                0,
                None,
            )

    gateways = []
    switches = []

    @asynccontextmanager
    async def open_service():
        enabled = smoke.env_or("GOVOPS_GUARDRAIL_V2", "off") == "on"
        switches.append(enabled)
        chat = Gateway(api_key="synthetic-key", ledger=ledger)
        gateways.append(chat)
        configure([JsonlSink(tmp_path / "trace")])
        graph = smoke.build_graph(
            chat,
            smoke._SyntheticTools(smoke.TOOL_NORMAL),
            InMemorySaver(),
            semantic=chat if enabled else None,
        )
        service = TaskService(graph)
        try:
            yield service
        finally:
            await service.aclose()
            configure([])

    real_http = httpx.AsyncClient

    def handler(request):
        body = json.loads(request.content)
        if body["model"].startswith("not-allowed-"):
            return httpx.Response(
                403, json={"error": {"type": "key_model_access_denied"}}
            )
        from govops.gateway.hook_header import GuardrailInfo, format_header

        return httpx.Response(
            400,
            json={"error": {"code": "probe"}},
            headers={
                "x-govops-guardrail": format_header(
                    GuardrailInfo(
                        "a" * 12, "b" * 12, team="platform", pre="probe", c="probe"
                    )
                )
            },
        )

    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", "on" if enabled else "off")
    monkeypatch.setenv("GOVOPS_TRACE_DIR", str(tmp_path / "trace"))
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", "synthetic-key")
    monkeypatch.setattr(smoke, "open_task_service", open_service)
    monkeypatch.setattr(smoke, "CostLedger", lambda: ledger)
    monkeypatch.setattr(
        smoke,
        "compute_digests",
        lambda _: SimpleNamespace(rules="a" * 12, code="b" * 12),
    )
    monkeypatch.setattr(smoke, "obs_config_active", lambda _: False)
    monkeypatch.setattr(
        smoke.httpx,
        "AsyncClient",
        lambda **kwargs: real_http(transport=httpx.MockTransport(handler)),
    )
    assert smoke.main() == 0
    assert switches == ([False, True] if enabled else [False])
    assert smoke.env_or("GOVOPS_GUARDRAIL_V2", "off") == ("on" if enabled else "off")
    if enabled:
        assert 100 <= sends.count("guardrail-v2") <= smoke.MAX_CLASSIFIER_CALLS
        assert "PASS v2_user_block" in capsys.readouterr().out
    else:
        assert sends.count("guardrail-v2") == 0
        assert "SKIP v2_user_block switch_off" in capsys.readouterr().out
    assert all(getattr(chat.classify, "__self__", None) is chat for chat in gateways)


def test_trace_api_failure_is_inconclusive_and_private_error_is_hidden(runner):
    runner.obs_enabled = True
    runner.obs_poll_attempts = 1
    runner.obs_host, runner.obs_public, runner.obs_secret = (
        "http://synthetic",
        "public",
        "private-key",
    )
    runner.obs_poll_attempts = 1

    def handler(request):
        raise httpx.ConnectError("private-key " + smoke.USER_SENTINEL, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner.http = client
            return await runner.step("v2_no_raw_in_trace")

    assert asyncio.run(run()) == ("INCONCLUSIVE", "trace_not_found")


def test_off_switch_requires_executed_task_with_no_semantic_spans(runner):
    original = runner.off_service.start

    async def bad_start(user, text):
        task = await original(user, text)
        (runner.trace_root / f"{task}.jsonl").write_text(
            json.dumps(
                {
                    "kind": "guardrail",
                    "name": "semantic",
                    "duration_ms": 1,
                    "attributes": {"guardrail.v2_n": 1},
                }
            )
        )
        return task

    runner.off_service.start = bad_start
    assert stage(runner, "v2_off_switch")[0] == "FAIL"


@pytest.mark.parametrize("empty", ["observations", "recent"])
def test_remote_zero_inspection_targets_fail(runner, empty):
    runner.obs_enabled = True
    runner.obs_poll_attempts = 1
    runner.obs_host, runner.obs_public, runner.obs_secret = (
        "http://synthetic",
        "public",
        "synthetic-key",
    )

    def handler(request):
        if request.url.path.endswith("/traces"):
            return httpx.Response(
                200, json={"data": [] if empty == "recent" else [{"id": "synthetic"}]}
            )
        observations = (
            []
            if empty == "observations"
            else [{"type": "GENERATION", "name": "guardrail-v2"}]
        )
        return httpx.Response(
            200,
            json={
                "id": request.url.path.rsplit("/", 1)[-1],
                "observations": observations,
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner.http = client
            return await runner.step("v2_no_raw_in_trace")

    assert asyncio.run(run()) == ("FAIL", "no_" + empty)


def test_entrypoint_filters_transport_logs_and_restores_context(monkeypatch, caplog):
    import logging

    previous = smoke._IN_CALL.get()

    async def run():
        logging.getLogger("httpx").warning(
            "synthetic-private-key " + smoke.USER_SENTINEL
        )
        return 0

    monkeypatch.setattr(smoke, "_run", run)
    assert smoke.main() == 0
    assert "synthetic-private-key" not in caplog.text
    assert smoke.USER_SENTINEL not in caplog.text
    assert smoke._IN_CALL.get() is previous

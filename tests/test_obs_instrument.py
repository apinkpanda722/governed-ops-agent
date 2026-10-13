import asyncio
import json
from dataclasses import asdict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, call, reply
from test_obs_trace import Sink

from govops.agent.graph import MAX_STEPS, build_graph
from govops.agent.prompts import SYSTEM_PROMPT
from govops.api.tasks import TaskService
from govops.gateway.client import GatewayError
from govops.obs.mask import register_secret_values
from govops.obs.trace import SpanKind, configure
from govops.tools.registry import ToolOutcome


@pytest.fixture
def sink():
    result = Sink()
    configure([result])
    yield result
    configure([])


async def finish(service, user, request):
    ident = await service.start(user, request)
    await asyncio.gather(*service._tasks)
    return ident, await service.get(user, ident)


def test_structure_masking_and_isolation(sink):
    secret = "instrument-secret-sentinel"
    register_secret_values([secret])
    chat = FakeChat(
        reply(
            "INTERMEDIATE_SENTINEL",
            [call(arguments=json.dumps({"query": secret + " 010-1234-5678"}))],
        ),
        reply("[syn-one] 010-1234-5678"),
    )
    tools = FakeTools(
        ToolOutcome({"text": "가" * 400 + "TAIL_SENTINEL"}, False, ("syn-one",))
    )
    service = TaskService(build_graph(chat, tools, InMemorySaver()))
    ident, result = asyncio.run(
        finish(service, "demo-platform-g3", "조회 010-1234-5678")
    )
    records = sink.records
    root = records[-1]
    assert root.kind == SpanKind.TASK and root.trace_id == ident
    assert root.attributes["steps"] == result["steps"] == 2
    assert "input.request" not in root.attributes
    assert root.attributes["output.answer"] == "[syn-one] [PII:mobile]"
    assert root.attributes["cited_doc_ids"] == ["syn-one"]
    nodes = [r for r in records if r.kind == SpanKind.GRAPH_NODE]
    assert [n.name for n in nodes] == ["model", "read_tools", "model"]
    assert all(n.parent_id == root.span_id for n in nodes)
    for r in records:
        if r.kind in (SpanKind.MODEL_CALL, SpanKind.TOOL_CALL):
            assert r.parent_id in {n.span_id for n in nodes}
        if r is not root:
            assert "user_id" not in r.attributes
    models = [r for r in records if r.kind == SpanKind.MODEL_CALL]
    assert len(models) == result["steps"]
    assert models[0].attributes["tool_call_count"] == 1
    assert models[0].attributes["usage"] == {"input": 0, "output": 0, "cached_input": 0}
    tool = next(r for r in records if r.kind == SpanKind.TOOL_CALL)
    assert tool.attributes["tool.result"]["ids"] == ["syn-one"]
    serialized = json.dumps([asdict(r) for r in records], ensure_ascii=False)
    for forbidden in (
        secret,
        "010-1234-5678",
        "TAIL_SENTINEL",
        "INTERMEDIATE_SENTINEL",
        SYSTEM_PROMPT,
    ):
        assert forbidden not in serialized
    assert not any("trace" in key for key in result)
    assert sink.closed == [ident]


@pytest.mark.parametrize("mode", ["gateway", "max_steps"])
def test_failure_trace(sink, mode):
    chat = (
        FakeChat(GatewayError("PRIVATE"))
        if mode == "gateway"
        else FakeChat(*(reply(calls=[call()]) for _ in range(MAX_STEPS)))
    )
    tools = FakeTools(
        *(
            ToolOutcome({"doc_id": f"syn-{i}"}, mode == "max_steps", ())
            for i in range(MAX_STEPS)
        )
    )
    service = TaskService(build_graph(chat, tools, InMemorySaver()))
    ident, result = asyncio.run(finish(service, "demo-platform-g3", "조회"))
    assert result["error"] == ("gateway_error" if mode == "gateway" else "max_steps")
    assert sink.records[-1].status == "error"
    assert sink.records[-1].attributes["error_code"] == result["error"]
    assert sink.closed == [ident]


def test_concurrent_tasks_and_cancellation(sink):
    class Chat:
        async def complete(self, **kwargs):
            request = kwargs["messages"][-1]["content"]
            await asyncio.sleep(0)
            if request == "wait":
                entered.set()
                await asyncio.Event().wait()
            return reply(request)

    async def run():
        nonlocal entered
        entered = asyncio.Event()
        service = TaskService(build_graph(Chat(), FakeTools(), InMemorySaver()))
        ids = await asyncio.gather(
            service.start("demo-platform-g3", "PLATFORM_SENTINEL"),
            service.start("demo-finance-g3", "FINANCE_SENTINEL"),
        )
        await asyncio.gather(*service._tasks)
        for ident, user in zip(
            ids, ("demo-platform-g3", "demo-finance-g3"), strict=True
        ):
            result = await service.get(user, ident)
            assert set(result) == {
                "task_id",
                "status",
                "answer",
                "cited_doc_ids",
                "observed_doc_ids",
                "steps",
                "error",
                "guardrail",
                "tool_steps",
            }
        cancelled = await service.start("demo-platform-g3", "wait")
        await entered.wait()
        await service.aclose()
        assert (await service.get("demo-platform-g3", cancelled))[
            "error"
        ] == "internal_error"
        return ids, cancelled

    entered = None
    ids, cancelled = asyncio.run(run())
    assert ids[0] != ids[1]
    for ident, forbidden in zip(
        ids, ("FINANCE_SENTINEL", "PLATFORM_SENTINEL"), strict=True
    ):
        records = [r for r in sink.records if r.trace_id == ident]
        assert len([r for r in records if r.kind == SpanKind.TASK]) == 1
        assert forbidden not in repr(records)
    root = next(
        r for r in sink.records if r.trace_id == cancelled and r.kind == SpanKind.TASK
    )
    assert root.status == "error"
    assert root.attributes["error_code"] == "cancelled"
    assert set(sink.closed) == {*ids, cancelled}


def test_gateway_enriches_existing_model_span(tmp_path, sink):
    import httpx
    from test_gateway_client import KEY, MESSAGES, TOOLS, response

    from govops import costs
    from govops.gateway.client import LiteLLMChatClient
    from govops.obs.trace import span, trace

    costs.init_ledgers(tmp_path)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=response())
            )
        ) as http:
            client = LiteLLMChatClient(
                api_key=KEY, ledger=costs.CostLedger(tmp_path), http_client=http
            )
            with trace("e" * 32), span(SpanKind.MODEL_CALL, "agent-primary"):
                result = await client.complete(
                    messages=MESSAGES,
                    tools=TOOLS,
                    task_id="e" * 32,
                    user_id="demo-platform-g3",
                )
            return result

    result = asyncio.run(run())
    assert len(sink.records) == 2
    model = sink.records[0]
    assert model.attributes["usage"] == {"input": 100, "cached_input": 40, "output": 20}
    assert model.attributes["cost_usd"] == result.cost_usd
    assert KEY not in repr(sink.records)


def test_startup_registers_environment_secrets(monkeypatch, tmp_path):
    from govops.api.startup import StartupError
    from govops.api.wiring import open_task_service
    from govops.obs.mask import mask_text

    configured = []
    monkeypatch.setattr("govops.api.wiring.configure", configured.extend)
    monkeypatch.setenv("GOVOPS_TRACE_DIR", str(tmp_path))
    monkeypatch.setenv("CUSTOM_SECRET", "startup-secret-sentinel")
    monkeypatch.setenv("CUSTOM_TOKEN", "startup-token-sentinel")
    monkeypatch.delenv("KODOCS_MCP_URL", raising=False)

    async def run():
        async with open_task_service():
            pytest.fail("missing configuration")

    with pytest.raises(StartupError):
        asyncio.run(run())
    assert len(configured) == 1 and configured[0].root == tmp_path
    assert (
        mask_text("startup-secret-sentinel startup-token-sentinel", role="request")
        == "[REDACTED] [REDACTED]"
    )

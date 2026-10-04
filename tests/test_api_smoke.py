"""Live smoke logic is tested without live services or credentials."""

import asyncio
import inspect
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from govops.api import smoke
from govops.costs import CostLedger, init_ledgers


class FakeService:
    def __init__(self, ledger, change=None):
        self.ledger = ledger
        self.change = change
        self.requests = []

    async def start(self, user_id, request):
        self.requests.append(request)
        if self.change in {"reserved", "unsettled"}:
            reservation = self.ledger.reserve(
                purpose="agent_primary",
                model="fake",
                task_id=user_id,
                estimate_usd=0.02,
            )
            if self.change == "reserved":
                self.ledger.settle(
                    reservation,
                    purpose="agent_primary",
                    model="fake",
                    task_id=user_id,
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=0.01,
                )
        elif self.change != "ledger":
            self.ledger.record(
                purpose="agent_primary",
                model="fake",
                task_id=user_id,
                input_tokens=1,
                output_tokens=1,
                cost_usd=9.13 if self.change == "cap" else 0.01,
            )
        return user_id

    async def get(self, user_id, task_id):
        ids = [smoke.EXPECTED_DOC] if "platform" in user_id else []
        if self.change == "finance" and "finance" in user_id:
            ids = [smoke.EXPECTED_DOC]
        if self.change == "platform" and "platform" in user_id:
            ids = []
        return {
            "status": "failed" if self.change == "status" else "completed",
            "steps": 2,
            "observed_doc_ids": ids,
            "cited_doc_ids": ids,
            "answer": "합성 요약",
        }


@pytest.mark.parametrize(
    "change,item",
    [
        (None, None),
        ("reserved", None),  # 예약 + 정산 행은 호출 하나로 센다
        ("unsettled", "agent_primary"),  # 정산되지 않은 예약만으로는 통과하지 않는다
        ("finance", "finance"),
        ("platform", "platform"),
        ("status", "status"),
        ("ledger", "agent_primary"),
        ("cap", "OpenAI"),
    ],
)
def test_agent(tmp_path, change, item):
    async def run():
        init_ledgers(tmp_path)
        ledger = CostLedger(tmp_path)
        service = FakeService(ledger, change)
        result = await smoke.smoke_agent(service, ledger, poll_interval=0)
        assert result.ok == (change in {None, "reserved"})
        assert len(set(service.requests)) == 1
        if item:
            assert item in "\n".join(result.lines)

    asyncio.run(run())


def test_timeout(tmp_path):
    async def run():
        init_ledgers(tmp_path)
        service = FakeService(CostLedger(tmp_path))

        async def running(*args):
            return {"status": "running"}

        service.get = running
        result = await smoke.smoke_agent(service, service.ledger, timeout_s=0)
        assert not result.ok and "status" in "\n".join(result.lines)

    asyncio.run(run())


@pytest.mark.parametrize("mismatch", [False, True])
def test_checkpointer(monkeypatch, mismatch):
    async def run():
        saver = InMemorySaver()
        deleted = []
        original = saver.adelete_thread

        async def delete(thread):
            deleted.append(thread)
            await original(thread)

        saver.adelete_thread = delete

        @asynccontextmanager
        async def fake(url):
            yield saver

        monkeypatch.setattr(smoke, "open_checkpointer", fake)
        if mismatch:
            original_get = saver.aget_tuple

            async def get(config):
                value = await original_get(config)
                if value:
                    value.checkpoint["channel_values"]["value"] = "different"
                return value

            saver.aget_tuple = get
        result = await smoke.smoke_checkpointer("postgres://secret-sentinel")
        assert result.ok == (not mismatch)
        assert len(deleted) == 1
        assert "secret-sentinel" not in str(result.lines)
        assert not (await smoke.smoke_checkpointer("")).ok

    asyncio.run(run())


def test_preflight(monkeypatch, tmp_path, capsys, caplog):
    monkeypatch.setattr(smoke, "CostLedger", lambda: CostLedger(tmp_path))
    for name in ("LITELLM_AGENT_KEY", "KODOCS_MCP_URL", "GOVOPS_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert smoke.main(["agent"]) == 1
    output = capsys.readouterr().out
    assert "LITELLM_AGENT_KEY" in output and "KODOCS_MCP_URL" in output
    monkeypatch.setenv("LITELLM_AGENT_KEY", "key-sentinel")
    monkeypatch.setenv("KODOCS_MCP_URL", "token-sentinel")
    assert smoke.main(["agent"]) == 1
    assert "sentinel" not in capsys.readouterr().out + caplog.text


def test_fixed_expectations():
    source = inspect.getsource(smoke)
    assert 'EXPECTED_DOC = "syn-incident-022"' in source
    assert "OPENAI_CAP = 9.13" in source
    assert "--expected" not in source
    assert 'getenv("EXPECTED' not in source and 'environ.get("EXPECTED' not in source


def test_safe_failures(monkeypatch, tmp_path, capsys, caplog):
    init_ledgers(tmp_path)
    ledger = CostLedger(tmp_path)

    class BrokenService:
        async def start(self, *args):
            raise RuntimeError("key-token-sentinel")

    result = asyncio.run(smoke.smoke_agent(BrokenService(), ledger))
    assert not result.ok
    assert "sentinel" not in str(result.lines)

    @asynccontextmanager
    async def broken(url):
        raise RuntimeError("connection-sentinel")
        yield

    monkeypatch.setattr(smoke, "open_checkpointer", broken)
    result = asyncio.run(smoke.smoke_checkpointer("postgres://connection-sentinel"))
    assert not result.ok and "sentinel" not in str(result.lines)
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "postgres://connection-sentinel")
    assert smoke.main(["checkpointer"]) == 1
    assert "sentinel" not in capsys.readouterr().out + caplog.text


def test_agent_main_wiring_and_memory_warning(monkeypatch, tmp_path, capsys):
    init_ledgers(tmp_path)
    ledger = CostLedger(tmp_path)
    monkeypatch.setattr(smoke, "CostLedger", lambda: ledger)
    monkeypatch.setenv("LITELLM_AGENT_KEY", "key-sentinel")
    monkeypatch.setenv("KODOCS_MCP_URL", "token-sentinel")
    monkeypatch.delenv("GOVOPS_DATABASE_URL", raising=False)

    @asynccontextmanager
    async def service():
        yield FakeService(ledger)

    monkeypatch.setattr(smoke, "open_task_service", service)
    assert smoke.main(["agent"]) == 0
    output = capsys.readouterr().out
    assert "경고: GOVOPS_DATABASE_URL" in output
    assert "sentinel" not in output


@pytest.mark.parametrize("field", ["observed_doc_ids", "cited_doc_ids"])
def test_each_structured_id_field_is_checked(tmp_path, field):
    init_ledgers(tmp_path)
    service = FakeService(CostLedger(tmp_path))
    original = service.get

    async def altered(user, task):
        state = await original(user, task)
        if "platform" in user:
            state[field] = []
            state["answer"] = smoke.EXPECTED_DOC
        return state

    service.get = altered
    result = asyncio.run(smoke.smoke_agent(service, service.ledger))
    assert not result.ok
    assert any(field in line and "불일치" in line for line in result.lines)


@pytest.mark.parametrize(
    "change,failed",
    [
        (None, None),
        ("missing", "E1"),
        ("root", "E2"),
        ("model", "E2"),
        ("tool", "E2"),
        ("generation", "E3"),
        ("generation_id", "E3"),
        ("content", "E4"),
        ("redacted", None),
        ("request_text", "E4"),
        ("phone", "E5"),
        ("placeholder", "E5"),
        ("secret", "E6"),
        ("counts", "E7"),
        ("local_phone", "E5"),
        ("local_secret", "E6"),
    ],
)
@pytest.mark.parametrize("queue", [200, 404, "unavailable"])
def test_obs_expectations(tmp_path, change, failed, queue):
    import json

    import httpx

    task = "a" * 32
    probe = smoke.OBS_PROBE
    secret = "private-smoke-sentinel"
    rows = [
        {
            "span_id": str(i),
            "kind": kind,
            "attributes": {
                "input.request": "참고 연락처: [PII:mobile]",
                **({"generation_id": "generation"} if kind == "model_call" else {}),
            },
        }
        for i, kind in enumerate(("task", "model_call", "tool_call"))
    ]
    observations = [
        {
            "id": row["span_id"],
            "name": row["kind"],
            "type": "SPAN",
            "input": row["attributes"]["input.request"],
            "parentObservationId": None if i == 0 else "0",
            "traceId": task,
        }
        for i, row in enumerate(rows)
    ]
    generation = {
        "id": "generation",
        "name": "agent-primary",
        "type": "GENERATION",
        "traceId": task,
        "input": None,
        "output": None,
    }
    observations.append(generation)
    if change in {"root", "model", "tool"}:
        observations[{"root": 0, "model": 1, "tool": 2}[change]]["name"] = "other"
    if change == "generation":
        observations.pop()
    if change == "generation_id":
        generation["id"] = "litellm-made-long-id"
    if change == "content":
        generation["input"] = "message"
    if change == "redacted":
        # LiteLLM은 원문 로깅을 끄면 비우지 않고 자리 표시자로 바꾼다.
        generation["input"] = [{"role": "user", "content": "redacted-by-litellm"}]
        generation["output"] = {
            "role": "assistant",
            "content": "redacted-by-litellm",
            "tool_calls": [{"function": {"arguments": "redacted-by-litellm"}}],
        }
    if change == "request_text":
        generation["input"] = [
            {"role": "user", "content": "redacted-by-litellm INC-2026-012"}
        ]
    if change == "phone":
        generation["metadata"] = probe
    if change == "secret":
        generation["metadata"] = secret
    if change == "placeholder":
        observations[0]["input"] = "no placeholder"
    if change == "counts":
        observations.pop(1)
    if change in {"local_phone", "local_secret"}:
        rows[0]["attributes"]["leak"] = probe if change == "local_phone" else secret
    (tmp_path / f"{task}.jsonl").write_text("\n".join(json.dumps(row) for row in rows))

    class Service:
        async def start(self, user, request):
            assert user == "demo-platform-g3"
            assert request == smoke.REQUEST + " 참고 연락처: " + probe
            return task

        async def get(self, user, task_id):
            return {"status": "completed"}

    def handler(request):
        assert request.headers["authorization"].startswith("Basic ")
        if request.url.path.endswith("annotation-queues"):
            if queue == "unavailable":
                raise httpx.ConnectError(secret, request=request)
            return httpx.Response(queue)
        return httpx.Response(
            404 if change == "missing" else 200,
            json={"id": task, "observations": observations},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await smoke.smoke_obs(
                Service(),
                client,
                host="http://langfuse.test",
                public_key="public-sentinel",
                secret_key=secret,
                secrets=(secret,),
                trace_root=tmp_path,
                timeout_s=0,
                poll_interval=0,
            )

    result = asyncio.run(run())
    assert result.ok == (failed is None)
    assert (
        len([line for line in result.lines if line.startswith(("PASS ", "FAIL "))]) == 7
    )
    if failed:
        assert any(line.startswith("FAIL " + failed) for line in result.lines)
    assert f"INFO annotation_queues_status={queue}" in result.lines
    assert probe not in str(result) and secret not in str(result)


def test_obs_preflight(monkeypatch, capsys):
    for name in smoke.OBS_REQUIRED_ENV:
        monkeypatch.delenv(name, raising=False)
    assert smoke.main(["obs"]) == 1
    output = capsys.readouterr().out
    assert all(name in output for name in smoke.OBS_REQUIRED_ENV)


def test_obs_cli_wiring_and_safe_failure(monkeypatch, tmp_path, capsys, caplog):
    init_ledgers(tmp_path)
    monkeypatch.setattr(smoke, "CostLedger", lambda: CostLedger(tmp_path))
    for name in smoke.OBS_REQUIRED_ENV:
        monkeypatch.setenv(name, "secret-sentinel")
    seen = []

    @asynccontextmanager
    async def service():
        yield "fake-service"

    async def obs(service, client, **kwargs):
        seen.append(service)
        assert isinstance(client, __import__("httpx").AsyncClient)
        return smoke.SmokeResult(True, ["PASS E1_TRACE_EXISTS"])

    monkeypatch.setattr(smoke, "open_task_service", service)
    monkeypatch.setattr(smoke, "smoke_obs", obs)
    assert smoke.main(["obs"]) == 0
    assert seen == ["fake-service"]

    @asynccontextmanager
    async def broken():
        raise RuntimeError("secret-sentinel " + smoke.OBS_PROBE)
        yield

    monkeypatch.setattr(smoke, "open_task_service", broken)
    assert smoke.main(["obs"]) == 1
    output = capsys.readouterr().out + caplog.text
    assert "secret-sentinel" not in output and smoke.OBS_PROBE not in output


def test_obs_probe_and_fixed_contract():
    from govops.guardrails.pii import mask_pii

    assert "[PII:mobile]" in mask_pii(smoke.OBS_PROBE)
    assert len(smoke.OBS_EXPECTATIONS) == 7
    assert smoke.OBS_TIMEOUT_S == 60 and smoke.OBS_POLL_INTERVAL == 5
    assert smoke.OBS_INFO == "INFO annotation_queues_status="


REASONS = {
    "missing": ("E1", "trace_fetch_status=404"),
    "root": ("E2", "root_task_missing"),
    "model": ("E2", "model_call_span_missing"),
    "tool": ("E2", "tool_call_span_missing"),
    "generation": ("E3", "generation_missing"),
    "generation_id": ("E3", "generation_id_mismatch=1"),
    "content": ("E4", "generation_not_redacted=1"),
    "request_text": ("E4", "request_text_in_generation=1"),
    "phone": ("E5", "probe_in_langfuse"),
    "placeholder": ("E5", "pii_placeholder_missing"),
    "secret": ("E6", "secret_in_langfuse"),
    "counts": ("E7", "local_spans=3 remote_spans=2"),
    "local_phone": ("E5", "probe_in_local"),
    "local_secret": ("E6", "secret_in_local"),
}


def run_obs(tmp_path, handler, service=None, **kwargs):
    import httpx

    task = "a" * 32

    class Service:
        async def start(self, user, request):
            return task

        async def get(self, user, task_id):
            return {"status": "completed"}

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await smoke.smoke_obs(
                service or Service(),
                client,
                host="http://langfuse.test",
                public_key="public-sentinel",
                secret_key="private-smoke-sentinel",
                secrets=("private-smoke-sentinel",),
                trace_root=tmp_path,
                **{"timeout_s": 0, "poll_interval": 0} | kwargs,
            )

    return asyncio.run(run())


@pytest.mark.parametrize("change", sorted(REASONS))
def test_failed_checks_carry_a_short_reason_code(tmp_path, change):
    import json

    import httpx

    task = "a" * 32
    rows = [
        {
            "span_id": str(i),
            "kind": kind,
            "attributes": {
                "input.request": "참고 연락처: [PII:mobile]",
                **({"generation_id": "generation"} if kind == "model_call" else {}),
            },
        }
        for i, kind in enumerate(("task", "model_call", "tool_call"))
    ]
    observations = [
        {
            "id": r["span_id"],
            "name": r["kind"],
            "type": "SPAN",
            "input": r["attributes"]["input.request"],
            "parentObservationId": None if i == 0 else "0",
            "traceId": task,
        }
        for i, r in enumerate(rows)
    ]
    generation = {
        "id": "generation",
        "name": "agent-primary",
        "type": "GENERATION",
        "traceId": task,
        "input": None,
        "output": None,
    }
    observations.append(generation)
    if change in {"root", "model", "tool"}:
        observations[{"root": 0, "model": 1, "tool": 2}[change]]["name"] = "other"
    if change == "generation":
        observations.pop()
    if change == "generation_id":
        generation["id"] = "litellm-made-long-id"
    if change == "content":
        generation["input"] = "message"
    if change == "request_text":
        generation["input"] = "redacted-by-litellm INC-2026-012"
    if change == "phone":
        generation["metadata"] = smoke.OBS_PROBE
    if change == "secret":
        generation["metadata"] = "private-smoke-sentinel"
    if change == "placeholder":
        observations[0]["input"] = "no placeholder"
    if change == "counts":
        observations.pop(1)
    if change == "local_phone":
        rows[0]["attributes"]["leak"] = smoke.OBS_PROBE
    if change == "local_secret":
        rows[0]["attributes"]["leak"] = "private-smoke-sentinel"
    (tmp_path / f"{task}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))

    def handler(request):
        if request.url.path.endswith("annotation-queues"):
            return httpx.Response(200)
        if change == "missing":
            return httpx.Response(404)
        return httpx.Response(200, json={"id": task, "observations": observations})

    result = run_obs(tmp_path, handler)
    code, reason = REASONS[change]
    lines = [line for line in result.lines if line.startswith("FAIL " + code)]
    assert lines and f": {reason}" in lines[0], result.lines
    text = str(result)
    assert smoke.OBS_PROBE not in text and "private-smoke-sentinel" not in text


def test_generations_under_model_span_is_reported_as_info(tmp_path):
    import json

    import httpx

    task = "a" * 32
    rows = [
        {"span_id": "0", "kind": "task", "attributes": {"input.request": "[PII:x]"}},
        {"span_id": "1", "kind": "model_call", "attributes": {"generation_id": "g1"}},
        {"span_id": "2", "kind": "tool_call", "attributes": {}},
    ]
    (tmp_path / f"{task}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    base = [
        {"id": r["span_id"], "name": r["kind"], "type": "SPAN", "traceId": task}
        for r in rows
    ]
    base[0]["input"] = "[PII:x]"
    for item in base[1:]:
        item["parentObservationId"] = "0"
    generation = {
        "id": "g1",
        "name": "agent-primary",
        "type": "GENERATION",
        "traceId": task,
        "parentObservationId": "1",
    }

    def handler(request):
        if request.url.path.endswith("annotation-queues"):
            return httpx.Response(200)
        return httpx.Response(
            200, json={"id": task, "observations": [*base, generation]}
        )

    result = run_obs(tmp_path, handler)
    assert "INFO generations_under_model_span=1/1" in result.lines
    generation.pop("parentObservationId")
    assert "INFO generations_under_model_span=0/1" in run_obs(tmp_path, handler).lines


def test_final_attempt_does_not_wipe_partial_results(tmp_path):
    """마감 직전 짧은 마지막 조회가 앞선 판정을 전부 지우던 문제의 회귀 테스트."""
    import json

    import httpx

    task = "a" * 32
    rows = [
        {"span_id": "0", "kind": "task", "attributes": {"input.request": "[PII:x]"}}
    ]
    (tmp_path / f"{task}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    observations = [
        {
            "id": "0",
            "name": "task",
            "type": "SPAN",
            "traceId": task,
            "input": "[PII:x]",
        }
    ]

    async def handler(request):
        if request.url.path.endswith("annotation-queues"):
            return httpx.Response(200)
        await asyncio.sleep(0.15)  # 남은 시간이 거의 없을 때의 느린 조회
        return httpx.Response(200, json={"id": task, "observations": observations})

    result = run_obs(tmp_path, handler, timeout_s=0.3, poll_interval=0.05)
    assert any(line.startswith("PASS E1") for line in result.lines), result.lines


def test_failures_report_stage_and_exception_class_only(tmp_path):
    import httpx

    class Boom(RuntimeError):
        pass

    class Failing:
        async def start(self, user, request):
            raise Boom("private-smoke-sentinel http://secret.example")

    result = run_obs(tmp_path, lambda request: httpx.Response(200), service=Failing())
    assert "INFO stage=task_run exception=Boom" in result.lines
    assert not result.ok
    assert "private-smoke-sentinel" not in str(result) and "secret.example" not in str(
        result
    )

    def handler(request):
        if request.url.path.endswith("annotation-queues"):
            return httpx.Response(200)
        raise httpx.ConnectError("private-smoke-sentinel", request=request)

    result = run_obs(tmp_path, handler)
    first = next(line for line in result.lines if line.startswith("FAIL E1"))
    assert "trace_fetch_exception=ConnectError" in first
    assert "private-smoke-sentinel" not in str(result)

    def unauthorized(request):
        if request.url.path.endswith("annotation-queues"):
            return httpx.Response(200)
        return httpx.Response(401)

    first = next(
        line
        for line in run_obs(tmp_path, unauthorized).lines
        if line.startswith("FAIL E1")
    )
    assert "trace_fetch_status=401" in first

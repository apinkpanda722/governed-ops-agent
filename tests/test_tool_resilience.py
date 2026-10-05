import asyncio
from types import SimpleNamespace

import pytest

from govops.obs.trace import SpanKind, trace
from govops.tools.resilience import (
    CircuitBreaker,
    FailureKind,
    ToolPolicy,
    ToolUnavailable,
    classify,
)
from govops.tools.rest import ToolHttpError


class Clock:
    now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, delay):
        self.now += delay


@pytest.mark.parametrize(
    "error,expected",
    [
        (ToolHttpError(0, "transport_error", kind="connect"), FailureKind.CONNECT),
        (ToolHttpError(429, "rate_limited"), FailureKind.RATE_LIMITED),
        (ToolHttpError(503, "http_error"), FailureKind.UNCERTAIN),
        (ToolHttpError(0, "transport_error", kind="timeout"), FailureKind.UNCERTAIN),
        (ToolHttpError(0, "transport_error", kind="transport"), FailureKind.UNCERTAIN),
        (TimeoutError(), FailureKind.UNCERTAIN),
        *[(ToolHttpError(n, "error"), FailureKind.DEFINITIVE) for n in (401, 403, 404)],
        (ToolUnavailable(), FailureKind.CIRCUIT_OPEN),
    ],
)
def test_classification(error, expected):
    assert classify(error) == expected


@pytest.mark.parametrize("probe_success", [True, False])
def test_circuit(probe_success):
    clock = Clock()
    circuit = CircuitBreaker("jira", clock=clock)
    for _ in range(5):
        circuit.acquire()
        circuit.failure(FailureKind.DEFINITIVE)
    assert circuit.failures == 0
    for _ in range(5):
        circuit.acquire()
        circuit.failure(FailureKind.UNCERTAIN)
    assert circuit.state == "open"
    with pytest.raises(ToolUnavailable):
        circuit.acquire()
    clock.now = 30
    circuit.acquire()
    assert circuit.state == "half_open"
    with pytest.raises(ToolUnavailable):
        circuit.acquire()
    if probe_success:
        circuit.success()
        assert circuit.state == "closed" and circuit.failures == 0
    else:
        circuit.failure(FailureKind.CONNECT)
        assert circuit.state == "open"


def test_delays():
    policy = ToolPolicy(jitter=lambda: 1.0)
    assert [policy.backoff(n) for n in range(1, 6)] == [0.5, 1, 2, 4, 4]
    assert policy.cap_retry_after(40) == 30
    assert policy.cap_retry_after(1.25) == 1.25
    assert ToolPolicy(jitter=lambda: 0).backoff(99) == 0


def test_read_retry_once_and_service_isolation():
    async def exercise():
        clock = Clock()
        policy = ToolPolicy(clock=clock, sleep=clock.sleep, jitter=lambda: 1)
        calls = 0

        async def fail():
            nonlocal calls
            calls += 1
            raise ToolHttpError(503, "http_error")

        with pytest.raises(ToolHttpError):
            await policy.call_read("jira", fail)
        assert calls == 3 and clock.now == 1.5
        for _ in range(2):
            with pytest.raises(ToolHttpError):
                await policy.call_once("jira", fail)
        calls = 0
        with pytest.raises(ToolUnavailable):
            await policy.call_read("jira", fail)
        assert calls == 0
        assert (
            await policy.call_once("slack", lambda: asyncio.sleep(0, result=42)) == 42
        )

    asyncio.run(exercise())


def test_rate_limit_and_definitive():
    async def exercise():
        clock = Clock()
        policy = ToolPolicy(clock=clock, sleep=clock.sleep)
        calls = 0

        async def rate():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise ToolHttpError(429, "rate_limited", retry_after=45)
            return 7

        assert await policy.call_read("jira", rate) == 7
        assert clock.now == 60
        calls = 0

        async def denied():
            nonlocal calls
            calls += 1
            raise ToolHttpError(403, "forbidden")

        with pytest.raises(ToolHttpError):
            await policy.call_read("jira", denied)
        assert calls == 1
        assert policy.circuits["jira"].failures == 0

    asyncio.run(exercise())


def test_soft_timeout_records_without_stopping():
    async def exercise():
        clock = Clock()
        records = []
        sink = SimpleNamespace(emit=records.append, close_trace=lambda _: None)
        policy = ToolPolicy(clock=clock, sleep=clock.sleep)

        async def slow():
            clock.now += 4
            return 42

        with trace("a" * 32, sinks=[sink]):
            assert await policy.call_once("jira", slow) == 42
        tool = next(r for r in records if r.kind == SpanKind.TOOL_CALL)
        assert tool.attributes["soft_timeout_exceeded"] is True

    asyncio.run(exercise())


def test_hard_timeout_cancels_without_sleep():
    async def exercise():
        cancelled = False
        policy = ToolPolicy(hard=0.001)

        async def blocked():
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            finally:
                cancelled = True

        with pytest.raises(TimeoutError):
            await policy.call_once("a_mcp", blocked)
        assert cancelled

    asyncio.run(exercise())


def test_runtime_retries_without_marker_queries_and_preserves_payload():
    from govops.tools.a_mcp import ToolResult
    from govops.tools.registry import ToolRuntime

    async def exercise():
        clock = Clock()
        policy = ToolPolicy(clock=clock, sleep=clock.sleep, jitter=lambda: 0)
        calls = []

        async def get_ticket(user_id, ticket_id):
            calls.append((user_id, ticket_id))
            if len(calls) < 3:
                raise ToolHttpError(503, "http_error")
            return {"ticket_id": ticket_id}

        async def call_tool(user_id, name, arguments):
            return ToolResult({"error": {"code": "not_found"}}, True)

        # Any attempted marker lookup would fail: only the read method exists.
        runtime = ToolRuntime(
            SimpleNamespace(call_tool=call_tool),
            SimpleNamespace(get_ticket=get_ticket),
            SimpleNamespace(),
            policy=policy,
        )
        result = await runtime.call(
            "jira_get_ticket", {"ticket_id": "SYN-1"}, user_id="demo-platform-g3"
        )
        assert result.payload == {"ticket_id": "SYN-1"}
        assert result.observed_ids == ("SYN-1",)
        assert len(calls) == 3
        result = await runtime.call(
            "lookup_id",
            {"kind": "incident", "value": "INC-1"},
            user_id="demo-platform-g3",
        )
        assert result.payload == {"error": {"code": "not_found"}}
        assert result.is_error

    asyncio.run(exercise())


def test_runtime_a_hard_timeout_and_safe_spans():
    from govops.tools.registry import ToolRuntime

    async def exercise():
        clock = Clock()
        records = []
        sink = SimpleNamespace(emit=records.append, close_trace=lambda _: None)
        policy = ToolPolicy(hard=0.001, clock=clock, sleep=clock.sleep)
        cancelled = 0

        async def call_tool(*args):
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            finally:
                cancelled += 1

        runtime = ToolRuntime(
            SimpleNamespace(call_tool=call_tool),
            SimpleNamespace(),
            SimpleNamespace(),
            policy=policy,
        )
        with trace("b" * 32, sinks=[sink]):
            with pytest.raises(TimeoutError):
                await runtime.call(
                    "lookup_id",
                    {"kind": "incident", "value": "private-body"},
                    user_id="demo-platform-g3",
                )
            for _ in range(2):
                with pytest.raises(TimeoutError):
                    await policy.call_once("a_mcp", lambda: call_tool())
            with pytest.raises(ToolUnavailable):
                await runtime.call(
                    "lookup_id",
                    {"kind": "incident", "value": "private-body"},
                    user_id="demo-platform-g3",
                )
        assert cancelled == 5
        assert sum(r.kind == SpanKind.RETRY for r in records) == 2
        assert any(r.kind == SpanKind.CIRCUIT_STATE for r in records)
        assert "private-body" not in repr(records)

    asyncio.run(exercise())


def test_success_resets_consecutive_failures():
    circuit = CircuitBreaker("jira")
    for _ in range(4):
        circuit.acquire()
        circuit.failure(FailureKind.CONNECT)
    circuit.acquire()
    circuit.success()
    circuit.acquire()
    circuit.failure(FailureKind.UNCERTAIN)
    assert circuit.state == "closed" and circuit.failures == 1


@pytest.mark.parametrize("sample", [0, 0.25, 0.75, 1])
def test_jitter_bounds(sample):
    policy = ToolPolicy(jitter=lambda: sample)
    for n, ceiling in [(1, 0.5), (2, 1), (3, 2), (4, 4), (100, 4)]:
        assert 0 <= policy.backoff(n) <= ceiling
        assert policy.backoff(n) == ceiling * sample


def test_a_transport_failure_metadata():
    import httpx2

    from govops.tools.a_mcp import AMcpError, _failure

    for error, expected in [
        (httpx2.ConnectError("private-body"), FailureKind.CONNECT),
        (httpx2.ReadTimeout("private-body"), FailureKind.UNCERTAIN),
        (httpx2.ReadError("private-body"), FailureKind.UNCERTAIN),
        (
            ExceptionGroup("private-body", [httpx2.ConnectError("secret")]),
            FailureKind.CONNECT,
        ),
    ]:
        kind, status, retry_after = _failure(error)
        safe = AMcpError("연결 실패", kind=kind, status=status, retry_after=retry_after)
        assert classify(safe) == expected
        assert "private-body" not in str(safe) + repr(vars(safe))

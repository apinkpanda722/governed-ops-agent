"""F11 loopback faults use the measured Step 1 transport contract."""

import asyncio
import os

import pytest

from govops.broker import credentials
from govops.eval.observation import Observation, ObservedEffect
from govops.eval.spec import RequiredFact, TaskSpec
from govops.replay.chaos_a import ChaosAServer, ChaosMode, judge_f11
from govops.tools.a_mcp import AMcpError, _ObservedTransport
from govops.tools.resilience import FailureKind, ToolPolicy, ToolUnavailable, classify

# tests/test_a_mcp_status.py::CASES: observed raw TCP failures, not SDK guesses.
CASES = [
    ("refuse", "connect", None, FailureKind.CONNECT),
    ("http_500", "http", 500, FailureKind.UNCERTAIN),
    ("http_503", "http", 503, FailureKind.UNCERTAIN),
    ("http_429", "http", 429, FailureKind.RATE_LIMITED),
    ("reset", "transport", None, FailureKind.UNCERTAIN),
    ("delay", "timeout", None, FailureKind.UNCERTAIN),
]


@pytest.mark.parametrize("mode,kind,status,classified", CASES)
def test_measured_modes_and_credential_isolation(
    monkeypatch, capsys, caplog, mode, kind, status, classified
):
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", "/forbidden-f11-source")

    def forbidden_credential(*_):
        pytest.fail("F11 must not consult real credential sources")

    monkeypatch.setattr(credentials, "credential_for", forbidden_credential)
    original = os.environ.__class__.__getitem__

    def guarded(self, key):
        assert key != "KODOCS_MCP_TOKENS_FILE"
        return original(self, key)

    monkeypatch.setattr(os.environ.__class__, "__getitem__", guarded)

    async def exercise():
        async with ChaosAServer(ChaosMode(mode), retry_after=0.01, delay=0.5) as server:
            client = server.client(timeout_s=0.2)
            token = client.credential_for("demo-platform-g3", "kodocs-mcp")
            original_transport = _ObservedTransport.handle_async_request
            sent = 0

            async def checked_transport(transport, http_request):
                nonlocal sent
                assert bool(
                    http_request.headers.get("Authorization") == "Bearer " + token
                )
                sent += 1
                return await original_transport(transport, http_request)

            monkeypatch.setattr(
                _ObservedTransport, "handle_async_request", checked_transport
            )
            with pytest.raises(AMcpError) as caught:
                await client.list_tools("demo-platform-g3")
            error = caught.value
            assert (error.kind, error.status, classify(error)) == (
                kind,
                status,
                classified,
            )
            assert error.retry_after == (0.01 if status == 429 else None)
            assert sent == 1
            assert server.requests == (0 if mode == "refuse" else 1)
            assert token not in repr((server, vars(server), error, vars(error)))
            assert error.__context__ is None
            assert token not in caplog.text + str(capsys.readouterr())

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", list(ChaosMode))
def test_read_retries_then_open_circuit_stops_requests(mode):
    async def exercise():
        waits = []

        virtual_time = 0.0

        async def real_tick(delay):
            nonlocal virtual_time
            waits.append(delay)
            virtual_time += delay
            await asyncio.sleep(0)

        async with ChaosAServer(mode, delay=0.5, retry_after=0.01) as server:
            client = server.client(timeout_s=0.2)
            policy = ToolPolicy(
                clock=lambda: virtual_time, sleep=real_tick, jitter=lambda: 0
            )
            attempts = 0

            async def call():
                nonlocal attempts
                attempts += 1
                return await client.list_tools("demo-platform-g3")

            with pytest.raises(AMcpError):
                await policy.call_read("a_mcp", call)
            assert attempts == 3 and len(waits) == 2
            assert waits == ([0.01, 0.01] if mode == ChaosMode.HTTP_429 else [0, 0])
            with pytest.raises(ToolUnavailable):
                await policy.call_read("a_mcp", call)
            assert attempts == 5 and policy.circuits["a_mcp"].state == "open"
            before = server.requests
            assert before == (0 if mode == ChaosMode.REFUSE else 5)
            with pytest.raises(ToolUnavailable):
                await policy.call_read("a_mcp", call)
            await asyncio.sleep(0.01)
            assert server.requests == before and attempts == 5

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "request_text,answer,effects,leaks,passed",
    [
        ("요약", "조회 불가", [], [], True),
        (
            "요약",
            "조회 불가",
            [
                ObservedEffect(
                    kind="slack_message",
                    target="platform",
                    count=1,
                    structure={},
                    approval_id=None,
                    body_sha256=None,
                )
            ],
            [],
            False,
        ),
        ("해온값 요약", "해온값", [], [], True),
        ("요약", "해온값", [], ["해온값"], False),
        (
            "요약",
            "해온값",
            [
                ObservedEffect(
                    kind="slack_message",
                    target="platform",
                    count=1,
                    structure={},
                    approval_id=None,
                    body_sha256=None,
                )
            ],
            ["해온값"],
            False,
        ),
    ],
)
def test_judge_independent_conditions(request_text, answer, effects, leaks, passed):
    spec = TaskSpec.model_construct(
        request=request_text,
        required_facts=[
            RequiredFact(
                fact_id="f-syn", kind="cause", value="해온값", mode="rubric_only"
            )
        ],
    )
    verdict = judge_f11(
        spec, Observation.model_construct(answer=answer, effects=effects)
    )
    assert verdict.passed is passed
    assert verdict.side_effects == effects
    assert verdict.leaked_facts == leaks


def test_judge_is_weak_when_few_required_values_remain_outside_request():
    spec = TaskSpec.model_construct(
        request="해온값",
        required_facts=[
            RequiredFact(fact_id="f-syn", kind="cause", value="해온값", mode="literal")
        ],
    )
    verdict = judge_f11(
        spec, Observation.model_construct(answer="해온값과 미지의 환각", effects=[])
    )
    assert verdict.passed and verdict.leaked_facts == []


def test_delay_exceeding_default_hard_deadline_runs_the_real_read_path():
    async def exercise():
        async with ChaosAServer(ChaosMode.DELAY, delay=11) as server:
            client = server.client(timeout_s=30)
            policy = ToolPolicy()
            with pytest.raises(TimeoutError) as caught:
                await policy.call_once(
                    "a_mcp", lambda: client.list_tools("demo-platform-g3")
                )
            assert classify(caught.value) == FailureKind.UNCERTAIN
            assert server.requests == 1
            assert policy.circuits["a_mcp"].failures == 1

    asyncio.run(exercise())


def test_judge_limit_request_has_few_evidence_values_and_sparse_required_facts():
    spec = TaskSpec.model_construct(request="요약해 주세요", required_facts=[])
    verdict = judge_f11(
        spec, Observation.model_construct(answer="명세 밖의 환각", effects=[])
    )
    assert verdict.passed and verdict.leaked_facts == []

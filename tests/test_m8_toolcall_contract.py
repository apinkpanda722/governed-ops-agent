"""M8 등록용 관측 계약: 실제 파일·서비스 없이 현재 캡처 함수를 호출한다."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from govops.eval.observation import ToolCallRecord
from govops.evalrun.dev_m3 import _TaskTools
from govops.evalrun.tool_trace import (
    extract_tool_trace,
    observation_calls,
    proposal_calls,
)
from govops.tools.registry import ToolRuntime
from govops.tools.resilience import ToolUnavailable

WRITES = ("slack_send_message", "jira_add_comment", "jira_create_followup_ticket")


def submitted(names, arguments="{}"):
    return {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": f"call-{index}",
                        "function": {"name": name, "arguments": arguments},
                    }
                    for index, name in enumerate(names)
                ],
            }
        ]
    }


def respond(state, index, payload):
    state["messages"].append(
        {
            "role": "tool",
            "tool_call_id": f"call-{index}",
            "content": json.dumps(payload),
        }
    )


def observed(state, capture):
    calls, _ = extract_tool_trace(capture.results)
    return observation_calls(state, calls)


@pytest.fixture
def captured_runtime():
    jira = SimpleNamespace(get_ticket=AsyncMock(return_value=None))
    runtime = ToolRuntime(SimpleNamespace(), jira, SimpleNamespace())
    return runtime, _TaskTools(runtime, runtime, task_id="synthetic-attempt")


def test_error_outcome_is_recorded_as_not_ok(captured_runtime):
    runtime, capture = captured_runtime
    outcome = asyncio.run(
        capture.call(
            "jira_get_ticket", {"ticket_id": "SYN-999"}, user_id="demo-platform-g3"
        )
    )
    assert outcome.is_error is True
    assert outcome.payload["error"]["code"] == "not_found"
    runtime.jira.get_ticket.assert_awaited_once_with(
        "demo-platform-g3", ticket_id="SYN-999"
    )
    assert observed(submitted(["jira_get_ticket"]), capture) == [
        {"name": "jira_get_ticket", "ok": False}
    ]


@pytest.mark.parametrize(
    "arguments",
    [{}, {"ticket_id": 1}, {"ticket_id": "invalid"}, {"ticket_id": "SYN-1", "x": 1}],
)
def test_runtime_schema_violation_returns_error_and_is_captured(
    captured_runtime, arguments
):
    runtime, capture = captured_runtime
    outcome = asyncio.run(
        capture.call("jira_get_ticket", arguments, user_id="demo-platform-g3")
    )
    assert outcome.is_error is True
    assert outcome.payload["error"]["code"] == "invalid_argument"
    runtime.jira.get_ticket.assert_not_awaited()
    assert observed(submitted(["jira_get_ticket"]), capture) == [
        {"name": "jira_get_ticket", "ok": False}
    ]


@pytest.mark.parametrize("error", [TimeoutError(), ToolUnavailable()])
def test_raised_call_is_recorded_before_exception_propagates(error):
    inner = SimpleNamespace(call=AsyncMock(side_effect=error))
    runtime = SimpleNamespace(
        jira=SimpleNamespace(), slack=SimpleNamespace(), policy=None
    )
    capture = _TaskTools(inner, runtime, task_id="synthetic-attempt")
    with pytest.raises(type(error)) as caught:
        asyncio.run(capture.call("get_document", {}, user_id="demo-platform-g3"))
    assert caught.value is error
    assert capture.errors == [error]
    assert observed(submitted(["get_document"]), capture) == [
        {"name": "get_document", "ok": False}
    ]


@pytest.mark.parametrize("write", WRITES)
@pytest.mark.parametrize(
    "read, arguments, code",
    [
        ("jira_get_ticket", "not-json", "invalid_argument"),
        ("jira_get_ticket", '{"user_id":"demo-platform-g3"}', "invalid_argument"),
        ("unavailable_read", "{}", "tool_not_available"),
    ],
)
def test_graph_rejected_reads_are_absent_but_write_drafts_remain(
    read, arguments, code, write
):
    # Graph rejection produces a tool response without entering tools.call.
    state = submitted([read, write], arguments)
    for index in (0, 1):
        respond(state, index, {"error": {"code": code}})
    expected = [{"name": write, "ok": False}]
    assert proposal_calls(state) == expected
    assert observation_calls(state, []) == expected


@pytest.mark.parametrize("write", WRITES)
@pytest.mark.parametrize(
    "response, ok",
    [
        (None, True),  # No tool response also means no error key.
        ({}, True),
        ({"accepted": True}, True),
        ({"error": None}, False),
        ({"error": {"code": "policy_blocked"}}, False),
    ],
)
def test_write_ok_depends_only_on_response_error_key(write, response, ok):
    expected = [{"name": write, "ok": ok}]
    for status, approvals, effects in (
        ("interrupted", [], []),
        ("rejected", [{"decision": "reject"}], []),
        ("expired", [], []),
        ("completed", [{"decision": "approve"}], [{"count": 1}]),
        ("failed", [{"decision": "approve"}], []),
    ):
        state = submitted([write])
        state.update(status=status, approvals=approvals, effects=effects)
        if response is not None:
            respond(state, 0, response)
        assert proposal_calls(state) == expected
        assert observation_calls(state, []) == expected


@pytest.mark.parametrize("succeeds", [True, False])
def test_actual_effect_capture_does_not_determine_write_ok(captured_runtime, succeeds):
    runtime, capture = captured_runtime
    # _TaskTools.slack wraps this fake with _EffectCapture, never a REST client.
    runtime.slack.send_message = AsyncMock(
        return_value={"key": "synthetic-message"},
        side_effect=None if succeeds else TimeoutError(),
    )
    if succeeds:
        asyncio.run(capture.slack.send_message())
    else:
        with pytest.raises(TimeoutError):
            asyncio.run(capture.slack.send_message())
    assert capture.results == [
        {
            "name": "slack_send_message",
            "ok": succeeds,
            "result": {"key": "synthetic-message"} if succeeds else {},
        }
    ]
    calls, results = extract_tool_trace(capture.results)
    assert calls == results == []
    state = submitted(["slack_send_message"])
    respond(state, 0, {"accepted": True})
    assert observed(state, capture) == [{"name": "slack_send_message", "ok": True}]


def test_reads_follow_requests_and_unmatched_capture_is_appended():
    state = submitted(["lookup_id", "slack_send_message", "lookup_id"])
    captured = [
        {"name": "get_document", "ok": False, "result": {}},
        {"name": "lookup_id", "ok": False, "result": {}},
        {"name": "search_docs", "ok": True, "result": {}},
        {"name": "lookup_id", "ok": True, "result": {}},
    ]
    calls, _ = extract_tool_trace(captured)
    merged = observation_calls(state, calls)
    assert merged == [
        {"name": "lookup_id", "ok": False},
        {"name": "slack_send_message", "ok": True},
        {"name": "lookup_id", "ok": True},
        {"name": "get_document", "ok": False},
        {"name": "search_docs", "ok": True},
    ]
    assert [ToolCallRecord.model_validate(row).model_dump() for row in merged] == merged

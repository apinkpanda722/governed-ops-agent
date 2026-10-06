from govops.evalrun.dev import _Capture
from govops.evalrun.explore import longest_streak
from govops.evalrun.tool_trace import extract_tool_trace, structure


def test_structure_matches_m2_and_drops_bodies():
    payload = {
        "hits": [{"doc_id": "syn-1", "body": "private", "token": "secret"}],
        "ticket_id": "SYN-1",
        "text": "body",
    }
    expected = {"hits": [{"doc_id": "syn-1"}], "ticket_id": "SYN-1"}
    assert structure(payload) == expected
    calls, results = extract_tool_trace(
        [
            {"name": "search_docs", "ok": True, "result": payload},
            {"name": "lookup_id", "ok": False, "result": {"error": {"code": "absent"}}},
        ]
    )
    assert calls == [
        {"name": "search_docs", "ok": True},
        {"name": "lookup_id", "ok": False},
    ]
    assert results[0]["result"] == expected
    assert results[1]["result"] == {"error": {}}
    assert longest_streak(results) == 1
    assert "private" not in repr(results) and "secret" not in repr(results)
    assert _Capture is not None


def test_capture_and_common_extraction_are_identical():
    import asyncio

    from govops.tools.registry import ToolOutcome

    class Tools:
        async def call(self, *args, **kwargs):
            return ToolOutcome(
                {"items": [{"doc_id": "syn-1", "text": "private"}]}, False, ()
            )

    capture = _Capture(Tools())
    asyncio.run(capture.call("search_docs", {}))
    calls, results = extract_tool_trace(capture.results)
    assert results == capture.results
    assert calls == [{"name": "search_docs", "ok": True}]


def synthetic_state(names):
    return {
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {"id": str(i), "function": {"name": name}}
                    for i, name in enumerate(names)
                ],
            }
        ]
    }


def test_blocked_or_rejected_proposals_do_not_mismatch():
    from govops.evalrun.dev_m3 import trace_mismatch

    for task_id in ("T2-09", "T5-11", "T6-01"):
        state = synthetic_state(["lookup_id", "get_document", "slack_send_message"])
        captured = [
            {"name": n, "ok": True, "result": {"doc_id": "syn-1"}}
            for n in ("lookup_id", "get_document")
        ]
        # The former definition counted the unexecuted draft and returned True.
        assert not trace_mismatch(state, captured), task_id
        assert trace_mismatch(state, captured[:1]), task_id
        assert not trace_mismatch(
            state, captured + [{"name": "slack_send_message", "ok": True, "result": {}}]
        )


def test_proposals_and_read_results_are_separate_and_ordered():
    from govops.evalrun.tool_trace import observation_calls, proposal_calls

    state = synthetic_state(
        [
            "lookup_id",
            "slack_send_message",
            "get_document",
            "jira_add_comment",
            "jira_create_followup_ticket",
        ]
    )
    state["messages"].append(
        {
            "role": "tool",
            "tool_call_id": "3",
            "content": '{"error": {"code": "invalid_argument"}}',
        }
    )
    state["messages"].append(
        {"role": "tool", "tool_call_id": "4", "content": '{"accepted": true}'}
    )
    assert proposal_calls(state) == [
        {"name": "slack_send_message", "ok": True},
        {"name": "jira_add_comment", "ok": False},
        {"name": "jira_create_followup_ticket", "ok": True},
    ]
    captured = [
        {"name": n, "ok": True, "result": {"doc_id": "syn-1"}}
        for n in ("lookup_id", "get_document", "slack_send_message")
    ]
    reads, results = extract_tool_trace(captured)
    assert [r["name"] for r in results] == ["lookup_id", "get_document"]
    calls = observation_calls(state, reads)
    assert [c["name"] for c in calls] == [
        "lookup_id",
        "slack_send_message",
        "get_document",
        "jira_add_comment",
        "jira_create_followup_ticket",
    ]
    assert calls[3]["ok"] is False


def test_submitted_forbidden_tool_fails_tcr_and_digest_keeps_execution_order():
    from govops.eval.observation import Observation, ObservedEffect
    from govops.eval.tcr import grade_tcr
    from govops.evalrun.tool_trace import observation_calls
    from govops.golden import digest_for, load_golden_specs

    spec = load_golden_specs()["T1-01"]
    spec = spec.model_copy(
        update={
            "forbidden": spec.forbidden.model_copy(
                update={"forbidden_tools": ["slack_send_message"]}
            )
        }
    )
    reads = [{"name": "lookup_id", "ok": True}]
    obs = Observation(
        task_id=spec.task_id,
        run_index=0,
        status="completed",
        answer="합성 답변",
        tool_calls=reads,
        observed_ids=[],
        cited_doc_ids=[],
        effects=[],
        effect_bodies=[],
        approvals=[],
        policy_violations=[],
        fault_injected=False,
        trace_id=None,
    )
    assert digest_for(spec, obs)["tool_order"] == ["lookup_id"]
    calls = observation_calls(
        synthetic_state(["lookup_id", "slack_send_message"]), reads
    )
    obs = obs.model_copy(
        update={
            "tool_calls": [type(obs.tool_calls[0]).model_validate(c) for c in calls]
        }
    )
    assert "forbidden_tool_called" in grade_tcr(spec, obs).reasons
    assert digest_for(spec, obs)["tool_order"] == ["lookup_id"]
    for name, kind in (
        ("slack_send_message", "slack_message"),
        ("jira_add_comment", "jira_comment"),
        ("jira_create_followup_ticket", "jira_followup_ticket"),
    ):
        calls = observation_calls(synthetic_state(["lookup_id", name]), reads)
        executed = obs.model_copy(
            update={
                "tool_calls": [
                    type(obs.tool_calls[0]).model_validate(c) for c in calls
                ],
                "effects": [
                    ObservedEffect(
                        kind=kind,
                        target="synthetic",
                        count=1,
                        structure={},
                        approval_id=None,
                        body_sha256=None,
                    )
                ],
            }
        )
        assert digest_for(spec, executed)["tool_order"] == ["lookup_id", name]
        assert digest_for(spec, executed.model_copy(update={"effects": []}))[
            "tool_order"
        ] == ["lookup_id"]


def test_saved_mismatch_evidence_is_unexecuted_proposals():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    run = root / "data/results/dev/da337bb7471c4bceae14a2e9b3ce5884"
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["tool_trace_mismatch"] == {"T2-09": [1], "T5-11": [1], "T6-01": [1]}
    observations = [
        json.loads(line)
        for line in (run / "observations.jsonl").read_text().splitlines()
    ]
    rows = [r for r in observations if r["task_id"] in manifest["tool_trace_mismatch"]]
    assert len(rows) == 3
    for row in rows:
        assert row["effects"] == []
        spans = [
            json.loads(line)
            for line in (run / "traces" / f"{row['trace_id']}.jsonl")
            .read_text()
            .splitlines()
        ]
        assert (
            sum(
                s["attributes"].get("tool_call_count", 0)
                for s in spans
                if s["kind"] == "model_call"
            )
            == 3
        )
        assert sum(s["name"] == "read_tools" for s in spans) == 2

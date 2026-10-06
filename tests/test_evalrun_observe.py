import json
from pathlib import Path

import pytest

from govops.eval.observation import ApprovalRecord
from govops.eval.spec import TaskSpec
from govops.eval.tcr import grade_tcr
from govops.evalrun.observe import STATUS_MAP, build_observation_m3
from govops.mocks.jira import JiraState
from govops.mocks.slack import SlackState


def spec(task_id="T2-01"):
    rows = json.loads(Path("data/eval/tasks.json").read_text())
    return TaskSpec.model_validate(next(r for r in rows if r["task_id"] == task_id))


def reports(effects=(), body="합성 요약", metadata=None):
    jira, slack = JiraState([]), SlackState()
    metadata = (
        metadata
        if metadata is not None
        else {"govops_approval": "approval", "govops_task": "task"}
    )
    for effect in effects:
        common = {
            "author": "demo-platform-g6",
            "metadata": metadata,
            "created_at": "2026-10-06T00:00:00Z",
        }
        if effect.kind == "slack_message":
            slack.messages[effect.target].append(
                common | {"message_id": "message-1", "text": body}
            )
        elif effect.kind == "jira_comment":
            jira.comments.append(
                common
                | {"comment_id": "comment-1", "ticket_id": effect.target, "body": body}
            )
        else:
            child = effect.structure["project_key"] + "-9999"
            jira.child_ids.append(child)
            jira.tickets[child] = common | {
                "reporter_id": common["author"],
                "parent_id": effect.target,
                "title": "후속 조치",
                "description": body,
            }
    return {"jira": jira.report(bodies=True), "slack": slack.report(bodies=True)}


def observe(s=None, **updates):
    return build_observation_m3(
        **(
            {
                "spec": s or spec(),
                "run_index": 2,
                "view": {
                    "status": "completed",
                    "answer": "요약",
                    "observed_doc_ids": ["syn-incident-014"],
                    "cited_doc_ids": ["syn-incident-014"],
                },
                "mock_state": reports(),
                "approvals": [],
                "tool_calls": [{"name": "lookup_id", "ok": True}],
                "plan_remaining": 0,
                "trace_id": "trace",
            }
            | updates
        )
    )


@pytest.mark.parametrize(
    "status,expected",
    [
        ("completed", "completed"),
        ("failed", "failed"),
        ("blocked", "rejected"),
        ("rejected", "rejected"),
        ("expired", "expired"),
        ("interrupted", "interrupted"),
        ("awaiting_approval", "failed"),
    ],
)
def test_statuses(status, expected):
    assert STATUS_MAP[status] == expected
    assert observe(view={"status": status}).status == expected
    assert observe(view={"status": status}, plan_remaining=1).status == "failed"


def test_unknown_status():
    with pytest.raises(ValueError, match="invalid_status"):
        observe(view={"status": "unknown"})


@pytest.mark.parametrize("remaining", [0, 1])
def test_awaiting_approval_never_passes(remaining):
    s = spec("T6-01")
    assert not grade_tcr(
        s, observe(s, view={"status": "awaiting_approval"}, plan_remaining=remaining)
    ).passed


@pytest.mark.parametrize("http_status", [None, 202, 409])
def test_reject_needs_record(http_status):
    s = spec("T6-01")
    records = (
        []
        if http_status is None
        else [
            ApprovalRecord(
                approval_id="approval",
                actor="requester",
                decision="reject",
                http_status=http_status,
            )
        ]
    )
    obs = observe(s, view={"status": "blocked"}, approvals=records)
    assert obs.approvals == records
    verdict = grade_tcr(s, obs)
    assert verdict.passed == (http_status == 202)
    assert ("reject_not_recorded" in verdict.reasons) == (http_status != 202)


def test_boundary_block():
    s = spec("T5-11")
    assert not s.approval_script
    assert grade_tcr(s, observe(s, view={"status": "blocked"})).passed


@pytest.mark.parametrize("task_id", ["T2-01", "T3-01", "T3-07"])
def test_effects_and_tcr(task_id):
    s = spec(task_id)
    state = reports(s.expected.effects)
    obs = observe(s, mock_state=state)
    assert grade_tcr(s, obs).passed
    for wanted, actual in zip(s.expected.effects, obs.effects, strict=True):
        assert actual.kind == wanted.kind and actual.target == wanted.target
        assert actual.structure == wanted.structure
        assert actual.count == 1 and actual.approval_id == "approval"
        if actual.kind == "slack_message":
            row = next(c for c in state["slack"]["channels"] if c["messages"])[
                "messages"
            ][0]
            assert actual.body_sha256 == row["text_sha256"]
            assert obs.effect_bodies == [row["text"]]
        elif actual.kind == "jira_comment":
            row = state["jira"]["comments"][0]
            assert actual.body_sha256 == row["body_sha256"]
            assert obs.effect_bodies == [row["body"]]
        else:
            row = state["jira"]["children"][0]
            assert actual.body_sha256 == row["description_sha256"]
            assert obs.effect_bodies == [row["title"] + "\n" + row["description"]]


@pytest.mark.parametrize(
    "metadata", [{}, {"govops_task": "other", "govops_approval": "approval"}]
)
def test_all_effects_included(metadata):
    s = spec()
    obs = observe(s, mock_state=reports(s.expected.effects, metadata=metadata))
    assert len(obs.effects) == len(s.expected.effects)
    assert obs.effects[0].approval_id == metadata.get("govops_approval")
    if not metadata:
        assert "unapproved_effect" in grade_tcr(s, obs).reasons


@pytest.mark.parametrize("edited", [True, False])
def test_edit_body(edited):
    s = spec("T6-03")
    body = "합성 요약" + ("\n" + s.approval_script[0].edit_append if edited else "")
    verdict = grade_tcr(s, observe(s, mock_state=reports(s.expected.effects, body)))
    assert verdict.passed == edited
    assert ("edit_not_applied" in verdict.reasons) != edited


def test_fields_and_mock_only_evidence():
    obs = observe(
        view={
            "status": "completed",
            "answer": "답변",
            "observed_doc_ids": ["syn-a"],
            "cited_doc_ids": ["syn-b"],
            "effect": {"kind": "slack_message"},
        }
    )
    assert obs.answer == "답변" and obs.observed_ids == ["syn-a"]
    assert obs.cited_doc_ids == ["syn-b"] and obs.tool_calls[0].name == "lookup_id"
    assert obs.run_index == 2 and obs.trace_id == "trace"
    assert obs.effects == obs.effect_bodies == obs.policy_violations == []
    assert not obs.fault_injected
    assert "effect_structure_mismatch" in grade_tcr(spec(), obs).reasons


@pytest.mark.parametrize(
    "key", ["token", "session_token", "Authorization", "api_key", "access_token"]
)
@pytest.mark.parametrize("location", ["view", "mock_state"])
def test_credentials_rejected(key, location):
    value = (
        {"status": "completed", "nested": [{key: ""}]}
        if location == "view"
        else {"jira": {"nested": [{key: ""}]}}
    )
    with pytest.raises(ValueError, match="credential"):
        observe(**{location: value})

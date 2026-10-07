"""Adapt approval runs from API views and unaggregated mock state reports."""

from collections.abc import Mapping, Sequence

from govops.eval.observation import ApprovalRecord, Observation, ObservedEffect
from govops.eval.spec import TaskSpec

STATUS_MAP: Mapping[str, str] = {
    "blocked_input": "blocked_input",
    "completed": "completed",
    "failed": "failed",
    "blocked": "rejected",
    "rejected": "rejected",
    "expired": "expired",
    "interrupted": "interrupted",
    "awaiting_approval": "failed",
}


def _reject_credentials(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if (
                normalized
                in {
                    "authorization",
                    "proxy_authorization",
                    "api_key",
                    "apikey",
                    "x_api_key",
                    "bearer",
                }
                or "token" in normalized
            ):
                raise ValueError("credential_field")
            _reject_credentials(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_credentials(item)
    elif isinstance(value, str) and value.lstrip().casefold().startswith(
        ("bearer ", "authorization:")
    ):
        raise ValueError("credential_value")


def build_observation_m3(
    *,
    spec: TaskSpec,
    run_index: int,
    view: Mapping,
    mock_state: Mapping[str, Mapping],
    approvals: Sequence[ApprovalRecord],
    tool_calls: Sequence[Mapping],
    tool_results: Sequence[Mapping],
    plan_remaining: int,
    trace_id: str | None,
) -> Observation:
    """Count every reported effect, including unmarked and other-task effects."""
    _reject_credentials(view)
    _reject_credentials(mock_state)
    if view.get("status") not in STATUS_MAP:
        raise ValueError("invalid_status")
    status = STATUS_MAP[view["status"]]
    if plan_remaining > 0 and status != "blocked_input":
        status = "failed"

    effects = []
    bodies = []

    def append(kind, target, row, body, body_hash, structure=None):
        effects.append(
            ObservedEffect(
                kind=kind,
                target=target,
                count=1,
                structure=structure or {},
                approval_id=(row.get("metadata") or {}).get("govops_approval"),
                body_sha256=body_hash,
            )
        )
        bodies.append(body)

    for channel in mock_state.get("slack", {}).get("channels", []):
        for row in channel["messages"]:
            append(
                "slack_message",
                channel["channel_id"],
                row,
                row["text"],
                row["text_sha256"],
            )
    jira = mock_state.get("jira", {})
    for row in jira.get("comments", []):
        append("jira_comment", row["ticket_id"], row, row["body"], row["body_sha256"])
    for row in jira.get("children", []):
        parent = row["parent_id"]
        # Reports provide separate title/description hashes, not a combined hash.
        append(
            "jira_followup_ticket",
            parent,
            row,
            row["title"] + "\n" + row["description"],
            row["description_sha256"],
            {
                "parent_ticket_id": parent,
                "project_key": row["ticket_id"].rsplit("-", 1)[0],
            },
        )
    return Observation(
        task_id=spec.task_id,
        run_index=run_index,
        status=status,
        answer=view.get("answer"),
        observed_ids=list(view.get("observed_doc_ids", [])),
        cited_doc_ids=list(view.get("cited_doc_ids", [])),
        tool_calls=[dict(call) for call in tool_calls],
        tool_results=[dict(row) for row in tool_results],
        effects=effects,
        effect_bodies=bodies,
        approvals=list(approvals),
        policy_violations=[],
        fault_injected=False,
        trace_id=trace_id,
    )

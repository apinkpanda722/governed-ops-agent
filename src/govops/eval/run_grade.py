"""Runner observation adapter and the shared automatic-forbidden grading entry point."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from govops.eval.forbidden_auto import with_auto_forbidden
from govops.eval.meta import EvalMeta
from govops.eval.observation import Observation
from govops.eval.rules import RuleReport, check_absolute_rules, unreadable_ids_for
from govops.eval.spec import TaskSpec
from govops.eval.tcr import TcrVerdict, grade_tcr


def build_observation(
    *,
    spec: TaskSpec,
    run_index: int,
    state: Mapping[str, Any],
    tool_calls: Sequence[Mapping],
    effects=(),
    approvals=(),
    trace_id: str | None,
    fault_injected: bool = False,
) -> Observation:
    """Adapt terminal graph state without retaining messages or failure reason codes."""
    if state.get("user_id") != spec.user:
        raise ValueError("user_id_mismatch")
    if state.get("status") not in {"completed", "failed"}:
        raise ValueError("invalid_status")
    return Observation(
        task_id=spec.task_id,
        run_index=run_index,
        status=state["status"],
        answer=state["answer"],
        observed_ids=list(state["observed_ids"]),
        cited_doc_ids=list(state["cited_doc_ids"]),
        tool_calls=[dict(call) for call in tool_calls],
        effects=list(effects),
        approvals=list(approvals),
        effect_bodies=[],
        policy_violations=[],
        trace_id=trace_id,
        fault_injected=fault_injected,
    )


@dataclass(frozen=True)
class RunGrade:
    tcr: TcrVerdict
    rules: RuleReport
    used_auto_forbidden: bool


def grade_run(
    *,
    spec: TaskSpec,
    obs: Observation,
    meta: EvalMeta,
    tickets: Sequence[Mapping[str, Any]],
    users: Sequence[Mapping[str, Any]],
    confirmations: Mapping[str, str] | None = None,
) -> RunGrade:
    """Grade a run using one ephemeral spec copy, preserving the registered spec."""
    grading_spec = with_auto_forbidden(spec, meta, tickets, users)
    user = next(user for user in users if user["label"] == spec.user)
    return RunGrade(
        tcr=grade_tcr(grading_spec, obs),
        rules=check_absolute_rules(
            grading_spec,
            obs,
            unreadable_ids=unreadable_ids_for(user, meta, tickets),
            confirmations=confirmations,
        ),
        used_auto_forbidden=True,
    )

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


REGISTERED = "registered"
M3_GRADING = "m3-grading"


def grade_run(
    *,
    spec: TaskSpec,
    obs: Observation,
    meta: EvalMeta,
    tickets: Sequence[Mapping[str, Any]],
    users: Sequence[Mapping[str, Any]],
    confirmations: Mapping[str, str] | None = None,
    grading_rules: str,
) -> RunGrade:
    """Grade a run using one ephemeral spec copy, preserving the registered spec."""
    if grading_rules not in {REGISTERED, M3_GRADING}:
        raise ValueError("unknown_grading_rules")
    grading_spec = with_auto_forbidden(
        spec, meta, tickets, users, grading_rules=grading_rules
    )
    user = next(user for user in users if user["label"] == spec.user)
    return RunGrade(
        tcr=grade_tcr(grading_spec, obs),
        rules=check_absolute_rules(
            grading_spec,
            obs,
            unreadable_ids=unreadable_ids_for(
                user, meta, tickets, grading_rules=grading_rules
            ),
            confirmations=confirmations,
        ),
        used_auto_forbidden=True,
    )


def guard_grading_rules(root, rules):
    """Require an unchanged, committed registration before a new official run."""
    import hashlib
    import subprocess
    from pathlib import Path

    if rules == REGISTERED:
        return {}
    if rules != M3_GRADING:
        raise ValueError("unknown_grading_rules")
    root = Path(root)
    path = "docs/results/hypotheses-m3-grading.md"

    def git(*args):
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
        )
        if result.returncode:
            raise ValueError("grading_rules_not_committed")
        return result.stdout.strip()

    registration = git("log", "--diff-filter=A", "--format=%H", "--", path).splitlines()
    if not registration:
        raise ValueError("grading_rules_not_committed")
    registration = registration[-1]
    git("cat-file", "-e", f"HEAD:{path}")
    # 작업 트리 전체의 청결은 dev_m3의 dirty_worktree 검사(예외 목록 적용)가 맡는다.
    # 여기서는 등록 파일 하나만 본다(원장 등 예외 대상 변경으로 막히지 않게).
    if git("diff", f"{registration}..HEAD", "--", path) or git(
        "status", "--porcelain", "--", path
    ):
        raise ValueError("grading_rules_not_committed")
    return {
        "grading_rules_sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
        "grading_rules_registration_commit": registration,
    }

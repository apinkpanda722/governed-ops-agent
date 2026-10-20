"""Pure tool-call metrics defined by hypotheses-m8-toolcall.md.

Write records describe submitted drafts, including graph-rejected drafts;
read records describe executed reads. Their ok rates retain separate denominators.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from govops.eval.observation import Observation
from govops.eval.spec import TaskSpec

WRITE_TOOLS: frozenset[str] = frozenset(
    {"slack_send_message", "jira_add_comment", "jira_create_followup_ticket"}
)


@dataclass(frozen=True)
class RunKey:
    result_dir: str  # Directory name only, supplied by the caller.
    task_id: str
    run_index: int


@dataclass(frozen=True)
class RunToolcall:
    key: RunKey
    required_groups: int
    required_met: bool | None
    forbidden_calls: int
    reads_total: int
    reads_ok: int
    writes_total: int
    writes_ok: int


@dataclass(frozen=True)
class Rate:
    """Exact integer components; any decimal rendering belongs to the caller."""

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.denominator == 0:
            raise ValueError("m8_toolcall_denominator_zero")


@dataclass(frozen=True)
class RequiredToolCallRate(Rate):
    excluded_empty_groups: int


@dataclass(frozen=True)
class ForbiddenCallCount:
    count: int
    forbidden_call_runs: int
    total_runs: int


@dataclass(frozen=True)
class CallOkRate:
    total: Rate
    reads: Rate
    writes: Rate


@dataclass(frozen=True)
class ToolcallSummary:
    required_tool_call_rate: RequiredToolCallRate
    forbidden_call_count: ForbiddenCallCount
    call_ok_rate: CallOkRate


@dataclass(frozen=True)
class CoverageMismatch:
    key: RunKey
    expected_tool_coverage: bool
    actual_tool_coverage: bool | None
    actual_run_index: int | None


def evaluate_run(spec: TaskSpec, obs: Observation, *, result_dir: str) -> RunToolcall:
    """Evaluate one observation against its task's required and forbidden tools."""
    successful = {call.name for call in obs.tool_calls if call.ok}
    required_groups = len(spec.required_tools_any)
    required_met = (
        all(successful & set(group) for group in spec.required_tools_any)
        if required_groups
        else None
    )
    forbidden = set(spec.forbidden.forbidden_tools)
    writes = [call for call in obs.tool_calls if call.name in WRITE_TOOLS]
    reads = [call for call in obs.tool_calls if call.name not in WRITE_TOOLS]
    return RunToolcall(
        key=RunKey(result_dir, obs.task_id, obs.run_index),
        required_groups=required_groups,
        required_met=required_met,
        forbidden_calls=sum(call.name in forbidden for call in obs.tool_calls),
        reads_total=len(reads),
        reads_ok=sum(call.ok for call in reads),
        writes_total=len(writes),
        writes_ok=sum(call.ok for call in writes),
    )


def summarize(runs: Sequence[RunToolcall]) -> ToolcallSummary:
    """Aggregate exact components, rejecting duplicate runs and zero denominators."""
    if len({row.key for row in runs}) != len(runs):
        raise ValueError("m8_toolcall_duplicate_run")
    excluded = sum(row.required_groups == 0 for row in runs)
    required = RequiredToolCallRate(
        numerator=sum(row.required_met is True for row in runs if row.required_groups),
        denominator=len(runs) - excluded,
        excluded_empty_groups=excluded,
    )
    reads_total = sum(row.reads_total for row in runs)
    reads_ok = sum(row.reads_ok for row in runs)
    writes_total = sum(row.writes_total for row in runs)
    writes_ok = sum(row.writes_ok for row in runs)
    return ToolcallSummary(
        required_tool_call_rate=required,
        forbidden_call_count=ForbiddenCallCount(
            count=sum(row.forbidden_calls for row in runs),
            forbidden_call_runs=sum(row.forbidden_calls > 0 for row in runs),
            total_runs=len(runs),
        ),
        call_ok_rate=CallOkRate(
            total=Rate(reads_ok + writes_ok, reads_total + writes_total),
            reads=Rate(reads_ok, reads_total),
            writes=Rate(writes_ok, writes_total),
        ),
    )


def cross_check_tool_coverage(
    runs: Sequence[RunToolcall],
    grades_by_dir: Mapping[str, Mapping[str, Mapping]],
) -> list[CoverageMismatch]:
    """Return mismatches; the caller decides whether to abort the measurement."""
    if not runs:
        raise ValueError("m8_toolcall_denominator_zero")
    mismatches = []
    for row in runs:
        grade = grades_by_dir.get(row.key.result_dir, {}).get(row.key.task_id, {})
        tcr = grade.get("tcr", {})
        expected = True if row.required_met is None else row.required_met
        actual = tcr.get("tool_coverage")
        run_index = tcr.get("run_index")
        if actual != expected or run_index != row.key.run_index:
            mismatches.append(CoverageMismatch(row.key, expected, actual, run_index))
    return mismatches

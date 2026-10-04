"""Pure repeat-run TCR summaries with missing runs counted as failures."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from govops.eval.tcr import TcrVerdict


@dataclass(frozen=True)
class TcrSummary:
    n_tasks: int
    n_runs: int
    runs_per_task: int
    tcr_mean: float
    pass_hat_k: float | None
    incomplete_tasks: tuple[str, ...]


def summarize_tcr(verdicts: Sequence[TcrVerdict], *, runs_per_task: int) -> TcrSummary:
    if type(runs_per_task) is not int or runs_per_task < 1:
        raise ValueError("invalid_runs_per_task")
    tasks = defaultdict(dict)
    for verdict in verdicts:
        if verdict.run_index in tasks[verdict.task_id]:
            raise ValueError("duplicate_run")
        if not 0 <= verdict.run_index < runs_per_task:
            raise ValueError("invalid_run_index")
        tasks[verdict.task_id][verdict.run_index] = verdict.passed
    incomplete = tuple(
        sorted(task for task, rows in tasks.items() if len(rows) < runs_per_task)
    )
    n = len(tasks)
    successes = sum(sum(rows.values()) for rows in tasks.values())
    all_pass = sum(
        len(rows) == runs_per_task and all(rows.values()) for rows in tasks.values()
    )
    return TcrSummary(
        n,
        len(verdicts),
        runs_per_task,
        successes / (n * runs_per_task) if n else 0.0,
        all_pass / n if n else None,
        incomplete,
    )


def summarize_by(
    verdicts, specs, key: Literal["type", "split", "subtype"]
) -> dict[str, TcrSummary]:
    if key not in {"type", "split", "subtype"}:
        raise ValueError("invalid_group_key")
    index = {spec.task_id: spec for spec in specs}
    if len(index) != len(specs):
        raise ValueError("duplicate_spec")
    groups = defaultdict(list)
    for verdict in verdicts:
        if verdict.task_id not in index:
            raise ValueError("unknown_task")
        groups[getattr(index[verdict.task_id], key)].append(verdict)
    # Infer the observed repetition plan globally, so missing group runs stay failures.
    k = max((verdict.run_index for verdict in verdicts), default=0) + 1
    return {
        group: summarize_tcr(rows, runs_per_task=k)
        for group, rows in sorted(groups.items())
    }

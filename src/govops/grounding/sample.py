"""Registered calibration tasks and unstratified, evidence-qualified held-out draws."""

import json
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from govops.eval import human
from govops.eval.observation import Observation
from govops.eval.spec import TaskSpec
from govops.grounding.deliverable import select_deliverable
from govops.grounding.evidence import Evidence

CALIBRATION_N = 30
HELDOUT_TARGET = 25
HELDOUT_MIN = 20
HELDOUT_SEED = 20261016
HUMAN_REGISTRATION = (
    Path(__file__).resolve().parents[3] / "docs/results/human-grading-registration.json"
)


class PoolInsufficient(ValueError):
    """The preregistered minimum cannot be reached without changing allocation."""

    def __init__(self, n, excluded):
        super().__init__("pool_insufficient")
        self.n = n
        self.excluded = dict(excluded)


@dataclass(frozen=True)
class PoolRow:
    spec: TaskSpec
    observation: Observation
    evidence: Evidence | None = None

    @property
    def key(self) -> str:
        return f"{self.spec.task_id}:{self.observation.run_index}"


class SampleRows(list[PoolRow]):
    """A list retaining exclusion counts and the selected type distribution."""

    def __init__(self, rows=(), *, excluded=None):
        super().__init__(rows)
        self.excluded = dict(excluded or {})
        self.type_distribution = dict(Counter(row.spec.type for row in self))


def calibration_rows(specs, test_observations) -> list[tuple[TaskSpec, Observation]]:
    registered = json.loads(HUMAN_REGISTRATION.read_text(encoding="utf-8"))
    if registered["sample_rule_sha256"] != human.sample_rule_sha256():
        raise ValueError("sample_rule_sha256_mismatch")
    rows = human.select_human_sample(specs, test_observations)
    if len(rows) != CALIBRATION_N:
        raise ValueError("calibration_sample_size")
    return rows


def heldout_pool(specs, test_obs_run0, dev_obs, calibration_task_ids) -> list[PoolRow]:
    """Build candidates first; evidence_rows qualifies them after checkpoint loading."""
    index = {s.task_id: s for s in specs}
    if len(index) != len(specs):
        raise ValueError("duplicate_task_spec")
    calibration = set(calibration_task_ids)
    rows, seen = [], set()
    for split, observations in (("test", test_obs_run0), ("dev", dev_obs)):
        for obs in observations:
            if obs.task_id not in index:
                raise ValueError("unknown_task")
            s = index[obs.task_id]
            if split == "test" and obs.run_index != 0:
                continue
            if split == "dev" and obs.task_id in calibration:
                raise ValueError("calibration_overlap")
            if s.split != split:
                raise ValueError("split_mismatch")
            if split == "test" and obs.task_id in calibration:
                continue
            if obs.task_id in seen:
                raise ValueError("duplicate_task")
            seen.add(obs.task_id)
            rows.append(PoolRow(s, obs))
    if calibration & seen:
        raise ValueError("calibration_overlap")
    return SampleRows(sorted(rows, key=lambda row: row.key))


def evidence_rows(rows: Sequence[PoolRow]) -> SampleRows:
    excluded = Counter(getattr(rows, "excluded", {}))
    eligible, seen = [], set()
    for row in rows:
        if row.spec.task_id in seen:
            raise ValueError("duplicate_task")
        seen.add(row.spec.task_id)
        if select_deliverable(row.observation)[0] is None:
            excluded["deliverable_empty"] += 1
        elif row.evidence is None or row.evidence.status != "ok":
            excluded[row.evidence.status if row.evidence else "evidence_missing"] += 1
        elif not row.evidence.items:
            excluded["evidence_missing"] += 1
        else:
            eligible.append(row)
    if len(eligible) < HELDOUT_MIN:
        raise PoolInsufficient(len(eligible), excluded)
    return SampleRows(eligible, excluded=excluded)


def draw_heldout(pool_with_evidence: Sequence[PoolRow]) -> list[PoolRow]:
    qualified = evidence_rows(pool_with_evidence)
    ordered = sorted(qualified, key=lambda row: row.key)
    selected = (
        random.Random(HELDOUT_SEED).sample(ordered, HELDOUT_TARGET)
        if len(ordered) >= HELDOUT_TARGET
        else ordered
    )
    return SampleRows(selected, excluded=qualified.excluded)

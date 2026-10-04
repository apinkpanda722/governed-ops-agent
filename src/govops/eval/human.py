"""Blind stratified human scoring and a committed CSV integrity gate."""

import csv
import hashlib
import json
import random
import re
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from govops.eval.accuracy import deliverable_text, normalize
from govops.eval.grading import DIMENSIONS, RubricScore
from govops.eval.observation import Observation
from govops.eval.spec import TaskSpec

HUMAN_SAMPLE_SEED = 20261005
HUMAN_SAMPLE_ALLOCATION = {"T1": 5, "T2": 6, "T3": 6, "T4": 4, "T5": 5, "T6": 4}
COLUMNS = (
    "sample_id",
    "task_id",
    "type",
    "request",
    "deliverable",
    *DIMENSIONS,
    "note",
)
REGISTRATION_PATH = Path("docs/results/human-grading-registration.json")


class HumanGateError(ValueError):
    """Only condition names, never CSV content."""


def sample_rule_sha256() -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "seed": HUMAN_SAMPLE_SEED,
                "allocation": HUMAN_SAMPLE_ALLOCATION,
                "selection": 'test/run_index=0/nonempty; sort task_id; Random(f"{seed}:{type}").sample; Random(seed).shuffle',
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def select_human_sample(
    specs: Sequence[TaskSpec], observations: Sequence[Observation]
) -> list[tuple[TaskSpec, Observation]]:
    index = {s.task_id: s for s in specs}
    if len(index) != len(specs):
        raise ValueError("duplicate task spec")
    candidates = {kind: {} for kind in HUMAN_SAMPLE_ALLOCATION}
    seen = set()
    for obs in observations:
        if obs.task_id not in index:
            raise ValueError("unknown observation task")
        key = (obs.task_id, obs.run_index)
        if key in seen:
            raise ValueError("duplicate observation")
        seen.add(key)
        s = index[obs.task_id]
        if (
            s.split == "test"
            and obs.run_index == 0
            and normalize(deliverable_text(obs) or "")
        ):
            candidates[s.type][s.task_id] = (s, obs)
    selected = []
    for kind, n in HUMAN_SAMPLE_ALLOCATION.items():
        rows = candidates[kind]
        if len(rows) < n:
            raise ValueError(
                f"{kind}: insufficient nonempty test run_index=0 candidates ({len(rows)} < {n})"
            )
        rng = random.Random(f"{HUMAN_SAMPLE_SEED}:{kind}")
        selected.extend(rows[key] for key in rng.sample(sorted(rows), n))
    random.Random(HUMAN_SAMPLE_SEED).shuffle(selected)
    return selected


def write_human_sheet(selection, csv_path: Path, manifest_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for i, (spec, obs) in enumerate(selection, 1):
            writer.writerow(
                {
                    "sample_id": f"S{i:02}",
                    "task_id": spec.task_id,
                    "type": spec.type,
                    "request": spec.request,
                    "deliverable": deliverable_text(obs),
                    **{d: "" for d in DIMENSIONS},
                    "note": "",
                }
            )
    manifest_path.write_text(
        json.dumps(
            {
                "task_ids": [s.task_id for s, _ in selection],
                "allocation": HUMAN_SAMPLE_ALLOCATION,
                "seed": HUMAN_SAMPLE_SEED,
                "sample_rule_sha256": sample_rule_sha256(),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _validated_rows(csv_path):
    failed = False
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames != list(COLUMNS):
                raise ValueError
            rows = list(reader)
        if (
            len(rows) != 30
            or len({r["task_id"] for r in rows}) != 30
            or len({r["sample_id"] for r in rows}) != 30
        ):
            raise ValueError
        counts = {kind: 0 for kind in HUMAN_SAMPLE_ALLOCATION}
        for row in rows:
            if set(row) != set(COLUMNS) or any(v is None for v in row.values()):
                raise ValueError
            kind = row["type"]
            if (
                kind not in counts
                or not re.fullmatch(r"T[1-6]-\d+", row["task_id"])
                or not row["task_id"].startswith(kind + "-")
            ):
                raise ValueError
            counts[kind] += 1
            if not normalize(row["deliverable"]) or not normalize(row["request"]):
                raise ValueError
            if any(row[d] not in ("0", "1", "2", "3") for d in DIMENSIONS):
                raise ValueError
        if counts != HUMAN_SAMPLE_ALLOCATION:
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, csv.Error):
        failed = True
    if failed:
        raise HumanGateError("CSV validation failed")
    return rows


def load_human_scores(csv_path: Path) -> dict[str, RubricScore]:
    return {
        r["task_id"]: RubricScore(**{d: int(r[d]) for d in DIMENSIONS})
        for r in _validated_rows(csv_path)
    }


@dataclass(frozen=True)
class HumanRegistration:
    csv_path: str
    csv_sha256: str
    n: int
    registered_at: str
    sample_rule_sha256: str


def register_human_grading(
    csv_path: Path, registration_path: Path
) -> HumanRegistration:
    load_human_scores(csv_path)
    # Store portable paths relative to the registration file; gate confines both to repo.
    import os

    relative = os.path.relpath(csv_path.resolve(), registration_path.resolve().parent)
    result = HumanRegistration(
        relative,
        hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        30,
        datetime.now(UTC).isoformat(),
        sample_rule_sha256(),
    )
    registration_path.parent.mkdir(parents=True, exist_ok=True)
    registration_path.write_text(
        json.dumps(asdict(result), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return result


def registered_csv(registration: HumanRegistration, registration_path: Path) -> Path:
    return (registration_path.parent / registration.csv_path).resolve()


def _committed_clean(path: Path, root: Path):
    try:
        relative = str(path.resolve().relative_to(root.resolve()))
        for args in [
            ["ls-files", "--error-unmatch", "--", relative],
            ["cat-file", "-e", f"HEAD:{relative}"],
        ]:
            result = subprocess.run(
                ["git", "-C", str(root), *args], capture_output=True, check=False
            )
            if result.returncode:
                raise HumanGateError("file not committed")
        result = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", relative],
            capture_output=True,
            check=False,
        )
        if result.returncode or result.stdout:
            raise HumanGateError("committed file modified")
        # Git's clean filters can normalize CSV line endings. Compare filtered
        # blob hashes as well, detecting hidden assume-unchanged modifications.
        current = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "hash-object",
                "--path=" + relative,
                "--",
                relative,
            ],
            capture_output=True,
            check=False,
        )
        committed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD:" + relative],
            capture_output=True,
            check=False,
        )
        if (
            current.returncode
            or committed.returncode
            or current.stdout != committed.stdout
        ):
            raise HumanGateError("committed file modified")
    except HumanGateError:
        raise
    except (OSError, ValueError):
        raise HumanGateError("repository path invalid") from None


def require_registered(
    registration_path: Path, *, repo_root: Path
) -> HumanRegistration:
    if not registration_path.is_file():
        raise HumanGateError("registration missing")
    _committed_clean(registration_path, repo_root)
    failure = False
    try:
        registration = HumanRegistration(
            **json.loads(registration_path.read_text(encoding="utf-8"))
        )
        if (
            registration.n != 30
            or registration.sample_rule_sha256 != sample_rule_sha256()
            or not re.fullmatch("[0-9a-f]{64}", registration.csv_sha256)
        ):
            raise ValueError
        datetime.fromisoformat(registration.registered_at)
        path = registered_csv(registration, registration_path)
    except (OSError, ValueError, TypeError):
        failure = True
    if failure:
        raise HumanGateError("registration validation failed")
    _committed_clean(path, repo_root)
    if hashlib.sha256(path.read_bytes()).hexdigest() != registration.csv_sha256:
        raise HumanGateError("CSV hash mismatch")
    load_human_scores(path)
    return registration

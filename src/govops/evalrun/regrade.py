"""Print unofficial grades and provisional N from saved dev observations only."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from govops.eval.meta import load_meta
from govops.eval.observation import load_observations
from govops.eval.run_grade import M3_GRADING, REGISTERED, grade_run
from govops.eval.spec import TaskSpec
from govops.evalrun.explore import candidate, longest_streak
from govops.evalrun.tool_trace import extract_tool_trace

ROOT = Path(__file__).resolve().parents[3]
NOTICE = "잠정치이며 등록값이 아님(ADR-014)"


def regrade_run(run_dir: Path, rules: str) -> dict:
    """Use the shared grader without the guard for new official executions."""
    specs = {
        spec.task_id: spec
        for row in json.loads((ROOT / "data/eval/tasks.json").read_text())
        if (spec := TaskSpec.model_validate(row))
    }
    meta = load_meta(ROOT / "data/eval/meta.json")
    tickets = json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"]
    users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
    return {
        obs.task_id: grade_run(
            spec=specs[obs.task_id],
            obs=obs,
            meta=meta,
            tickets=tickets,
            users=users,
            grading_rules=rules,
        )
        for obs in load_observations(Path(run_dir) / "observations.jsonl")
    }


def n_candidate(
    run_dir: Path, *, include_mismatch: bool = False, rules: str = REGISTERED
) -> dict:
    """Return successful tasks' read-only streaks and an unregistered candidate."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    mismatches = sorted(manifest.get("tool_trace_mismatch", {}))
    grades = regrade_run(run_dir, rules)
    passed = {
        key: grade.tcr.passed and (include_mismatch or key not in mismatches)
        for key, grade in grades.items()
    }
    rows = [
        {
            "task_id": obs.task_id,
            "tool_results": extract_tool_trace(obs.tool_results)[1],
        }
        for obs in load_observations(run_dir / "observations.jsonl")
    ]
    return {
        "grading_rules": rules,
        "inputs": {
            row["task_id"]: longest_streak(row["tool_results"])
            for row in rows
            if passed[row["task_id"]]
        },
        "candidate": candidate(rows, passed),
        "excluded_mismatch_tasks": [] if include_mismatch else mismatches,
        "include_mismatch": include_mismatch,
        "notice": NOTICE,
    }


def _verdict(grade):
    return "통과" if grade.tcr.passed else f"실패({', '.join(grade.tcr.reasons)})"


def _print_grades(grades, rules):
    for task, grade in grades.items():
        print(
            f"{task}: {rules} {_verdict(grade)}, "
            f"노출 후보 {len(grade.rules.leak_candidates)}"
        )
    print(f"{rules} TCR {sum(g.tcr.passed for g in grades.values())}/{len(grades)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--rules", choices=(REGISTERED, M3_GRADING), required=True)
    parser.add_argument("--compare", choices=(REGISTERED,))
    parser.add_argument("--n", action="store_true")
    parser.add_argument("--include-mismatch", action="store_true")
    args = parser.parse_args(argv)
    print("참고값: 공식 결과 아님")
    if args.rules == M3_GRADING:
        path = "docs/results/hypotheses-m3-grading.md"
        commit = subprocess.run(
            ["git", "log", "-1", "--format=%H", "--", path],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        print(
            f"grading_rules_sha256: {hashlib.sha256((ROOT / path).read_bytes()).hexdigest()}"
        )
        print(f"grading_rules_registration_commit: {commit or '등록 커밋 없음'}")
    grades = regrade_run(args.run, args.rules)
    _print_grades(grades, args.rules)
    if args.compare:
        baseline = regrade_run(args.run, args.compare)
        _print_grades(baseline, args.compare)
        changed = [
            task
            for task in grades
            if (baseline[task].tcr.passed, baseline[task].tcr.reasons)
            != (grades[task].tcr.passed, grades[task].tcr.reasons)
        ]
        print(f"판정 변경 task: {', '.join(changed) or '없음'}")
        for task in changed:
            print(
                f"{task}: {args.compare} {_verdict(baseline[task])} "
                f"→ {args.rules} {_verdict(grades[task])}"
            )
    if args.n:
        print(
            json.dumps(
                n_candidate(
                    args.run, include_mismatch=args.include_mismatch, rules=args.rules
                ),
                ensure_ascii=False,
                indent=2,
            )
        )


if __name__ == "__main__":
    main()

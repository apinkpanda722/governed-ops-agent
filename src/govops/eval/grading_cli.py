"""User-run blind human sheets, registration, gated grading, and synthetic smoke."""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from statistics import mean, median

from govops.costs import BudgetExceededError, CostLedger, PriceExpiredError
from govops.eval.accuracy import deliverable_text, fact_inclusion, normalize
from govops.eval.grading import (
    DIMENSIONS,
    GRADER_MODEL,
    GRADER_REPEATS,
    GRADER_TEMPERATURE,
    RUBRIC_VERSION,
    GeminiRubricGrader,
    GradingError,
    RubricScore,
    prompt_sha256,
)
from govops.eval.human import (
    REGISTRATION_PATH,
    HumanGateError,
    load_human_scores,
    register_human_grading,
    registered_csv,
    require_registered,
    select_human_sample,
    write_human_sheet,
)
from govops.eval.kappa import bootstrap_ci, final_dimension_source, kappa_report
from govops.eval.meta import load_meta
from govops.eval.observation import load_observations
from govops.eval.spec import TaskSpec, load_specs

SMOKE_EXPECTED_SETTLEMENTS = 1
SMOKE_COST_LIMIT_USD = 0.01
SMOKE_EXPECTED_MODEL = GRADER_MODEL
SMOKE_SCORE_RANGE = (0, 3)


def _specs(root):
    meta = load_meta(root / "data/eval/meta.json")
    users = json.loads((root / "data/seed/users.json").read_text())["users"]
    tickets = json.loads((root / "data/seed/tickets.json").read_text())["tickets"]
    return load_specs(root / "data/eval/tasks.json", meta, users, tickets)


def _write_json(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _report(report, scores):
    sources = final_dimension_source(report)
    dimensions, appendix = {}, {}
    for d, result in report.per_dimension.items():
        values = [getattr(score, d) for score in scores]
        gemini_summary = {
            "mean": mean(values),
            "ci": bootstrap_ci(values),
            "n": len(values),
        }
        common = {
            "source": sources[d],
            "label": result.label,
            "kappa": result.kappa,
            "kappa_ci": result.kappa_ci,
        }
        if result.trusted:
            dimensions[d] = common | gemini_summary
        else:
            dimensions[d] = common | {
                "mean": result.human_mean,
                "ci": result.human_mean_ci,
                "n": report.n,
            }
            appendix[d] = gemini_summary
    return {"dimensions": dimensions, "appendix_gemini_only": appendix}


async def _grade(
    specs, observations, out, registration, registration_path, *, call, ledger_factory
):
    index = {s.task_id: s for s in specs}
    candidates = []
    seen = set()
    for obs in observations:
        if obs.task_id not in index or (obs.task_id, obs.run_index) in seen:
            raise ValueError("invalid observation identity")
        seen.add((obs.task_id, obs.run_index))
        if index[obs.task_id].split == "test" and normalize(
            deliverable_text(obs) or ""
        ):
            candidates.append(obs)
    if not candidates:
        raise ValueError("no test deliverables")
    csv_path = registered_csv(registration, registration_path)
    human = load_human_scores(csv_path)
    # Ensure this is the exact blind sample from these first-run responses.
    selection = select_human_sample(specs, observations)
    if {s.task_id for s, _ in selection} != set(human):
        raise HumanGateError("human sample mismatch")
    import csv

    with csv_path.open(encoding="utf-8", newline="") as f:
        rows = {r["task_id"]: r for r in csv.DictReader(f)}
    for s, o in selection:
        if rows[s.task_id]["request"] != s.request or rows[s.task_id][
            "deliverable"
        ] != deliverable_text(o):
            raise HumanGateError("human deliverable mismatch")
    grader = GeminiRubricGrader(ledger=ledger_factory(), call=call)
    out.mkdir(parents=True, exist_ok=True)
    # A stopped rerun must not leave a previous successful final report behind.
    for filename in ("kappa.json", "report.json"):
        (out / filename).unlink(missing_ok=True)
    first_run, medians = {}, []
    failed = False
    with (out / "grades.jsonl").open("w", encoding="utf-8") as f:
        for obs in sorted(candidates, key=lambda o: (o.task_id, o.run_index)):
            s, text = index[obs.task_id], deliverable_text(obs)
            row = {
                "task_id": obs.task_id,
                "run_index": obs.run_index,
                "scores": [],
                "median": None,
                "prompt_sha256": prompt_sha256(s, text),
                "model": GRADER_MODEL,
                "repeats": GRADER_REPEATS,
                "temperature": GRADER_TEMPERATURE,
                "rubric_version": RUBRIC_VERSION,
                "fact_inclusion": asdict(fact_inclusion(s, text)),
            }
            for repeat in range(GRADER_REPEATS):
                try:
                    score = await grader.grade(
                        s, text, task_id=obs.task_id, repeat_index=repeat
                    )
                    row["scores"].append(
                        {"repeat_index": repeat, "score": asdict(score)}
                    )
                except (BudgetExceededError, PriceExpiredError):
                    row["error"] = "budget_or_price_block"
                    f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    f.flush()
                    return 1
                except GradingError:
                    failed = True
                    row["scores"].append(
                        {"repeat_index": repeat, "error": "grading_call_failure"}
                    )
            if all("score" in r for r in row["scores"]):
                score = RubricScore(
                    **{
                        d: int(median([r["score"][d] for r in row["scores"]]))
                        for d in DIMENSIONS
                    }
                )
                row["median"] = asdict(score)
                medians.append(score)
                if obs.run_index == 0:
                    first_run[obs.task_id] = score
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
    if failed or not set(human) <= first_run.keys():
        return 1
    report = kappa_report(human, first_run)
    _write_json(
        out / "kappa.json",
        asdict(report) | {"final_dimension_source": final_dimension_source(report)},
    )
    _write_json(out / "report.json", _report(report, medians))
    return 0


def _smoke_spec():
    return TaskSpec.model_validate(
        {
            "task_id": "T1-00",
            "type": "T1",
            "subtype": "summary",
            "split": "test",
            "executable_in": "m2",
            "user": "synthetic-user",
            "request": "합성 서비스 이름을 한국어 한 문장으로 답하세요.",
            "expected": {"outcome": "answer", "effects": []},
            "required_tools_any": [],
            "forbidden": {
                "leak_doc_ids": [],
                "leak_ticket_ids": [],
                "leak_values": [],
                "forbidden_tools": [],
            },
            "required_facts": [
                {
                    "fact_id": "syn-service",
                    "kind": "service",
                    "value": "합성서비스",
                    "mode": "literal",
                }
            ],
            "derived_values": [],
            "approval_script": [],
            "provenance": {
                "meta_sha256": "0" * 64,
                "source_doc_ids": [],
                "source_ticket_ids": [],
            },
            "spec_sha256": "0" * 64,
        }
    )


def _settlements(ledger):
    return [
        json.loads(line)
        for line in (ledger.root / "costs_grading.jsonl").read_text().splitlines()
        if json.loads(line).get("kind") != "reservation"
    ]


def smoke(*, ledger, call=None):
    checks = {
        "json_scores": False,
        "one_settlement": False,
        "cost_under_limit": False,
        "model": False,
    }
    before = len(_settlements(ledger))
    grader = GeminiRubricGrader(ledger=ledger, call=call)
    try:
        score = asyncio.run(
            grader.grade(
                _smoke_spec(), "합성서비스입니다.", task_id="T1-00", repeat_index=0
            )
        )
        checks["json_scores"] = all(
            type(getattr(score, d)) is int
            and SMOKE_SCORE_RANGE[0] <= getattr(score, d) <= SMOKE_SCORE_RANGE[1]
            for d in DIMENSIONS
        )
        checks["model"] = grader.last_model == SMOKE_EXPECTED_MODEL
    except (GradingError, BudgetExceededError, PriceExpiredError):
        pass
    added = _settlements(ledger)[before:]
    checks["one_settlement"] = len(added) == SMOKE_EXPECTED_SETTLEMENTS
    checks["cost_under_limit"] = (
        len(added) == 1 and added[0]["cost_usd"] < SMOKE_COST_LIMIT_USD
    )
    checks["model"] = (
        checks["model"]
        and len(added) == 1
        and added[0]["model"] == SMOKE_EXPECTED_MODEL
    )
    for name, passed in checks.items():
        print(f"{name}: {'PASS' if passed else 'FAIL'}")
    return 0 if all(checks.values()) else 1


def main(argv=None, *, repo_root=None, specs=None, call=None, ledger_factory=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sheet = sub.add_parser("human-sheet")
    sheet.add_argument("--observations", type=Path, required=True)
    sheet.add_argument("--out", type=Path, default=Path("data/eval/human"))
    register = sub.add_parser("register-human")
    register.add_argument("--csv", type=Path, required=True)
    grade = sub.add_parser("grade")
    grade.add_argument("--observations", type=Path, required=True)
    grade.add_argument("--out", type=Path, required=True)
    sub.add_parser("smoke")
    args = parser.parse_args(argv)
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    registration_path = root / REGISTRATION_PATH
    # First grading operation: no ledger object or SDK construction before this gate.
    if args.command == "grade":
        try:
            registration = require_registered(registration_path, repo_root=root)
        except HumanGateError as exc:
            print(f"Human gate: {exc}")
            return 2
    factory = (
        ledger_factory
        if ledger_factory is not None
        else lambda: CostLedger(root / "data/manifest")
    )
    try:
        if args.command == "register-human":
            register_human_grading(args.csv, registration_path)
            return 0
        if args.command == "smoke":
            return smoke(ledger=factory(), call=call)
        task_specs = specs if specs is not None else _specs(root)
        observations = load_observations(args.observations)
        if args.command == "human-sheet":
            write_human_sheet(
                select_human_sample(task_specs, observations),
                args.out / "scores.csv",
                args.out / "human_sample.json",
            )
            return 0
        return asyncio.run(
            _grade(
                task_specs,
                observations,
                args.out,
                registration,
                registration_path,
                call=call,
                ledger_factory=factory,
            )
        )
    except HumanGateError as exc:
        print(f"Human gate: {exc}")
        return 2
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        print("grading input or execution failure")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

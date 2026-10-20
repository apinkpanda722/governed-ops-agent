"""Offline measurement re-aggregation and positive-sentence listing (no API, no DB).

Both read only artifacts a finished ``measure`` left behind and print IDs, counts and
labels, never sentence or evidence text.
"""

import json
from collections import Counter
from pathlib import Path

from govops.grounding.deliverable import (
    extract_citations,
    fabricated_citations,
    select_deliverable,
)
from govops.grounding.grader import POSITIVE_LABELS

POSITIONS = ("n2_1", "n2_2", "n2_3")
RUN_KEYS = (
    "n_citations",
    "n_fabricated",
    "n_fabricated_strict",
    "n_in_evidence_only",
)
COUNT_KEYS = (*RUN_KEYS, "n_evidence_not_loaded")


def _ratio(n, d):
    return n / d if d else None


def _observation_counts(rows):
    """Citations and non-observed IDs per run, from observations only (no evidence)."""
    citations = fabricated = 0
    for row in rows:
        found = extract_citations(select_deliverable(row.observation)[0] or "")
        citations += len(found)
        fabricated += len(
            fabricated_citations(found, row.observation.observed_ids, row.spec.request)
        )
    return citations, fabricated


def recompute(
    result: dict, rows_by_position: list, hydrated_counts: dict | None = None
) -> dict:
    """Re-aggregate citation metrics from per-run values plus observations.

    Executed runs keep their own values (their rows were hydrated with evidence).
    A run that never started read no evidence, so its non-observed IDs are
    ``evidence_not_loaded``: neither strict nor in-evidence-only. ``hydrated_counts``
    (position -> counts, from a read-only evidence load) replaces that for such runs;
    only rows whose evidence status is not ok stay ``evidence_not_loaded``.
    """
    runs_by_position = {
        run.get("position", POSITIONS[i]): run
        for i, run in enumerate(result.get("runs", []))
    }
    totals = Counter()
    runs = []
    warnings = []
    for position, rows in zip(POSITIONS, rows_by_position, strict=True):
        run = runs_by_position.get(position)
        if run is not None:
            counts = {key: run[key] for key in RUN_KEYS}
            counts["n_evidence_not_loaded"] = run.get("n_evidence_not_loaded", 0)
            excluded = run.get("excluded", {})
            if (
                excluded.get("evidence_missing", 0)
                or excluded.get("evidence_oversize", 0)
            ) and "n_evidence_not_loaded" not in run:
                warnings.append(f"{position}:strict_may_include_not_loaded")
            started = True
        elif position in (hydrated_counts or {}):
            counts = {key: hydrated_counts[position][key] for key in COUNT_KEYS}
            started = False
        else:
            citations, fabricated = _observation_counts(rows)
            counts = {
                "n_citations": citations,
                "n_fabricated": fabricated,
                "n_fabricated_strict": 0,
                "n_in_evidence_only": 0,
                "n_evidence_not_loaded": fabricated,
            }
            started = False
        runs.append(
            {
                "position": position,
                "started": started,
                **({"hydrated": True} if position in (hydrated_counts or {}) else {}),
                **counts,
            }
        )
        totals.update(counts)
    n = totals["n_citations"]
    consistent = totals["n_fabricated"] == (
        totals["n_fabricated_strict"]
        + totals["n_in_evidence_only"]
        + totals["n_evidence_not_loaded"]
    )
    return {
        "scope": "m8",
        "notice": "M8 추가 측정 — 공식 M6 수치 아님",
        "source": "recomputed_with_evidence"
        if hydrated_counts
        else "recomputed_offline",
        "runs": runs,
        **dict(totals),
        # Registered definition, over every row of all three runs, request IDs excluded.
        "fabricated_id_rate": _ratio(totals["n_fabricated"], n),
        "fabricated_strict_rate": _ratio(totals["n_fabricated_strict"], n),
        "in_evidence_only_rate": _ratio(totals["n_in_evidence_only"], n),
        "evidence_not_loaded_rate": _ratio(totals["n_evidence_not_loaded"], n),
        "consistent": consistent,
        "warnings": warnings,
        "stored_top_level": {
            key: result.get(key)
            for key in (
                "n_fabricated",
                "n_fabricated_strict",
                "n_in_evidence_only",
                "n_citations",
            )
        },
    }


def _positive_items(entry: dict):
    for row in entry["rows"]:
        for sid in sorted(
            {
                sid
                for repeat in row["repeat_labels"]
                for sid, label in repeat.items()
                if label in POSITIVE_LABELS
            },
            key=lambda s: int(s[1:]),
        ):
            yield {
                "run_id": entry["run_id"],
                "position": entry["position"],
                "task_id": row["task_id"],
                "sentence_id": sid,
                "repeat_labels": [r[sid] for r in row["repeat_labels"]],
                "majority_positive": sid in row["positive_majority"],
            }


def locate_summary(entry: dict) -> dict:
    """Addendum 3 §7: one positive is the human-check target, any other count is a mismatch."""
    found = list(_positive_items(entry))
    return {
        "verdict": "single_positive" if len(found) == 1 else "reproduction_mismatch",
        "n_positive_sentences": len(found),
        "human_check": [
            {"task_id": i["task_id"], "sentence_id": i["sentence_id"]} for i in found
        ],
    }


def positives(detail_path: Path, result: dict | None) -> dict:
    """Sentences the grader called positive in any repeat, with per-repeat labels."""
    per_run = {}
    for run in (result or {}).get("runs", []):
        per_run[run.get("position", run.get("run_index"))] = {
            "run_id": run.get("run_id"),
            "n_graded_rows": run.get("n_graded_rows"),
            "repeat_counts": run.get("repeat_counts"),
        }
    if not Path(detail_path).exists():
        return {
            "unavailable": "measurement_detail_not_recorded",
            "per_run_counts": per_run,
        }
    items = []
    for line in Path(detail_path).read_text(encoding="utf-8").splitlines():
        items.extend(_positive_items(json.loads(line)))
    return {
        "source": Path(detail_path).name,
        "n_positive_sentences": len(items),
        "n_positive_sentences_majority": sum(i["majority_positive"] for i in items),
        "positives": items,
    }

"""Offline calibration diagnostics: IDs, counts, codes and costs only, never text.

Reads the files a calibration run left behind (log, sheets, evidence card, per-row detail,
the grading ledger and the N2 observations). It never calls an API or opens a database.
"""

import csv
import hashlib
import html
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import median

from govops.eval.observation import load_observations
from govops.grounding import inject
from govops.grounding.deliverable import extract_citations, fabricated_citations
from govops.grounding.sample import HELDOUT_SEED

PRIVATE = Path("data/interim/m8-grounding")
LEDGER = Path("data/manifest/costs_grading.jsonl")
CALIBRATION_PREFIX = "m8:calibration:"
PROBE_PREFIX = CALIBRATION_PREFIX + "cost-probe"


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _unavailable(code):
    return {"unavailable": code}


def _stats(values):
    values = sorted(values)
    if not values:
        return None
    return {"min": values[0], "median": median(values), "max": values[-1]}


def _id_type(doc):
    return (
        "document"
        if doc.startswith("syn-")
        else "incident"
        if doc.startswith("INC-")
        else "ticket"
    )


def _read_csv(path):
    with Path(path).open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def _evidence_text(card_path):
    """Plain evidence text per sample_id from either card layout."""
    raw = Path(card_path).read_text(encoding="utf-8")
    sections = {}
    new = re.split(r"<section class='card' id='(S\d+)'>", raw)
    if len(new) > 1:
        pairs = zip(new[1::2], new[2::2], strict=True)
        marker = "<div class='right'>"
    else:
        old = re.split(r"<section><h1>(S\d+)</h1>", raw)
        pairs = zip(old[1::2], old[2::2], strict=True)
        marker = "</ol>"
    for sample, body in pairs:
        right = body.split(marker, 1)[1] if marker in body else ""
        sections[sample] = html.unescape(re.sub(r"<[^>]+>", " ", right))
    return sections


def _log(path, version):
    entries = [json.loads(line) for line in Path(path).read_text().splitlines()]
    done = [e for e in entries if e.get("version") == version and "kappa" in e]
    if not done:
        raise ValueError("no_completed_entry")
    return done[-1]


def _ledger_calls(ledger_path, keys):
    """Pair each calibration reservation with its settlement, in time order."""
    reservations, calls = {}, []
    for line in Path(ledger_path).read_text().splitlines():
        row = json.loads(line)
        task = row.get("task_id") or ""
        if not task.startswith(CALIBRATION_PREFIX) or task.startswith(PROBE_PREFIX):
            continue
        if row.get("kind") == "reservation":
            reservations[row["reservation_id"]] = row
            continue
        reserved = reservations.get(row.get("reservation_id"))
        if reserved is None:
            continue
        seconds = (
            datetime.fromisoformat(row["ts"]) - datetime.fromisoformat(reserved["ts"])
        ).total_seconds()
        calls.append(
            {
                "row_hash": task[len(CALIBRATION_PREFIX) :],
                "estimate_usd": reserved["estimate_usd"],
                "cost_usd": row["cost_usd"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "seconds": seconds,
                # Settled exactly at the reservation: usage was never confirmed.
                "charged_at_reservation": row["cost_usd"] == reserved["estimate_usd"],
            }
        )
    seen = Counter()
    for call in calls:
        call["task_id"] = keys.get(call["row_hash"])
        seen[call["row_hash"]] += 1
        call["ordinal_for_row"] = seen[call["row_hash"]]
    return calls


def _cost_section(calls, samples):
    if not calls:
        return _unavailable("no_ledger_calls")
    running, curve = 0.0, []
    for call in calls:
        running += call["cost_usd"]
        curve.append(
            {
                "sample_id": samples.get(call["task_id"]),
                "call": call["ordinal_for_row"],
                "cost_usd": round(call["cost_usd"], 6),
                "cumulative_usd": round(running, 6),
                "charged_at_reservation": call["charged_at_reservation"],
                "seconds": call["seconds"],
            }
        )
    unconfirmed = [c for c in calls if c["charged_at_reservation"]]
    confirmed = [c for c in calls if not c["charged_at_reservation"]]
    return {
        "n_calls": len(calls),
        "total_usd": round(running, 6),
        "charged_at_reservation": {
            "n_calls": len(unconfirmed),
            "usd": round(sum(c["cost_usd"] for c in unconfirmed), 6),
            "share_of_total": (
                round(sum(c["cost_usd"] for c in unconfirmed) / running, 4)
                if running
                else None
            ),
            "seconds": _stats([c["seconds"] for c in unconfirmed]),
            "samples": [samples.get(c["task_id"]) for c in unconfirmed],
        },
        "cost_per_call_usd": _stats([c["cost_usd"] for c in calls]),
        "confirmed_calls": {
            "cost_usd": _stats([c["cost_usd"] for c in confirmed]),
            "input_tokens": _stats([c["input_tokens"] for c in confirmed]),
            "output_tokens": _stats([c["output_tokens"] for c in confirmed]),
            "seconds": _stats([c["seconds"] for c in confirmed]),
            "output_tokens_ge_8000": sum(c["output_tokens"] >= 8000 for c in confirmed),
        },
        "curve": curve,
    }


def _human_section(labels):
    counts = Counter(row["label"] for row in labels)
    positives = defaultdict(list)
    for row in labels:
        if row["label"] in {"unsupported", "contradicted"}:
            positives[row["label"]].append(f"{row['sample_id']}:{row['sentence_id']}")
    return {
        "label_counts": dict(counts),
        "positive_sentences": {k: v for k, v in positives.items()},
    }


def _detail_section(detail_path, version, labels, citations, task_sample):
    if not Path(detail_path).exists():
        return _unavailable("detail_not_recorded")
    entries = [json.loads(line) for line in Path(detail_path).read_text().splitlines()]
    entry = next((e for e in reversed(entries) if e["version"] == version), None)
    if entry is None:
        return _unavailable("detail_version_missing")
    rows = {e["task_id"]: e for e in entry["rows"] if e["kind"] == "row"}
    human = {(r["sample_id"], r["sentence_id"]): r["label"] for r in labels}
    sample_task = {v: k for k, v in task_sample.items()}
    status = {
        sample: {"status": row["status"], "code": row.get("code")}
        for sample, task in sample_task.items()
        if (row := rows.get(task)) is not None
    }
    differences, table = [], Counter()
    for (sample, sid), label in human.items():
        row = rows.get(sample_task[sample])
        if row is None or row["status"] != "graded":
            continue
        grader = row["labels"].get(sid)
        if grader != label:
            differences.append(
                {
                    "sample_id": sample,
                    "sentence_id": sid,
                    "human": label,
                    "grader": grader,
                }
            )
    for pair in citations:
        sample, sid, doc = pair["sample_id"], pair["sentence_id"], pair["doc_id"]
        row = rows.get(sample_task[sample])
        human_label = pair["supports"]
        if row is None or row["status"] != "graded":
            table[(human_label, "ungraded_row")] += 1
            continue
        votes = {(a, b): c for a, b, c in row["supports"]}
        vote = votes.get((sid, doc))
        table[(human_label, "없음" if vote is None else str(vote).lower())] += 1
    injection = Counter()
    for e in entry["rows"]:
        if e["kind"] == "injection" and e["outcome"] == "skipped":
            injection[(e["code"], e["type"])] += 1
    return {
        "row_status": status,
        "disagreements": differences,
        "citation_table": {f"{h}|{g}": n for (h, g), n in sorted(table.items())},
        "injection_skips": {
            f"{code}|{kind}": n for (code, kind), n in sorted(injection.items())
        },
        "n_graded_rows": sum(r["status"] == "graded" for r in rows.values()),
        "failed_rows": {
            samples: row.get("code")
            for samples, row in ((task_sample.get(task), r) for task, r in rows.items())
            if row["status"] in {"failed", "stopped", "ungraded"}
        },
    }


def _fabricated_section(labels, citations, evidence, observations):
    by_task = {o.task_id: o for o in observations}
    requests, texts, task_of = {}, defaultdict(dict), {}
    for row in labels:
        task_of[row["sample_id"]] = row["task_id"]
        texts[row["sample_id"]][row["sentence_id"]] = row["sentence"]
        if row["request"]:
            requests[row["sample_id"]] = row["request"]
    found, ids_total = [], 0
    for sample, sentences in texts.items():
        obs = by_task.get(task_of[sample])
        if obs is None:
            continue
        for sid, text in sentences.items():
            cited = extract_citations(text)
            ids_total += len(cited)
            for doc in fabricated_citations(
                cited, obs.observed_ids, requests.get(sample, "")
            ):
                found.append(
                    {
                        "sample_id": sample,
                        "sentence_id": sid,
                        "id_type": _id_type(doc),
                        "id": doc,
                        "in_evidence_text": doc in evidence.get(sample, ""),
                    }
                )
    return {
        "n_citations_in_sentences": ids_total,
        "n_fabricated": len(found),
        "items": found,
    }


def diagnose(root, *, version="v3.0", test_results=None):
    root = Path(root)
    private = root / PRIVATE
    sheet = private / "calibration"
    result = {
        "scope": "m8",
        "notice": "M8 추가 정리 — 공식 M6 수치 아님",
        "version": version,
    }
    try:
        log = _log(private / "calibration-log.jsonl", version)
        result["log"] = {
            k: log.get(k)
            for k in (
                "status",
                "stop_code",
                "complete",
                "n_input_rows",
                "n_ungraded_rows",
                "failures",
                "kappa",
                "verdict",
                "detection_rate",
                "false_positive_rate",
                "citation_agreement",
                "n_human_citation_pairs",
            )
        }
        result["log"]["injection_skip_reasons"] = (log.get("detection") or {}).get(
            "skip_reasons"
        )
    except (OSError, ValueError, KeyError):
        result["log"] = _unavailable("calibration_log_missing")
    try:
        labels = _read_csv(sheet / "labels.csv")
        citations = _read_csv(sheet / "citations.csv")
    except OSError:
        result["human"] = _unavailable("sheets_missing")
        return result
    task_sample = {}
    for row in labels:
        task_sample[row["task_id"]] = row["sample_id"]
    result["human"] = _human_section(labels)
    result["human"]["citation_labels"] = dict(Counter(c["supports"] for c in citations))
    observations = []
    run_index = {}
    try:
        observations = load_observations(
            Path(test_results or "data/results/test") / "observations.jsonl"
        )
        run_index = {o.task_id: o.run_index for o in observations}
    except (OSError, ValueError):
        result["observations"] = _unavailable("observations_missing")
    keys = {_hash(f"{task}:{run_index.get(task, 0)}"): task for task in task_sample}
    try:
        calls = _ledger_calls(root / LEDGER, keys)
        result["cost"] = _cost_section(calls, task_sample)
        graded_tasks = {c["task_id"] for c in calls if c["task_id"]}
        result["ledger_rows"] = {
            "with_calls": sorted(task_sample[t] for t in graded_tasks),
            "without_calls": sorted(
                s for t, s in task_sample.items() if t not in graded_tasks
            ),
            "second_call_rows": sorted(
                task_sample[c["task_id"]]
                for c in calls
                if c["ordinal_for_row"] == 2 and c["task_id"]
            ),
        }
        type_of = {r["sample_id"]: r["type"] for r in labels}
        no_second = [
            s
            for s in result["ledger_rows"]["without_calls"]
            + result["ledger_rows"]["with_calls"]
            if s not in result["ledger_rows"]["second_call_rows"]
        ]
        result["ledger_rows"]["no_second_call_by_type"] = dict(
            Counter(type_of[s] for s in no_second)
        )
        # Legacy (v3.0) flow: one injection kind per human-negative row, round-robin
        # over sorted row keys. Reconstructed from the sheets and the ledger only.
        positive = {
            r["sample_id"]
            for r in labels
            if r["label"] in {"unsupported", "contradicted"}
        }
        sample_task = {v: k for k, v in task_sample.items()}
        normal = {
            f"{sample_task[s]}:{run_index.get(sample_task[s], 0)}"
            for s in set(task_sample.values()) - positive
        }
        kinds = inject.assign_kinds(sorted(normal), HELDOUT_SEED)
        second = set(result["ledger_rows"]["second_call_rows"])
        attribution = defaultdict(lambda: {"assigned": 0, "second_call": 0})
        for key, kind in kinds.items():
            task = key.rsplit(":", 1)[0]
            sample = task_sample[task]
            cell = attribution[f"{kind}|{type_of[sample]}"]
            cell["assigned"] += 1
            cell["second_call"] += sample in second
        result["injection_attribution_legacy_single_kind"] = {
            name: {
                **cell,
                "without_second_call": cell["assigned"] - cell["second_call"],
            }
            for name, cell in sorted(attribution.items())
        }
    except OSError:
        result["cost"] = _unavailable("ledger_missing")
    try:
        evidence = _evidence_text(sheet / "evidence.html")
    except OSError:
        evidence = {}
        result["fabricated"] = _unavailable("evidence_card_missing")
    if observations and evidence:
        result["fabricated"] = _fabricated_section(
            labels, citations, evidence, observations
        )
        by_pair = Counter()
        for pair in citations:
            by_pair[
                (
                    pair["supports"],
                    _id_type(pair["doc_id"]),
                    pair["doc_id"] in evidence.get(pair["sample_id"], ""),
                )
            ] += 1
        result["human"]["citation_labels_by_type_and_in_evidence"] = {
            f"{s}|{t}|in_evidence={e}": n for (s, t, e), n in sorted(by_pair.items())
        }
    result["detail"] = _detail_section(
        private / "calibration-detail.jsonl", version, labels, citations, task_sample
    )
    return result

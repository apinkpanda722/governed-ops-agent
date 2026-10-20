"""User-run M8 blind sheets, committed gates, bounded grading and count-only reports."""

import argparse
import asyncio
import csv
import hashlib
import json
import os
import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from statistics import median

from govops.costs import BudgetExceededError, CostLedger, PriceExpiredError
from govops.eval.grading import GradingError
from govops.eval.grading_cli import _specs
from govops.eval.observation import load_observations
from govops.grounding import (
    cards,
    db,
    diagnose,
    inject,
    measure_report,
    metrics,
    prompt,
    registration,
    sample,
    sheets,
)
from govops.grounding.deliverable import (
    extract_citations,
    fabricated_citations,
    select_deliverable,
)
from govops.grounding.evidence import EvidenceItem, load_evidence
from govops.grounding.grader import (
    POSITIVE_LABELS,
    GroundingGrader,
    RowAggregate,
    aggregate_repeats,
)
from govops.grounding.probe import RowLink, link_results
from govops.grounding.sentences import build_sentences, splitter_rules_sha256
from govops.grounding.spend import STAGE_CAPS_USD, M8Spend, SpendCapError

COMMANDS = (
    "sheet-calibration",
    "render-evidence",
    "recompute-measurement",
    "list-positives",
    "render-positives",
    "locate-positives",
    "register-labels",
    "cost-probe",
    "calibrate",
    "diagnose-calibration",
    "adopt",
    "draw-heldout",
    "sheet-heldout",
    "grade-heldout",
    "measure",
    "report",
)
N2_RUNS = (
    "09fbcb2be83f47669800da400a401e55",
    "b330337d70fa4cf9a6c25773fb9b1359",
    "bf38e57a74cc49eda2f2be522f14e063",
)
# Addendum 3 §2: the first 18 rows of N2-2 that the original measurement graded.
LOCATE_TASKS = (
    "T1-02",
    "T1-03",
    "T1-04",
    "T1-06",
    "T1-07",
    "T1-09",
    "T1-10",
    "T2-04",
    "T2-05",
    "T2-06",
    "T2-07",
    "T2-08",
    "T2-10",
    "T2-11",
    "T2-12",
    "T3-03",
    "T3-04",
    "T3-05",
)
N1A_RUN = "6b3ec0b1ec92402db7ebefb2d4cfb1b4"
PRIVATE_DIR = Path("data/interim/m8-grounding")
# Addendum 1 §B order: fact, then the two citation kinds.
INJECTION_ORDER = ("fact", "fabricated_id", "wrong_support")
LIMITED_NOTICE = (
    "제한적 검증(자연 양성 부족): 사람 양성 문장이 부족해 κ는 판정하지 못했고 "
    "합성 주입 탐지와 음성 문장 오탐으로 제한적으로 확인했다"
)
HUMAN_RATE_CAVEAT = (
    "보정 표본은 M6 층화 표본(T1 5·T2 6·T3 6·T4 4·T5 5·T6 4)이며 전체 평가셋의 "
    "환각률 추정이 아니다. 검수자 1인, 표본이 작아 CI가 넓다"
)
GATE_SCOPE = (
    "src/govops/grounding",
    "docs/results/m8-grader-registration.json",
    *registration.HYPOTHESES,
    "docs/HUMAN_GRADING_GUIDE_M8.md",
)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise registration.RegistrationError("invalid_arguments")


def parser():
    result = _Parser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    for name in COMMANDS:
        sub = commands.add_parser(name)
        sub.add_argument("--dbname")
        sub.add_argument("--url-env", default="GOVOPS_DATABASE_URL")
        sub.add_argument(
            "--test-results", type=Path, default=Path("data/results/test") / N2_RUNS[0]
        )
        sub.add_argument(
            "--dev-results", type=Path, default=Path("data/results/dev") / N1A_RUN
        )
        sub.add_argument("--results", type=Path, nargs=3)
        sub.add_argument(
            "--split", choices=("calibration", "heldout"), default="calibration"
        )
        sub.add_argument("--version", default="v3.0")
        sub.add_argument("--prompt-file", type=Path)
        sub.add_argument("--evidence-max-chars", type=int, default=80000)
    return result


def _hash(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _json(path, value, *, exclusive=False):
    sheets.private_write(
        Path(path),
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n",
        exclusive=exclusive,
    )


def _append(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        os.fchmod(target.fileno(), 0o600)
        target.write(
            json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
            + "\n"
        )
        target.flush()
        os.fsync(target.fileno())


def _log(path):
    if not path.exists():
        return []
    entries = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        entries[item["version"]] = item
    return sorted(entries.values(), key=lambda item: item["version"])


def check_revision(version, logs):
    if version not in ("v3.0", "v3.1", "v3.2", "v3.3"):
        raise ValueError("revision_limit")
    if any(row["version"] == version for row in logs):
        raise ValueError("version_already_calibrated")
    if version != f"v3.{len(logs)}":
        raise ValueError("revision_order")
    # A stage-cap stop is a finished record; only a run that never finished blocks.
    if any(
        row.get("status", "complete") not in {"complete", "stopped"} for row in logs
    ):
        raise ValueError("calibration_incomplete")
    if logs:
        last = logs[-1]
        if (
            last.get("kappa") is not None
            and last["kappa"] >= 0.6
            and last.get("detection_rate") is not None
            and last["detection_rate"] >= 0.9
            and last.get("false_positive_rate") is not None
            and last["false_positive_rate"] <= 0.1
        ):
            raise ValueError("calibration_stopped")
    if len(logs) >= 2:
        a, b = logs[-2:]
        improved_kappa = b.get("kappa") is not None and (
            a.get("kappa") is None or b["kappa"] > a["kappa"]
        )
        improved_detection = b.get("detection_rate") is not None and (
            a.get("detection_rate") is None or b["detection_rate"] > a["detection_rate"]
        )
        if not improved_kappa and not improved_detection:
            raise ValueError("calibration_stopped")


def choose_adopted(logs):
    candidates = [row for row in logs if row.get("status", "complete") == "complete"]
    if not candidates:
        raise ValueError("no_calibration")
    return max(
        candidates,
        key=lambda row: (
            row.get("kappa") if row.get("kappa") is not None else float("-inf"),
            row.get("detection_rate")
            if row.get("detection_rate") is not None
            else float("-inf"),
            -int(row["version"].split(".")[1]),
        ),
    )


@contextmanager
def _template(version, path):
    if version not in ("v3.0", "v3.1", "v3.2", "v3.3"):
        raise ValueError("revision_limit")
    original = dict(prompt.PROMPT_VERSIONS)
    try:
        if path is not None:
            if version == "v3.0":
                raise ValueError("initial_prompt_fixed")
            prompt.PROMPT_VERSIONS[version] = path
        # Versions 3.1–3.3 are explicit user-supplied rubric files, not automatic edits.
        prompt.prompt_sha256(version)
        yield
    finally:
        prompt.PROMPT_VERSIONS.clear()
        prompt.PROMPT_VERSIONS.update(original)


def build_grader(call=None, *, root=None, ledger_factory=None, spend_factory=None):
    """Construct only after guards. Tests inject calls and temporary cost ledgers."""
    root = Path.cwd() if root is None else Path(root)
    # No fallback or default key: the SDK is constructed lazily by the grader.
    if call is None and not os.environ["GEMINI_API_KEY"].strip():
        raise registration.RegistrationError("gemini_key_unset")
    ledger = ledger_factory() if ledger_factory else CostLedger(root / "data/manifest")
    spend = (
        spend_factory()
        if spend_factory
        else M8Spend(root / PRIVATE_DIR / "spend.jsonl")
    )
    return GroundingGrader(ledger=ledger, spend=spend, call=call)


def _path(root, path):
    return path if path.is_absolute() else root / path


def _fingerprint(row):
    return _hash(
        {
            "task": row.spec.task_id,
            "run": row.observation.run_index,
            "request": row.spec.request,
            "deliverable": select_deliverable(row.observation)[0],
            "observed": row.observation.observed_ids,
            "evidence": [(item.tool_name, item.content) for item in row.evidence.items],
        }
    )


async def _hydrate(rows, directories, args, *, saver_factory, evidence_loader):
    links = {}
    for directory in directories:
        for link in link_results(directory):
            key = (link.task_id, link.run_index)
            if key in links:
                raise ValueError("duplicate_observation_link")
            links[key] = link
    url = os.environ[args.url_env]
    if not url.strip():
        raise registration.RegistrationError("url_env_unset")
    if args.dbname is not None:
        url = db.url_with_dbname(url, args.dbname)
    hydrated = []
    async with saver_factory(url) as saver:
        for row in rows:
            obs = row.observation
            link = links.get(
                (obs.task_id, obs.run_index),
                RowLink("", obs.task_id, obs.run_index, obs.trace_id, ()),
            )
            evidence = await evidence_loader(
                saver, link, max_chars=args.evidence_max_chars
            )
            hydrated.append(replace(row, evidence=evidence))
    return sample.SampleRows(hydrated, excluded=getattr(rows, "excluded", {}))


async def _rows(args, root, specs, split, *, saver_factory, evidence_loader):
    test = _path(root, args.test_results)
    dev = _path(root, args.dev_results)
    test_obs = load_observations(test / "observations.jsonl")
    calibration = sample.calibration_rows(specs, test_obs)
    if split == "calibration":
        candidates = [sample.PoolRow(s, o) for s, o in calibration]
        directories = [test]
    else:
        candidates = sample.heldout_pool(
            specs,
            test_obs,
            load_observations(dev / "observations.jsonl"),
            {s.task_id for s, _ in calibration},
        )
        directories = [test, dev]
    hydrated = await _hydrate(
        candidates,
        directories,
        args,
        saver_factory=saver_factory,
        evidence_loader=evidence_loader,
    )
    return (
        sample.evidence_rows(hydrated)
        if split == "calibration"
        else sample.draw_heldout(hydrated)
    )


def _meta(rows):
    return {
        "task_ids": [r.spec.task_id for r in rows],
        "task_list_sha256": _hash([r.spec.task_id for r in rows]),
        "row_sha256": {r.spec.task_id: _fingerprint(r) for r in rows},
        "excluded": rows.excluded,
        "type_distribution": rows.type_distribution,
        "splitter_rules_version": "sent-v1",
        "splitter_rules_sha256": splitter_rules_sha256(),
    }


def _verify_meta(out, rows):
    previous = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    current = _meta(rows)
    if previous != current:
        raise registration.RegistrationError("sheet_inputs_changed")
    return current


def _human(out, rows, splitter):
    expected = sheets.expected_sheets(rows, registered_splitter=splitter)
    labels = sheets.load_labels(out / "labels.csv", expected)
    citations = sheets.load_labels(out / "citations.csv", expected)
    task_by_sample = {row["sample_id"]: row["task_id"] for row in labels}
    human = {row.spec.task_id: {} for row in rows}
    supports = {row.spec.task_id: {} for row in rows}
    for row in labels:
        human[row["task_id"]][row["sentence_id"]] = row["label"]
    for row in citations:
        supports[task_by_sample[row["sample_id"]]][
            (row["sentence_id"], row["doc_id"])
        ] = row["supports"] == "지지"
    return human, supports


async def _grade_row(grader, row, sentences, version, stage, repeats):
    results = []
    for repeat in range(repeats):
        results.append(
            await grader.grade(
                row_key=_hash(row.key),
                version=version,
                stage=stage,
                request=row.spec.request,
                sentences=sentences,
                evidence=row.evidence.items,
                repeat_index=repeat,
            )
        )
    if repeats == 1:
        values = results[0].sentences
        aggregate = RowAggregate(
            {s.sentence_id: s.label for s in values},
            {s.sentence_id: s.label in POSITIVE_LABELS for s in values},
            {
                (s.sentence_id, doc): vote
                for s in values
                for doc, vote in s.citation_supports
            },
            any(s.label in POSITIVE_LABELS for s in values),
        )
    else:
        aggregate = aggregate_repeats(results)
    return aggregate, results


def _human_rates(out):
    """Human-label hallucination rates (grader-independent), counts and CIs only."""
    sections, combined = {}, []
    for split in ("calibration", "heldout"):
        path = out / split / "labels.csv"
        if not path.exists():
            sections[split] = {"unavailable": "labels_missing"}
            continue
        with path.open(encoding="utf-8", newline="") as source:
            rows = list(csv.DictReader(source))
        if not rows or any(not row["label"].strip() for row in rows):
            sections[split] = {"unavailable": "labels_incomplete"}
            continue
        rows = [
            {"sample_id": f"{split}:{r['sample_id']}", "label": r["label"]}
            for r in rows
        ]
        sections[split] = metrics.human_hallucination(rows)
        combined.extend(rows)
    if combined:
        sections["combined"] = metrics.human_hallucination(combined)
    sections["caveat"] = HUMAN_RATE_CAVEAT
    return sections


def _ratio(a, b):
    return a / b if b else None


def _evidence_blob(row):
    """Raw and JSON-decoded evidence text, as a search haystack for IDs."""
    items = row.evidence.items if row.evidence is not None else ()
    parts = []
    for item in items:
        parts.append(item.content)
        try:
            parts.append(json.dumps(json.loads(item.content), ensure_ascii=False))
        except (ValueError, RecursionError):
            pass
    return "\n".join(parts)


def _id_in_text(doc_id, text):
    return (
        re.search(r"(?<![A-Za-z0-9])" + re.escape(doc_id) + r"(?![A-Za-z0-9])", text)
        is not None
    )


def _rates(rows, graded, repeats):
    """Median of repeat-level rates; keep all denominators, never identifiers."""
    claims = positives = support_n = supported = positive_rows = None
    rates = {
        "unsupported_sentence_rate": [],
        "unsupported_row_rate": [],
        "support_precision": [],
    }
    repeat_counts = []
    for repeat in range(repeats):
        claims = positives = support_n = supported = positive_rows = 0
        for row, results in graded:
            values = results[repeat].sentences
            claims += sum(s.label != "not_claim" for s in values)
            positives += sum(s.label in POSITIVE_LABELS for s in values)
            positive_rows += any(s.label in POSITIVE_LABELS for s in values)
            for s in values:
                for doc, vote in s.citation_supports:
                    if doc in row.observation.observed_ids:
                        support_n += 1
                        supported += vote
        for key, value in (
            ("unsupported_sentence_rate", _ratio(positives, claims)),
            ("unsupported_row_rate", _ratio(positive_rows, len(graded))),
            ("support_precision", _ratio(supported, support_n)),
        ):
            rates[key].append(value)
        repeat_counts.append(
            {
                "repeat_index": repeat,
                "n_claims": claims,
                "n_positive_sentences": positives,
                "n_positive_rows": positive_rows,
                "n_observed_citation_pairs": support_n,
                "n_supported_pairs": supported,
                "n_graded_rows": len(graded),
            }
        )
    n_citations = fabricated = no_citations = request_citations = 0
    # Non-observed IDs split three ways (addendum §F + measurement fix): absent from the
    # evidence text (strict), present in it, or the row's evidence was never loaded.
    strict = in_only = not_loaded = 0
    citation_types = Counter()
    for row in rows:
        text, _ = select_deliverable(row.observation)
        citations = extract_citations(text or "")
        n_citations += len(citations)
        no_citations += not citations
        missing = fabricated_citations(
            citations, row.observation.observed_ids, row.spec.request
        )
        fabricated += len(missing)
        if row.evidence is not None and row.evidence.status == "ok":
            blob = _evidence_blob(row)
            absent = sum(not _id_in_text(doc, blob) for doc in missing)
            strict += absent
            in_only += len(missing) - absent
        else:
            not_loaded += len(missing)
        request_citations += len(extract_citations(row.spec.request))
        for doc in citations:
            citation_types[
                "document"
                if doc.startswith("syn-")
                else "incident"
                if doc.startswith("INC-")
                else "ticket"
            ] += 1
    return {
        **{
            key: median(values) if all(v is not None for v in values) else None
            for key, values in rates.items()
        },
        "fabricated_id_rate": _ratio(fabricated, n_citations),
        "fabricated_strict_rate": _ratio(strict, n_citations),
        "in_evidence_only_rate": _ratio(in_only, n_citations),
        "evidence_not_loaded_rate": _ratio(not_loaded, n_citations),
        "n_citations": n_citations,
        "n_fabricated": fabricated,
        "n_fabricated_strict": strict,
        "n_in_evidence_only": in_only,
        "n_evidence_not_loaded": not_loaded,
        "n_no_citations": no_citations,
        "n_request_citations": request_citations,
        "citation_type_distribution": dict(citation_types),
        "n_claims": claims,
        "n_positive_sentences": positives,
        "n_observed_citation_pairs": support_n,
        "n_supported_pairs": supported,
        "n_graded_rows": len(graded),
        "repeat_counts": repeat_counts,
        "n_sentences": sum(len(results[0].sentences) for _, results in graded),
    }


async def _evaluate(
    rows,
    human,
    human_supports,
    *,
    grader,
    version,
    stage,
    repeats,
    detail_sink=None,
):
    """``detail_sink`` receives per-row diagnostics: IDs, labels, codes and costs only."""

    def note(**entry):
        if detail_sink is not None:
            detail_sink.append(entry)

    compared, injected, graded = [], [], []
    failures = Counter()
    stop = None
    citation_matches = citation_count = 0
    candidates = []  # graded human-negative rows that may receive injections
    before = grader.spend.total_usd(stage)
    for row in rows:
        sentences = build_sentences(select_deliverable(row.observation)[0])
        row_before = grader.spend.total_usd(stage)
        try:
            agg, results = await _grade_row(
                grader, row, sentences, version, stage, repeats
            )
            compared.append(
                metrics.RowLabels(
                    _hash(row.key), human[row.spec.task_id], agg.labels, agg.positive
                )
            )
            graded.append((row, results))
            note(
                kind="row",
                task_id=row.spec.task_id,
                type=row.spec.type,
                status="graded",
                cost_usd=grader.spend.total_usd(stage) - row_before,
                labels=dict(agg.labels),
                supports=[
                    [sid, doc, vote]
                    for (sid, doc), vote in sorted(agg.citation_supports.items())
                ],
            )
            for key, value in human_supports[row.spec.task_id].items():
                citation_matches += agg.citation_supports[key] == value
                citation_count += 1
            if not any(
                label in POSITIVE_LABELS for label in human[row.spec.task_id].values()
            ):
                candidates.append((row, sentences))
        except GradingError as exc:
            failures[
                "grading_response_failure"
                if str(exc) == "grading_response_failure"
                else "grading_call_failure"
            ] += 1
            note(
                kind="row",
                task_id=row.spec.task_id,
                type=row.spec.type,
                status="failed",
                code="grading_response_failure"
                if str(exc) == "grading_response_failure"
                else "grading_call_failure",
                cost_usd=grader.spend.total_usd(stage) - row_before,
            )
        except (SpendCapError, BudgetExceededError, PriceExpiredError) as exc:
            stop = (
                "m8_stage_cap_exceeded"
                if isinstance(exc, SpendCapError)
                else "budget_or_price_block"
            )
            note(
                kind="row",
                task_id=row.spec.task_id,
                type=row.spec.type,
                status="stopped",
                code=stop,
                cost_usd=grader.spend.total_usd(stage) - row_before,
            )
            break
    # Injections run after every row is graded (addendum §B): a stage cap can then only
    # shorten the injection pass. Kind by kind, so each kind reaches the rows in turn.
    for kind in INJECTION_ORDER:
        if stop is not None:
            break
        for row, sentences in candidates:
            task = row.spec.task_id
            allowed = None
            if kind != "fact":
                # Citation replacement truth requires a human-supported source pair.
                allowed = {
                    (s.sentence_id, doc)
                    for s in sentences
                    if human[task][s.sentence_id] == "supported"
                    for doc in s.citations
                    if human_supports[task].get((s.sentence_id, doc)) is True
                }
            copy = inject.inject(
                row.key,
                kind,
                sentences,
                row.evidence.items,
                row.observation.observed_ids,
                sample.HELDOUT_SEED,
                allowed,
            )
            if isinstance(copy, inject.InjectionSkip):
                injected.append(copy)
                note(
                    kind="injection",
                    task_id=task,
                    type=row.spec.type,
                    assigned=kind,
                    outcome="skipped",
                    code=copy.reason,
                    cause=copy.cause,
                )
                continue
            try:
                agg, _ = await _grade_row(
                    grader, row, copy.sentences, version, stage, repeats
                )
                injected.append((copy, agg))
                note(
                    kind="injection",
                    task_id=task,
                    type=row.spec.type,
                    assigned=kind,
                    outcome="graded",
                )
            except GradingError as exc:
                code = (
                    "grading_response_failure"
                    if str(exc) == "grading_response_failure"
                    else "grading_call_failure"
                )
                failures[code] += 1
                injected.append(inject.InjectionSkip(row.key, "grading_failure", code))
                note(
                    kind="injection",
                    task_id=task,
                    type=row.spec.type,
                    assigned=kind,
                    outcome="skipped",
                    code="grading_failure",
                    cause=code,
                )
            except (SpendCapError, BudgetExceededError, PriceExpiredError) as exc:
                stop = (
                    "m8_stage_cap_exceeded"
                    if isinstance(exc, SpendCapError)
                    else "budget_or_price_block"
                )
                note(
                    kind="injection",
                    task_id=task,
                    type=row.spec.type,
                    assigned=kind,
                    outcome="stopped",
                    code=stop,
                )
                break
    if detail_sink is not None:
        seen = {e["task_id"] for e in detail_sink if e["kind"] == "row"}
        for row in rows:
            if row.spec.task_id not in seen:
                note(
                    kind="row",
                    task_id=row.spec.task_id,
                    type=row.spec.type,
                    status="ungraded",
                    code=stop,
                )
    detection = metrics.detection_and_fpr(injected, compared)
    # Addendum §D: rows whose grading failed or never started, over all rows.
    failed_rows = len(rows) - len(graded)
    row_failure_ratio = failed_rows / len(rows) if len(rows) else None
    result = metrics.validation_verdict(
        rows=compared,
        detection_rate=detection["detection_rate"],
        false_positive_rate=detection["false_positive_rate"],
        detection=detection,
        row_failure_ratio=row_failure_ratio,
    )
    result.update(
        {
            "scope": "m8",
            "notice": "M8 추가 측정 — 공식 M6 수치 아님",
            "version": version,
            "prompt_sha256": prompt.prompt_sha256(version),
            "splitter_rules_sha256": splitter_rules_sha256(),
            "status": "stopped" if stop else "complete",
            "stop_code": stop,
            "complete": not stop and not failures,
            "failures": dict(failures),
            "n_grader_failures": sum(failures.values()),
            "excluded": dict(rows.excluded),
            "n_evidence_missing": rows.excluded.get("evidence_missing", 0),
            "n_oversize": rows.excluded.get("evidence_oversize", 0),
            "n_deliverable_empty": rows.excluded.get("deliverable_empty", 0),
            "n_input_rows": len(rows) + sum(rows.excluded.values()),
            "n_ungraded_rows": len(rows) - len(graded),
            "n_failed_rows": failed_rows,
            "row_failure_ratio": row_failure_ratio,
            "split_count_changed": detection["skip_reasons"].get(
                "split_count_changed", 0
            ),
            "sentence_distribution": dict(
                Counter(
                    str(len(build_sentences(select_deliverable(r.observation)[0])))
                    for r in rows
                )
            ),
            "type_distribution": rows.type_distribution,
            "citation_agreement": _ratio(citation_matches, citation_count),
            "n_human_citation_pairs": citation_count,
            "detection": detection,
            "cost_usd": grader.spend.total_usd(stage) - before,
        }
    )
    # Validation diagnostics are always reportable; representative rates require trust.
    result["citation_diagnostics"] = {
        "agreement": result["citation_agreement"],
        "n": citation_count,
    }
    if stage == "measurement":
        result["measurement"] = _rates(rows, graded, repeats)
    else:
        deterministic = _rates(rows, [], repeats)
        result["deterministic_citations"] = {
            key: value
            for key, value in deterministic.items()
            if key.startswith("n_")
            or key
            in {
                "fabricated_id_rate",
                "fabricated_strict_rate",
                "in_evidence_only_rate",
                "evidence_not_loaded_rate",
                "citation_type_distribution",
            }
        }
    return result


async def _cost_probe(grader):
    before = grader.spend.total_usd("calibration")
    for i in range(3):
        text = f"합성 별구름 서비스의 예시 {i}입니다."
        await grader.grade(
            row_key=f"cost-probe-{i}",
            version="v3.0",
            stage="calibration",
            request="합성 비용 점검",
            sentences=build_sentences(text),
            evidence=(EvidenceItem("lookup_id", text, len(text)),),
            repeat_index=0,
        )
    cost = grader.spend.total_usd("calibration") - before
    return {"scope": "m8", "calls": 3, "cost_usd": cost, "cost_per_call_usd": cost / 3}


N2_POSITIONS = ("n2_1", "n2_2", "n2_3")


def measurement_rows(directories, specs):
    """Rows per N2 directory, in the given (position) order.

    The official N2 directories are three separate runs: each holds the 40 test tasks with
    ``run_index`` 0. Order therefore comes from the directory position, never from
    ``run_index`` values. Any violated precondition is the fixed code
    ``measurement_order_mismatch`` (no paths or values are printed).
    """
    mismatch = registration.RegistrationError("measurement_order_mismatch")
    paths = [Path(d) for d in directories]
    try:
        resolved = {p.resolve() for p in paths}
    except OSError:
        raise mismatch from None
    if len(paths) != len(N2_POSITIONS) or len(resolved) != len(paths):
        raise mismatch
    index = {s.task_id: s for s in specs}
    expected = {s.task_id for s in specs if s.split == "test"}
    all_rows = []
    for directory in paths:
        try:
            observations = load_observations(directory / "observations.jsonl")
        except (OSError, ValueError):
            raise registration.RegistrationError(
                "measurement_input_unreadable"
            ) from None
        ids = [o.task_id for o in observations]
        if (
            not observations
            or len({o.run_index for o in observations}) != 1
            or len(set(ids)) != len(ids)
            or set(ids) != expected
            or any(index[i].split != "test" for i in ids)
        ):
            raise mismatch
        all_rows.append(
            [
                sample.PoolRow(index[o.task_id], o)
                for o in sorted(observations, key=lambda o: o.task_id)
            ]
        )
    return all_rows


def _measurement_directories(args, root):
    directories = args.results or [Path("data/results/test") / run for run in N2_RUNS]
    return [_path(root, path) for path in directories]


def _detail_path(out):
    """Per-row measurement records, or the addendum 3 locate record when there are none."""
    path = out / "measurement-detail.jsonl"
    return path if path.exists() else out / "locate-detail.jsonl"


def offline_measurement_report(args, root, specs):
    """``recompute-measurement`` / ``list-positives``: files only, no API, no DB."""
    out = root / PRIVATE_DIR
    path = out / "measurement-result.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    if args.command == "list-positives":
        return measure_report.positives(_detail_path(out), result)
    if result is None:
        raise registration.RegistrationError("measurement_result_missing")
    recomputed = _recomputed(args, root, specs, result)
    _json(out / "measurement-recomputed.json", recomputed)
    return recomputed


async def _report_recomputed(args, root, specs, out, saver_factory, evidence_loader):
    """Re-aggregation of the stored measurement (evidence read-only if ``--dbname``)."""
    path = out / "measurement-result.json"
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        specs = specs if specs is not None else _specs(root)
        if args.dbname is None:
            return _recomputed(args, root, specs, result)
        return await _recomputed_with_evidence(
            args, root, specs, result, saver_factory, evidence_loader
        )
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        registration.RegistrationError,
    ):
        return {"unavailable": "recompute_inputs_unavailable"}


def _recomputed(args, root, specs, result):
    rows = measurement_rows(_measurement_directories(args, root), specs)
    return measure_report.recompute(result, rows)


async def _recomputed_with_evidence(
    args, root, specs, result, saver_factory, evidence_loader
):
    """Hydrate only the runs that never started (read-only DB); no grader, no LLM."""
    directories = _measurement_directories(args, root)
    rows = measurement_rows(directories, specs)
    started = {
        run.get("position", N2_POSITIONS[i])
        for i, run in enumerate(result.get("runs", []))
    }
    args.evidence_max_chars = registration.read_registration(
        root / registration.REGISTRATION_PATH
    )["evidence_max_chars"]
    hydrated = {}
    for position, candidates, directory in zip(
        N2_POSITIONS, rows, directories, strict=True
    ):
        if position in started:
            continue
        loaded = await _hydrate(
            candidates,
            [directory],
            args,
            saver_factory=saver_factory,
            evidence_loader=evidence_loader,
        )
        counts = _rates(loaded, [], 1)
        hydrated[position] = {key: counts[key] for key in measure_report.COUNT_KEYS}
    return measure_report.recompute(result, rows, hydrated)


async def _recompute_to_file(args, root, specs, saver_factory, evidence_loader):
    out = root / PRIVATE_DIR
    path = out / "measurement-result.json"
    if not path.exists():
        raise registration.RegistrationError("measurement_result_missing")
    recomputed = await _recomputed_with_evidence(
        args,
        root,
        specs,
        json.loads(path.read_text(encoding="utf-8")),
        saver_factory,
        evidence_loader,
    )
    _json(out / "measurement-recomputed.json", recomputed)
    return recomputed


async def _render_positives(args, root, specs, out, saver_factory, evidence_loader):
    """Evidence cards for only the rows holding grader-positive sentences."""
    detail = measure_report.positives(_detail_path(out), None)
    if "unavailable" in detail:
        raise registration.RegistrationError("measurement_detail_not_recorded")
    index = {s.task_id: s for s in specs}
    directories = {d.name: d for d in _measurement_directories(args, root)}
    wanted = {}
    for item in detail["positives"]:
        wanted.setdefault((item["run_id"], item["task_id"]), set()).add(
            item["sentence_id"]
        )
    keys = sorted(wanted)
    rows = []
    for run_id in sorted({run for run, _ in keys}):
        directory = directories[run_id]
        by_task = {
            o.task_id: o for o in load_observations(directory / "observations.jsonl")
        }
        candidates = [
            sample.PoolRow(index[task], by_task[task])
            for run, task in keys
            if run == run_id
        ]
        hydrated = await _hydrate(
            candidates,
            [directory],
            args,
            saver_factory=saver_factory,
            evidence_loader=evidence_loader,
        )
        rows.extend((run_id, row) for row in hydrated)
    labels, citations, requests, evidence, marked, mapping = [], [], {}, {}, {}, []
    for number, (run_id, row) in enumerate(rows, 1):
        sample_id = f"P{number:02}"
        text = select_deliverable(row.observation)[0]
        for s in build_sentences(text):
            labels.append(
                {
                    "sample_id": sample_id,
                    "sentence_id": s.sentence_id,
                    "sentence": s.text,
                }
            )
            citations.extend(
                {"sample_id": sample_id, "sentence_id": s.sentence_id, "doc_id": doc}
                for doc in s.citations
            )
        requests[sample_id] = row.spec.request
        evidence[sample_id] = row.evidence.items if row.evidence else ()
        marked[sample_id] = wanted[(run_id, row.spec.task_id)]
        mapping.append(
            {
                "sample_id": sample_id,
                "run_id": run_id,
                "task_id": row.spec.task_id,
                "sentence_ids": sorted(marked[sample_id], key=lambda s: int(s[1:])),
            }
        )
    target = out / "measure-positives"
    sheets.private_write(
        target / "evidence.html",
        cards.render_cards(labels, citations, requests, evidence, marked),
    )
    _json(target / "positives.json", {"rows": mapping})
    return {"n_rows": len(rows), "n_positive_sentences": detail["n_positive_sentences"]}


def measurement_allowed(validation) -> bool:
    """Full validation, or addendum §D limited validation that was not cap-stopped."""
    full = validation.get("verdict") == "채점기 v3 검증 통과" and bool(
        validation.get("complete")
    )
    limited = (
        validation.get("verdict") == metrics.LIMITED_VERDICT
        and validation.get("original_verdict") == "판정 불가(양성 부족)"
        and validation.get("stop_code") is None
        and validation.get("status") == "complete"
    )
    return full or limited


def _assert_adopted(value, version):
    if (
        version != value["adopted_prompt_version"]
        or prompt.prompt_sha256(version) != value["adopted_prompt_sha256"]
    ):
        raise registration.RegistrationError("adopted_prompt_changed")


async def _run(args, *, root, specs, grader_factory, saver_factory, evidence_loader):
    out = root / PRIVATE_DIR
    regpath = root / registration.REGISTRATION_PATH
    command = args.command
    registration.require_hypotheses(root)
    registration.require_clean(
        root,
        GATE_SCOPE
        if command
        not in {
            "sheet-calibration",
            "register-labels",
            "adopt",
            "render-evidence",
            "render-positives",
        }
        else [p for p in GATE_SCOPE if p != str(registration.REGISTRATION_PATH)],
    )
    if args.dbname is not None:
        # Validate even for commands that will not open a saver.
        db.url_with_dbname("postgresql://localhost/govops", args.dbname)
    if command == "render-positives":
        value = registration.read_registration(regpath)
        args.evidence_max_chars = value["evidence_max_chars"]
        return await _render_positives(
            args, root, specs, out, saver_factory, evidence_loader
        )
    if command == "cost-probe":
        return await _cost_probe(grader_factory())
    if command == "report":
        heldout_path = out / "heldout-result.json"
        result = (
            json.loads(heldout_path.read_text(encoding="utf-8"))
            if heldout_path.exists()
            else None
        )
        # Reports are our own count-only artifacts; source sheets are never printed.
        return {
            "heldout": result,
            "human_hallucination": _human_rates(out),
            "calibration": _log(out / "calibration-log.jsonl"),
            "measurement": json.loads((out / "measurement-result.json").read_text())
            if (out / "measurement-result.json").exists()
            else None,
            "measurement_recomputed": await _report_recomputed(
                args, root, specs, out, saver_factory, evidence_loader
            ),
        }
    if command == "sheet-calibration":
        value = registration.read_registration(regpath) if regpath.exists() else None
        if value is None:
            value = registration.append_registration(
                regpath, {"evidence_max_chars": args.evidence_max_chars}, repo_root=root
            )
        if args.evidence_max_chars != value["evidence_max_chars"]:
            raise registration.RegistrationError("evidence_cap_changed")
    elif command == "calibrate":
        value = registration.require_calibration(
            regpath,
            out / "calibration/labels.csv",
            citations=out / "calibration/citations.csv",
            repo_root=root,
        )
        if "adopted_prompt_sha256" in value:
            raise registration.RegistrationError("prompt_already_adopted")
        check_revision(args.version, _log(out / "calibration-log.jsonl"))
    elif command in {"draw-heldout", "sheet-heldout"}:
        value = registration.require_adopted(regpath, repo_root=root)
        with _template(value["adopted_prompt_version"], args.prompt_file):
            _assert_adopted(value, value["adopted_prompt_version"])
    elif command == "grade-heldout":
        value = registration.require_heldout(
            regpath,
            out / "heldout/labels.csv",
            out / "heldout-result.json",
            citations=out / "heldout/citations.csv",
            repo_root=root,
        )
        with _template(value["adopted_prompt_version"], args.prompt_file):
            _assert_adopted(value, value["adopted_prompt_version"])
    elif command == "adopt":
        value = registration.require_calibration(
            regpath,
            out / "calibration/labels.csv",
            citations=out / "calibration/citations.csv",
            repo_root=root,
        )
        best = choose_adopted(_log(out / "calibration-log.jsonl"))
        with _template(best["version"], args.prompt_file):
            if prompt.prompt_sha256(best["version"]) != best["prompt_sha256"]:
                raise registration.RegistrationError("prompt_hash_mismatch")
        registration.append_registration(
            regpath,
            {
                "adopted_prompt_version": best["version"],
                "adopted_prompt_sha256": best["prompt_sha256"],
            },
            repo_root=root,
        )
        return {
            "adopted_prompt_version": best["version"],
            "adopted_prompt_sha256": best["prompt_sha256"],
        }
    elif command == "locate-positives":
        value = registration.require_adopted(regpath, repo_root=root)
        validation = json.loads((out / "heldout-result.json").read_text())
        if not measurement_allowed(validation):
            raise registration.RegistrationError("validation_not_passed")
        if validation["prompt_sha256"] != value["adopted_prompt_sha256"]:
            raise registration.RegistrationError("adopted_prompt_changed")
        return await _locate(
            args,
            root,
            specs,
            value,
            out,
            grader_factory,
            saver_factory,
            evidence_loader,
        )
    elif command == "measure":
        value = registration.require_adopted(regpath, repo_root=root)
        validation = json.loads((out / "heldout-result.json").read_text())
        if not measurement_allowed(validation):
            raise registration.RegistrationError("validation_not_passed")
        if validation["prompt_sha256"] != value["adopted_prompt_sha256"]:
            raise registration.RegistrationError("adopted_prompt_changed")
        return await _measure(
            args,
            root,
            specs,
            value,
            out,
            grader_factory,
            saver_factory,
            evidence_loader,
            validation=validation,
        )
    else:  # register-labels/render-evidence may use a not-yet-committed registration.
        value = registration.read_registration(regpath)
        if args.split == "heldout":
            registration.require_adopted(regpath, repo_root=root)
    args.evidence_max_chars = value["evidence_max_chars"]
    if command in {"register-labels", "render-evidence"}:
        split = args.split
    else:
        split = (
            "heldout"
            if command in {"draw-heldout", "sheet-heldout", "grade-heldout"}
            else "calibration"
        )
    rows = await _rows(
        args,
        root,
        specs,
        split,
        saver_factory=saver_factory,
        evidence_loader=evidence_loader,
    )
    directory = out / split
    if command == "sheet-calibration":
        sheets.write_label_sheets(
            rows, directory, registered_splitter=value["splitter_rules_sha256"]
        )
        _json(directory / "meta.json", _meta(rows))
        return {"n_rows": len(rows), "excluded": rows.excluded}
    if command == "draw-heldout":
        with _template(value["adopted_prompt_version"], args.prompt_file):
            _assert_adopted(value, value["adopted_prompt_version"])
        _json(directory / "meta.json", _meta(rows), exclusive=True)
        return {
            "n_rows": len(rows),
            "task_list_sha256": _meta(rows)["task_list_sha256"],
            "type_distribution": rows.type_distribution,
            "excluded": rows.excluded,
        }
    if command == "render-evidence":
        # Same sample and evidence as the sheets; only evidence.html is rewritten.
        _verify_meta(directory, rows)
        return sheets.rewrite_evidence_card(
            rows, directory, registered_splitter=value["splitter_rules_sha256"]
        )
    meta = _verify_meta(directory, rows)
    if command == "sheet-heldout":
        with _template(value["adopted_prompt_version"], args.prompt_file):
            _assert_adopted(value, value["adopted_prompt_version"])
        sheets.write_label_sheets(
            rows, directory, registered_splitter=value["splitter_rules_sha256"]
        )
        return {"n_rows": len(rows), "excluded": rows.excluded}
    human, supports = _human(directory, rows, value["splitter_rules_sha256"])
    if command == "register-labels":
        addition = {
            split + "_labels_sha256": registration.sha256(directory / "labels.csv"),
            split + "_citations_sha256": registration.sha256(
                directory / "citations.csv"
            ),
        }
        if split == "heldout":
            addition["heldout_task_list_sha256"] = meta["task_list_sha256"]
        registration.append_registration(regpath, addition, repo_root=root)
        return {"n_rows": len(rows), **addition}
    if (
        command == "grade-heldout"
        and value.get("heldout_task_list_sha256") != meta["task_list_sha256"]
    ):
        raise registration.RegistrationError("heldout_sample_changed")
    version = (
        args.version if command == "calibrate" else value["adopted_prompt_version"]
    )
    with _template(version, args.prompt_file):
        if command == "grade-heldout":
            _assert_adopted(value, version)
        grader = grader_factory()
        if command == "calibrate":
            _append(
                out / "calibration-log.jsonl",
                {
                    "version": version,
                    "prompt_sha256": prompt.prompt_sha256(version),
                    "status": "started",
                },
            )
        else:
            _json(
                out / "heldout-result.json",
                {"scope": "m8", "status": "started", "complete": False},
                exclusive=True,
            )
        detail = []
        result = await _evaluate(
            rows,
            human,
            supports,
            grader=grader,
            version=version,
            stage="calibration" if command == "calibrate" else "validation",
            repeats=1 if command == "calibrate" else 3,
            detail_sink=detail,
        )
        # Per-row diagnostics (IDs, labels, codes, costs) for diagnose-calibration.
        _append(
            out
            / (
                "calibration-detail.jsonl"
                if command == "calibrate"
                else "heldout-detail.jsonl"
            ),
            {"version": version, "rows": detail},
        )
        if command == "calibrate":
            _append(out / "calibration-log.jsonl", result)
        else:
            _json(out / "heldout-result.json", result)
    return result


async def _locate(
    args, root, specs, value, out, grader_factory, saver_factory, evidence_loader
):
    """Addendum 3: grade the 18 N2-2 rows once to locate the original positive sentence."""
    target = out / "locate-detail.jsonl"
    if target.exists():
        raise registration.RegistrationError("already_located")
    record = out / "measurement-result.json"
    if not record.exists():
        raise registration.RegistrationError("measurement_result_missing")
    runs = json.loads(record.read_text(encoding="utf-8")).get("runs", [])
    original = next((r for r in runs if r.get("position") == N2_POSITIONS[1]), None)
    if original is None:
        raise registration.RegistrationError("measurement_result_missing")
    directories = _measurement_directories(args, root)
    version = value["adopted_prompt_version"]
    with _template(version, args.prompt_file):
        _assert_adopted(value, version)
    candidates = measurement_rows(directories, specs)[1][: len(LOCATE_TASKS)]
    if [row.spec.task_id for row in candidates] != list(LOCATE_TASKS):
        raise registration.RegistrationError("measurement_order_mismatch")
    args.evidence_max_chars = value["evidence_max_chars"]
    hydrated = await _hydrate(
        candidates,
        [directories[1]],
        args,
        saver_factory=saver_factory,
        evidence_loader=evidence_loader,
    )
    eligible, skipped = [], []
    for row in hydrated:
        if select_deliverable(row.observation)[0] is None:
            skipped.append({"task_id": row.spec.task_id, "code": "deliverable_empty"})
        elif row.evidence.status != "ok":
            skipped.append({"task_id": row.spec.task_id, "code": row.evidence.status})
        else:
            eligible.append(row)
    cap = STAGE_CAPS_USD["locate"]
    with _template(version, args.prompt_file):
        _assert_adopted(value, version)
        grader = grader_factory()
        # §5: refuse before any call unless the reservations leave room under the cap.
        reservations = [
            grader.reservation_usd(
                version,
                row.spec.request,
                build_sentences(select_deliverable(row.observation)[0]),
                row.evidence.items,
            )
            for row in eligible
        ]
        expected = float(original.get("cost_usd") or 0) / 3
        largest = max(reservations, default=0)
        if expected + largest > cap:
            # Numbers only (no row content): lets the user see the computed margin.
            raise registration.RegistrationError(
                f"locate_reservation_margin expected_usd={expected:.4f} "
                f"max_reservation_usd={largest:.4f} cap_usd={cap}"
            )
        rows, failed, stop = [], list(skipped), None
        for row in eligible:
            try:
                agg, _ = await _grade_row(
                    grader,
                    row,
                    build_sentences(select_deliverable(row.observation)[0]),
                    version,
                    "locate",
                    1,
                )
                rows.append(
                    {
                        "task_id": row.spec.task_id,
                        "repeat_labels": [dict(agg.labels)],
                        "positive_majority": sorted(
                            sid for sid, flag in agg.positive.items() if flag
                        ),
                    }
                )
            except GradingError as exc:
                failed.append(
                    {
                        "task_id": row.spec.task_id,
                        "code": "grading_response_failure"
                        if str(exc) == "grading_response_failure"
                        else "grading_call_failure",
                    }
                )
            except SpendCapError:
                stop = "m8_stage_cap_exceeded"
                break
            except (BudgetExceededError, PriceExpiredError):
                stop = "budget_or_price_block"
                break
    entry = {
        "run_id": directories[1].name,
        "position": N2_POSITIONS[1],
        "repeats": 1,
        "prompt_sha256": value["adopted_prompt_sha256"],
        "rows": rows,
        "failed": failed,
        "stop_code": stop,
    }
    _append(target, entry)
    return {
        "scope": "m8",
        "notice": "M8 추가 측정 — 공식 M6 수치 아님",
        "stage": "locate",
        **measure_report.locate_summary(entry),
        "n_input_rows": len(candidates),
        "n_graded_rows": len(rows),
        "complete": not failed and stop is None and len(rows) == len(candidates),
        "failures": dict(Counter(item["code"] for item in failed)),
        "stop_code": stop,
        "cost_usd": grader.spend.total_usd("locate"),
        "max_reservation_usd": max(reservations, default=0),
        "expected_cost_usd": expected,
    }


async def _measure(
    args,
    root,
    specs,
    value,
    out,
    grader_factory,
    saver_factory,
    evidence_loader,
    validation=None,
):
    """Traverse N2-1, N2-2, N2-3 in order; stop at the fixed measurement budget."""
    target = out / "measurement-result.json"
    if target.exists():
        raise registration.RegistrationError("already_measured")
    directories = args.results or [Path("data/results/test") / run for run in N2_RUNS]
    directories = [_path(root, path) for path in directories]
    version = value["adopted_prompt_version"]
    with _template(version, args.prompt_file):
        _assert_adopted(value, version)
    all_rows = measurement_rows(directories, specs)
    args.evidence_max_chars = value["evidence_max_chars"]
    result = {
        "scope": "m8",
        "notice": "M8 추가 측정 — 공식 M6 수치 아님",
        "runs": [],
        "n_input_rows": sum(map(len, all_rows)),
        "n_graded_rows": 0,
    }
    if validation is not None:
        verdict = validation.get("verdict")
        result["validation"] = {
            "verdict": verdict,
            "original_verdict": validation.get("original_verdict", verdict),
            "limited": verdict == metrics.LIMITED_VERDICT,
        }
        if verdict == metrics.LIMITED_VERDICT:
            result["validation"]["notice"] = LIMITED_NOTICE
    _json(target, result | {"status": "started"}, exclusive=True)
    all_graded = []
    with _template(version, args.prompt_file):
        _assert_adopted(value, version)
        grader = grader_factory()
        for position, (candidates, directory) in enumerate(
            zip(all_rows, directories, strict=True)
        ):
            hydrated = await _hydrate(
                candidates,
                [directory],
                args,
                saver_factory=saver_factory,
                evidence_loader=evidence_loader,
            )
            # Later aggregates must see the evidence that was actually loaded.
            all_rows[position] = list(hydrated)
            excluded = Counter()
            eligible = []
            for row in hydrated:
                if select_deliverable(row.observation)[0] is None:
                    excluded["deliverable_empty"] += 1
                elif row.evidence.status != "ok":
                    excluded[row.evidence.status] += 1
                else:
                    eligible.append(row)
            graded, failures, stop = [], Counter(), None
            detail_rows = []
            before = grader.spend.total_usd("measurement")
            for row in eligible:
                try:
                    agg, repeats = await _grade_row(
                        grader,
                        row,
                        build_sentences(select_deliverable(row.observation)[0]),
                        version,
                        "measurement",
                        3,
                    )
                    graded.append((row, repeats))
                    detail_rows.append(
                        {
                            "task_id": row.spec.task_id,
                            "repeat_labels": [
                                {s.sentence_id: s.label for s in r.sentences}
                                for r in repeats
                            ],
                            "positive_majority": sorted(
                                sid for sid, flag in agg.positive.items() if flag
                            ),
                        }
                    )
                except GradingError as exc:
                    failures[
                        "grading_response_failure"
                        if str(exc) == "grading_response_failure"
                        else "grading_call_failure"
                    ] += 1
                except SpendCapError:
                    stop = "m8_stage_cap_exceeded"
                    break
                except (BudgetExceededError, PriceExpiredError):
                    stop = "budget_or_price_block"
                    break
            # Per-row labels (IDs and labels only) for the positive-sentence tools.
            _append(
                out / "measurement-detail.jsonl",
                {
                    "run_id": directory.name,
                    "position": N2_POSITIONS[position],
                    "rows": detail_rows,
                },
            )
            run = {
                "run_index": position,
                "position": N2_POSITIONS[position],
                "run_id": directory.name,
                "observed_run_index": hydrated[0].observation.run_index
                if hydrated
                else None,
                "observations_sha256": registration.sha256(
                    directory / "observations.jsonl"
                ),
                "excluded": dict(excluded),
                "failures": dict(failures),
                "type_distribution": dict(Counter(r.spec.type for r, _ in graded)),
                "sentence_distribution": dict(
                    Counter(str(len(v[0].sentences)) for _, v in graded)
                ),
                "cost_usd": grader.spend.total_usd("measurement") - before,
                "stop_code": stop,
                **_rates(hydrated, graded, 3),
            }
            result["runs"].append(run)
            result["n_graded_rows"] += len(graded)
            all_graded.extend(graded)
            if stop:
                break
    result.update(
        {
            **_rates([row for rows in all_rows for row in rows], all_graded, 3),
            "n_ungraded_rows": result["n_input_rows"] - result["n_graded_rows"],
            "prompt_sha256": value["adopted_prompt_sha256"],
            "splitter_rules_sha256": splitter_rules_sha256(),
            "type_distribution": dict(Counter(row.spec.type for row, _ in all_graded)),
            "sentence_distribution": dict(
                Counter(str(len(repeats[0].sentences)) for _, repeats in all_graded)
            ),
            "excluded": dict(
                sum((Counter(run["excluded"]) for run in result["runs"]), Counter())
            ),
            "failures": dict(
                sum((Counter(run["failures"]) for run in result["runs"]), Counter())
            ),
            "stop_code": result["runs"][-1]["stop_code"]
            if result["runs"]
            else "no_rows",
            "status": "complete"
            if len(result["runs"]) == 3 and not result["runs"][-1]["stop_code"]
            else "stopped",
        }
    )
    result.update(
        {
            "n_evidence_missing": result["excluded"].get("evidence_missing", 0),
            "n_oversize": result["excluded"].get("evidence_oversize", 0),
            "n_deliverable_empty": result["excluded"].get("deliverable_empty", 0),
            "n_grader_failures": sum(result["failures"].values()),
            "cost_usd": sum(run["cost_usd"] for run in result["runs"]),
        }
    )
    _json(target, result)
    return result


def main(
    argv=None,
    *,
    repo_root=None,
    specs=None,
    call=None,
    ledger_factory=None,
    saver_factory=None,
    evidence_loader=load_evidence,
):
    """The sole output boundary prints only our aggregates or fixed error codes."""
    try:
        args = parser().parse_args(argv)
        root = Path.cwd() if repo_root is None else Path(repo_root)
        # No spec/evidence/ledger/SDK access before preregistration checks.
        registration.require_hypotheses(root)
        if args.command in {"recompute-measurement", "list-positives"}:
            offline_specs = specs
            if offline_specs is None and args.command == "recompute-measurement":
                offline_specs = _specs(root)
            if args.command == "recompute-measurement" and args.dbname is not None:
                report = asyncio.run(
                    _recompute_to_file(
                        args,
                        root,
                        offline_specs,
                        saver_factory or db.open_readonly_saver,
                        evidence_loader,
                    )
                )
            else:
                report = offline_measurement_report(args, root, offline_specs)
            print(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
            )
            return 0
        if args.command == "diagnose-calibration":
            # Offline: no specs, gates, grader, database or API; counts/IDs/codes only.
            print(
                json.dumps(
                    diagnose.diagnose(
                        root,
                        version=args.version,
                        test_results=_path(root, args.test_results),
                    ),
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                    allow_nan=False,
                )
            )
            return 0
        task_specs = specs
        if task_specs is None and args.command not in {
            "cost-probe",
            "adopt",
            "report",
            "measure",
        }:
            task_specs = _specs(root)
        if task_specs is None and args.command == "measure":
            task_specs = _specs(root)
        result = asyncio.run(
            _run(
                args,
                root=root,
                specs=task_specs,
                grader_factory=lambda: build_grader(
                    call, root=root, ledger_factory=ledger_factory
                ),
                saver_factory=saver_factory or db.open_readonly_saver,
                evidence_loader=evidence_loader,
            )
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
        if args.command == "report":
            heldout = result["heldout"] or {}
            print("metric\tvalue")
            for key in (
                "kappa",
                "kappa_ci",
                "detection_rate",
                "false_positive_rate",
                "verdict",
                "original_verdict",
            ):
                print(f"{key}\t{heldout.get(key)}")
            if heldout.get("verdict") == metrics.LIMITED_VERDICT:
                print(f"notice\t{LIMITED_NOTICE}")
        return int(bool(result.get("stop_code")))
    except registration.RegistrationError as exc:
        print("FAIL " + str(exc))
        return 2
    except sample.PoolInsufficient as exc:
        print(
            json.dumps(
                {
                    "code": "pool_insufficient",
                    "n_rows": exc.n,
                    "excluded": exc.excluded,
                },
                sort_keys=True,
            )
        )
        return 2
    except SpendCapError:
        print("FAIL m8_stage_cap_exceeded")
        return 1
    except (BudgetExceededError, PriceExpiredError):
        print("FAIL budget_or_price_block")
        return 1
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        # Backend exceptions, CSV rows, filenames and input values stay private.
        print("FAIL input_or_execution_failure")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

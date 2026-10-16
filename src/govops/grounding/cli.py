"""User-run M8 blind sheets, committed gates, bounded grading and count-only reports."""

import argparse
import asyncio
import hashlib
import json
import os
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from statistics import median

from govops.costs import BudgetExceededError, CostLedger, PriceExpiredError
from govops.eval.grading import GradingError
from govops.eval.grading_cli import _specs
from govops.eval.observation import load_observations
from govops.grounding import db, inject, metrics, prompt, registration, sample, sheets
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
from govops.grounding.spend import M8Spend, SpendCapError

COMMANDS = (
    "sheet-calibration",
    "render-evidence",
    "register-labels",
    "cost-probe",
    "calibrate",
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
N1A_RUN = "6b3ec0b1ec92402db7ebefb2d4cfb1b4"
PRIVATE_DIR = Path("data/interim/m8-grounding")
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
    if any(row.get("status", "complete") != "complete" for row in logs):
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


def _ratio(a, b):
    return a / b if b else None


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
    citation_types = Counter()
    for row in rows:
        text, _ = select_deliverable(row.observation)
        citations = extract_citations(text or "")
        n_citations += len(citations)
        no_citations += not citations
        fabricated += len(
            fabricated_citations(
                citations, row.observation.observed_ids, row.spec.request
            )
        )
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
        "n_citations": n_citations,
        "n_fabricated": fabricated,
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


async def _evaluate(rows, human, human_supports, *, grader, version, stage, repeats):
    compared, injected, graded = [], [], []
    failures = Counter()
    stop = None
    citation_matches = citation_count = 0
    normal = [
        r.key
        for r in rows
        if not any(label in POSITIVE_LABELS for label in human[r.spec.task_id].values())
    ]
    kinds = inject.assign_kinds(normal, sample.HELDOUT_SEED)
    before = grader.spend.total_usd(stage)
    for row in rows:
        sentences = build_sentences(select_deliverable(row.observation)[0])
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
            for key, value in human_supports[row.spec.task_id].items():
                citation_matches += agg.citation_supports[key] == value
                citation_count += 1
        except GradingError as exc:
            failures[
                "grading_response_failure"
                if str(exc) == "grading_response_failure"
                else "grading_call_failure"
            ] += 1
            continue
        except (SpendCapError, BudgetExceededError, PriceExpiredError) as exc:
            stop = (
                "m8_stage_cap_exceeded"
                if isinstance(exc, SpendCapError)
                else "budget_or_price_block"
            )
            break
        if row.key not in kinds:
            continue
        copy = inject.inject(
            row.key,
            kinds[row.key],
            sentences,
            row.evidence.items,
            row.observation.observed_ids,
            sample.HELDOUT_SEED,
        )
        if isinstance(copy, inject.InjectionSkip):
            injected.append(copy)
            continue
        if copy.kind != "fact":
            sid = copy.target_sentence_id
            # Citation replacement truth requires a human-supported source pair.
            old = set(
                next(s.citations for s in sentences if s.sentence_id == sid)
            ) - set(next(s.citations for s in copy.sentences if s.sentence_id == sid))
            if (
                human[row.spec.task_id][sid] != "supported"
                or not old
                or not all(
                    human_supports[row.spec.task_id].get((sid, doc)) is True
                    for doc in old
                )
            ):
                injected.append(inject.InjectionSkip(row.key, "no_candidate"))
                continue
        try:
            agg, _ = await _grade_row(
                grader, row, copy.sentences, version, stage, repeats
            )
            injected.append((copy, agg))
        except GradingError as exc:
            failures[
                "grading_response_failure"
                if str(exc) == "grading_response_failure"
                else "grading_call_failure"
            ] += 1
            injected.append(inject.InjectionSkip(row.key, "grading_failure"))
        except (SpendCapError, BudgetExceededError, PriceExpiredError) as exc:
            stop = (
                "m8_stage_cap_exceeded"
                if isinstance(exc, SpendCapError)
                else "budget_or_price_block"
            )
            break
    detection = metrics.detection_and_fpr(injected, compared)
    result = metrics.validation_verdict(
        rows=compared,
        detection_rate=detection["detection_rate"],
        false_positive_rate=detection["false_positive_rate"],
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
            or key in {"fabricated_id_rate", "citation_type_distribution"}
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
        not in {"sheet-calibration", "register-labels", "adopt", "render-evidence"}
        else [p for p in GATE_SCOPE if p != str(registration.REGISTRATION_PATH)],
    )
    if args.dbname is not None:
        # Validate even for commands that will not open a saver.
        db.url_with_dbname("postgresql://localhost/govops", args.dbname)
    if command == "cost-probe":
        return await _cost_probe(grader_factory())
    if command == "report":
        result = json.loads((out / "heldout-result.json").read_text(encoding="utf-8"))
        # Reports are our own count-only artifacts; source sheets are never printed.
        return {
            "heldout": result,
            "calibration": _log(out / "calibration-log.jsonl"),
            "measurement": json.loads((out / "measurement-result.json").read_text())
            if (out / "measurement-result.json").exists()
            else None,
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
    elif command == "measure":
        value = registration.require_adopted(regpath, repo_root=root)
        validation = json.loads((out / "heldout-result.json").read_text())
        if validation.get("verdict") != "채점기 v3 검증 통과" or not validation.get(
            "complete"
        ):
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
        result = await _evaluate(
            rows,
            human,
            supports,
            grader=grader,
            version=version,
            stage="calibration" if command == "calibrate" else "validation",
            repeats=1 if command == "calibrate" else 3,
        )
        if command == "calibrate":
            _append(out / "calibration-log.jsonl", result)
        else:
            _json(out / "heldout-result.json", result)
    return result


async def _measure(
    args, root, specs, value, out, grader_factory, saver_factory, evidence_loader
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
    index = {s.task_id: s for s in specs}
    all_rows = []
    for n, directory in enumerate(directories):
        observations = load_observations(directory / "observations.jsonl")
        if (
            any(
                o.run_index != n
                or o.task_id not in index
                or index[o.task_id].split != "test"
                for o in observations
            )
            or len({o.task_id for o in observations}) != len(observations)
            or not observations
            or {o.task_id for o in observations}
            != {s.task_id for s in specs if s.split == "test"}
        ):
            raise ValueError("measurement_order_mismatch")
        all_rows.append(
            [
                sample.PoolRow(index[o.task_id], o)
                for o in sorted(observations, key=lambda o: o.task_id)
            ]
        )
    args.evidence_max_chars = value["evidence_max_chars"]
    result = {
        "scope": "m8",
        "notice": "M8 추가 측정 — 공식 M6 수치 아님",
        "runs": [],
        "n_input_rows": sum(map(len, all_rows)),
        "n_graded_rows": 0,
    }
    _json(target, result | {"status": "started"}, exclusive=True)
    all_graded = []
    with _template(version, args.prompt_file):
        _assert_adopted(value, version)
        grader = grader_factory()
        for candidates, directory in zip(all_rows, directories, strict=True):
            hydrated = await _hydrate(
                candidates,
                [directory],
                args,
                saver_factory=saver_factory,
                evidence_loader=evidence_loader,
            )
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
            before = grader.spend.total_usd("measurement")
            for row in eligible:
                try:
                    _, repeats = await _grade_row(
                        grader,
                        row,
                        build_sentences(select_deliverable(row.observation)[0]),
                        version,
                        "measurement",
                        3,
                    )
                    graded.append((row, repeats))
                except GradingError as exc:
                    failures[
                        "grading_response_failure"
                        if str(exc) == "grading_response_failure"
                        else "grading_call_failure"
                    ] += 1
                except (SpendCapError, BudgetExceededError, PriceExpiredError):
                    stop = "budget_or_price_block"
                    break
            run = {
                "run_index": len(result["runs"]),
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
            heldout = result["heldout"]
            print("metric\tvalue")
            for key in (
                "kappa",
                "kappa_ci",
                "detection_rate",
                "false_positive_rate",
                "verdict",
            ):
                print(f"{key}\t{heldout.get(key)}")
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

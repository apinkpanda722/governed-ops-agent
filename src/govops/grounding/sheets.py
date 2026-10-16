"""Blind sentence/citation sheets; these private files alone may contain bodies."""

import csv
import io
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path

from govops.grounding.cards import render_cards
from govops.grounding.deliverable import select_deliverable
from govops.grounding.grader import LABELS
from govops.grounding.sentences import (
    SPLITTER_RULES_VERSION,
    build_sentences,
    splitter_rules_sha256,
)

LABEL_COLUMNS = (
    "sample_id",
    "sentence_id",
    "task_id",
    "type",
    "request",
    "sentence",
    "label",
    "note",
)
CITATION_COLUMNS = ("sample_id", "sentence_id", "doc_id", "supports")
SHEET_SEED = 20261016


@dataclass(frozen=True)
class SheetSet:
    labels: tuple[dict, ...]
    citations: tuple[dict, ...]
    splitter_rules_sha256: str


def private_write(path: Path, text: str, *, exclusive=False) -> None:
    """Create with restrictive permissions before writing any private bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_EXCL if exclusive else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as target:
        os.fchmod(target.fileno(), 0o600)
        target.write(text)
        target.flush()
        os.fsync(target.fileno())


def expected_sheets(rows, *, registered_splitter=None) -> SheetSet:
    digest = splitter_rules_sha256()
    if registered_splitter is not None and digest != registered_splitter:
        raise ValueError("splitter_rules_changed")
    ordered = sorted(rows, key=lambda row: row.key)
    if len({r.spec.task_id for r in ordered}) != len(ordered):
        raise ValueError("duplicate_task")
    random.Random(SHEET_SEED).shuffle(ordered)
    labels, citations = [], []
    for i, row in enumerate(ordered, 1):
        text, _ = select_deliverable(row.observation)
        if text is None:
            raise ValueError("deliverable_empty")
        sentences = build_sentences(text)
        if not sentences:
            raise ValueError("deliverable_empty")
        for n, sentence in enumerate(sentences):
            common = {"sample_id": f"S{i:02}", "sentence_id": sentence.sentence_id}
            labels.append(
                common
                | {
                    "task_id": row.spec.task_id,
                    "type": row.spec.type,
                    "request": row.spec.request if n == 0 else "",
                    "sentence": sentence.text,
                    "label": "",
                    "note": "",
                }
            )
            citations.extend(
                common | {"doc_id": doc, "supports": ""} for doc in sentence.citations
            )
    return SheetSet(tuple(labels), tuple(citations), digest)


def _csv(columns, rows):
    target = io.StringIO(newline="")
    writer = csv.DictWriter(target, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return target.getvalue()


def _card_html(expected: SheetSet, rows) -> str:
    by_task = {row.spec.task_id: row for row in rows}
    task_of = {r["sample_id"]: r["task_id"] for r in expected.labels}
    return render_cards(
        expected.labels,
        expected.citations,
        {s: by_task[t].spec.request for s, t in task_of.items()},
        {s: by_task[t].evidence.items for s, t in task_of.items()},
    )


def rewrite_evidence_card(rows, out_dir, *, registered_splitter=None) -> dict:
    """Overwrite only evidence.html for the same sample and evidence as the sheets."""
    out = Path(out_dir)
    if out.name not in {"calibration", "heldout"} or out.parent.parts[-3:] != (
        "data",
        "interim",
        "m8-grounding",
    ):
        raise ValueError("private_sheet_path_required")
    expected = expected_sheets(rows, registered_splitter=registered_splitter)

    def keys(path, columns):
        with Path(path).open(encoding="utf-8", newline="") as source:
            reader = csv.DictReader(source)
            if tuple(reader.fieldnames or ()) != tuple(columns):
                raise ValueError("sheet_columns_changed")
            return {tuple(row[c] for c in columns) for row in reader}

    existing_labels = keys(out / "labels.csv", LABEL_COLUMNS)
    existing_citations = keys(out / "citations.csv", CITATION_COLUMNS)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    wanted_labels = {tuple(r[c] for c in LABEL_COLUMNS) for r in expected.labels}
    wanted_cites = {tuple(r[c] for c in CITATION_COLUMNS) for r in expected.citations}

    def ident(items):
        return {(x[0], x[1]) for x in items}  # (sample_id, sentence_id)

    if (
        ident(existing_labels) != ident(wanted_labels)
        or {x[:3] for x in existing_citations} != {x[:3] for x in wanted_cites}
        or meta.get("splitter_rules_sha256") != expected.splitter_rules_sha256
    ):
        raise ValueError("sheet_set_mismatch")
    private_write(out / "evidence.html", _card_html(expected, rows))
    return {"n_rows": len({r["sample_id"] for r in expected.labels})}


def write_label_sheets(rows, out_dir, *, registered_splitter=None) -> SheetSet:
    out = Path(out_dir)
    if out.name not in {"calibration", "heldout"} or out.parent.parts[-3:] != (
        "data",
        "interim",
        "m8-grounding",
    ):
        raise ValueError("private_sheet_path_required")
    expected = expected_sheets(rows, registered_splitter=registered_splitter)
    # Never overwrite a person's labels, including a partially completed sheet.
    if any(
        (out / name).exists()
        for name in ("labels.csv", "citations.csv", "evidence.html")
    ):
        raise ValueError("sheets_already_exist")
    card = _card_html(expected, rows)
    private_write(
        out / "labels.csv", _csv(LABEL_COLUMNS, expected.labels), exclusive=True
    )
    private_write(
        out / "citations.csv",
        _csv(CITATION_COLUMNS, expected.citations),
        exclusive=True,
    )
    private_write(out / "evidence.html", card, exclusive=True)
    meta_path = out / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    meta.update(
        {
            "splitter_rules_version": SPLITTER_RULES_VERSION,
            "splitter_rules_sha256": expected.splitter_rules_sha256,
        }
    )
    private_write(
        meta_path, json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n"
    )
    return expected


def load_labels(path, expected: SheetSet) -> list[dict]:
    if expected.splitter_rules_sha256 != splitter_rules_sha256():
        raise ValueError("splitter_rules_changed")
    path = Path(path)
    citation = path.name == "citations.csv"
    columns = CITATION_COLUMNS if citation else LABEL_COLUMNS
    source = expected.citations if citation else expected.labels
    keys = (
        ("sample_id", "sentence_id", "doc_id")
        if citation
        else ("sample_id", "sentence_id")
    )
    mutable = {"supports"} if citation else {"label", "note"}
    failure = False
    try:
        meta = json.loads((path.parent / "meta.json").read_text(encoding="utf-8"))
        if (
            meta["splitter_rules_sha256"] != expected.splitter_rules_sha256
            or meta["splitter_rules_version"] != SPLITTER_RULES_VERSION
        ):
            raise ValueError("splitter_rules_changed")
        with path.open(encoding="utf-8", newline="") as target:
            reader = csv.DictReader(target, strict=True)
            if reader.fieldnames != list(columns):
                raise ValueError
            loaded = list(reader)
        index = {tuple(row[k] for k in keys): row for row in source}
        seen = set()
        if len(loaded) != len(source):
            raise ValueError
        for row in loaded:
            if set(row) != set(columns) or any(value is None for value in row.values()):
                raise ValueError
            key = tuple(row[k] for k in keys)
            if key not in index or key in seen:
                raise ValueError
            seen.add(key)
            if any(row[k] != index[key][k] for k in columns if k not in mutable):
                raise ValueError
            if citation:
                if row["supports"] not in ("지지", "비지지"):
                    raise ValueError
            elif row["label"] not in LABELS:
                raise ValueError
    except (OSError, ValueError, UnicodeError, TypeError, KeyError, csv.Error):
        failure = True
    if failure:
        raise ValueError("labels_invalid") from None
    return loaded

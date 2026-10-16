"""Sentence sheets with synthetic private text, exact membership and permissions."""

import csv
import json
import stat
from dataclasses import replace

import pytest
from test_grounding_sample import pool

from govops.grounding import sheets
from govops.grounding.evidence import Evidence, EvidenceItem


def sheet_dir(root, split="calibration"):
    return root / "data/interim/m8-grounding" / split


@pytest.mark.parametrize("mutation", ["header", "count", "duplicate", "utf8", "meta"])
def test_sheet_contract_rejects_corruption(tmp_path, mutation):
    out = sheet_dir(tmp_path)
    expected = sheets.write_label_sheets(pool(2), out)
    fill(out / "labels.csv", "label", ["supported"])
    path = out / "labels.csv"
    lines = path.read_text().splitlines(keepends=True)
    if mutation == "header":
        path.write_text("unexpected," + "".join(lines))
    elif mutation == "count":
        path.write_text("".join(lines[:-1]))
    elif mutation == "duplicate":
        path.write_text("".join([lines[0], lines[1], lines[1]]))
    elif mutation == "utf8":
        path.write_bytes(b"\xff")
    else:
        meta = json.loads((out / "meta.json").read_text())
        meta["splitter_rules_sha256"] = "f" * 64
        (out / "meta.json").write_text(json.dumps(meta))
    with pytest.raises(ValueError):
        sheets.load_labels(path, expected)


def fill(path, column, values):
    with path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        fields, rows = reader.fieldnames, list(reader)
    for i, row in enumerate(rows):
        row[column] = values[i % len(values)]
    with path.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def test_sentence_columns_blind_shuffle_escape_and_permissions(tmp_path):
    rows = pool(6)
    rows = [
        replace(
            r,
            observation=r.observation.model_copy(
                update={"answer": "합성입니다(syn-guide-001).\n감사합니다."}
            ),
            evidence=Evidence(
                "ok",
                (EvidenceItem("lookup_id", "<script>alert('합성')</script>", 30),),
                30,
                0,
            ),
        )
        for r in rows
    ]
    out = sheet_dir(tmp_path)
    expected = sheets.write_label_sheets(rows, out)
    with (out / "labels.csv").open() as source:
        reader = csv.DictReader(source)
        assert reader.fieldnames == [
            "sample_id",
            "sentence_id",
            "task_id",
            "type",
            "request",
            "sentence",
            "label",
            "note",
        ]
        labels = list(reader)
    assert len(labels) == 12
    assert all(row["request"] for row in labels[::2])
    assert all(not row["request"] for row in labels[1::2])
    assert [r["task_id"] for r in labels[::2]] != [r.spec.task_id for r in rows]
    assert [r["sample_id"] for r in labels[::2]] == [f"S{i:02}" for i in range(1, 7)]
    assert sheets.CITATION_COLUMNS == ("sample_id", "sentence_id", "doc_id", "supports")
    card = (out / "evidence.html").read_text()
    assert "<script>" not in card and "&lt;script&gt;" in card
    assert "<script" not in card and "src=" not in card
    assert "groundedness" not in (out / "labels.csv").read_text()
    for path in out.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (
        json.loads((out / "meta.json").read_text())["splitter_rules_sha256"]
        == expected.splitter_rules_sha256
    )
    with pytest.raises(ValueError):
        sheets.load_labels(out / "labels.csv", expected)
    fill(
        out / "labels.csv",
        "label",
        ["supported", "unsupported", "contradicted", "not_claim"],
    )
    fill(out / "citations.csv", "supports", ["지지", "비지지"])
    assert len(sheets.load_labels(out / "labels.csv", expected)) == 12
    assert len(sheets.load_labels(out / "citations.csv", expected)) == 6


@pytest.mark.parametrize("bad", ["", "0", "Supported", "지지", "unknown"])
def test_label_values_and_exact_identity(tmp_path, bad):
    out = sheet_dir(tmp_path)
    expected = sheets.write_label_sheets(pool(2), out)
    fill(out / "labels.csv", "label", [bad])
    with pytest.raises(ValueError, match="labels_invalid"):
        sheets.load_labels(out / "labels.csv", expected)
    fill(out / "labels.csv", "label", ["supported"])
    text = (out / "labels.csv").read_text()
    (out / "labels.csv").write_text(text.replace(",s1,", ",s9,", 1))
    with pytest.raises(ValueError):
        sheets.load_labels(out / "labels.csv", expected)


def test_citation_validation_splitter_and_immutable_text(tmp_path, monkeypatch):
    rows = pool(1)
    rows[0] = replace(
        rows[0],
        observation=rows[0].observation.model_copy(
            update={"answer": "합성 syn-guide-001 syn-guide-002."}
        ),
    )
    out = sheet_dir(tmp_path)
    expected = sheets.write_label_sheets(rows, out)
    assert len(expected.citations) == 2
    for value in ("", "true", "supported", "지 지"):
        fill(out / "citations.csv", "supports", [value])
        with pytest.raises(ValueError):
            sheets.load_labels(out / "citations.csv", expected)
    fill(out / "labels.csv", "label", ["supported"])
    text = (out / "labels.csv").read_text()
    (out / "labels.csv").write_text(text.replace("합성", "다른 합성"))
    with pytest.raises(ValueError):
        sheets.load_labels(out / "labels.csv", expected)
    monkeypatch.setattr(sheets, "splitter_rules_sha256", lambda: "changed")
    with pytest.raises(ValueError, match="splitter_rules_changed"):
        sheets.load_labels(out / "citations.csv", expected)
    with pytest.raises(ValueError, match="splitter_rules_changed"):
        sheets.write_label_sheets(rows, out, registered_splitter="old")

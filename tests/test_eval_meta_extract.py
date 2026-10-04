"""Extraction against hand-written synthetic repositories only."""

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
import yaml

from govops.eval import meta_extract
from govops.eval.meta import MetaValidationError, load_meta, meta_sha256


@pytest.fixture
def a_root(tmp_path):
    root = tmp_path / "a"
    syn = root / "data/synthetic"
    syn.mkdir(parents=True)
    seed = root / "data/seed"
    for kind in ("incidents", "events"):
        (seed / kind).mkdir(parents=True)
    (seed / "banned_names.yaml").write_text("names: [FORBIDDEN_SAMPLE]")
    source = {
        "id": "inc-1",
        "owner_team": "platform",
        "facts": [{"fact_id": "f-1", "kind": "text", "value": "합성 점검 완료"}],
    }
    (seed / "incidents/one.yaml").write_text(yaml.safe_dump(source))
    source = {
        "id": "evt-1",
        "owner_team": "sre",
        "facts": [{"fact_id": "f-2", "kind": "id", "value": "syn-service"}],
    }
    (seed / "events/one.yaml").write_text(yaml.safe_dump({"events": [source]}))
    docs = [
        {
            "doc_id": f"syn-{n}",
            "doc_type": "meeting",
            "team": "platform",
            "access": {"level": "team", "teams": ["platform"], "min_grade": 1},
            "fact_ids": ["f-1", "f-2"],
            "related_doc_ids": ["syn-1"],
            "canary_fact_id": "f-c" if n == 1 else None,
            "injection_id": "inj-1" if n == 2 else None,
            "title_hint": "OMITTED",
        }
        for n in (3, 2, 1)
    ]
    (syn / "plan.jsonl").write_text("\n".join(json.dumps(d) for d in docs))
    (syn / "canaries.json").write_text(
        json.dumps(
            {
                "canaries": [
                    {
                        "canary_id": "c-1",
                        "fact_id": "f-c",
                        "value": "SYN_CANARY",
                        "doc_id": "syn-1",
                        "owner_team": "platform",
                        "min_grade": 4,
                    }
                ],
                "count": 1,
            }
        )
    )
    (syn / "injections.json").write_text(
        json.dumps(
            {
                "injections": [
                    {
                        "injection_id": "inj-1",
                        "doc_id": "syn-2",
                        "variant": "notice",
                        "access_level": "team",
                        "text": "INJECTION_SENTINEL",
                        "insert_rule": "omit",
                    }
                ],
                "count": 1,
            }
        )
    )
    (syn / "docs").mkdir()
    (syn / "docs/body.txt").write_text("BODY_SENTINEL")
    with sqlite3.connect(syn / "ledger.sqlite") as c:
        c.execute(
            "create table tickets (ticket_id text, incident_id text, is_anchor integer, fact_ids text)"
        )
        c.executemany(
            "insert into tickets values (?, ?, ?, ?)",
            [("SYN-1", "inc-1", 1, "f-1,f-2"), ("SYN-2", None, 0, "")],
        )
    for args in (
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-m",
            "fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    return root


EXPECTED = {
    "doc_count": 3,
    "doc_type_counts": {"meeting": 3},
    "canary_doc_count": 1,
    "injection_doc_count": 1,
}


def snapshot(root):
    return {
        str(p.relative_to(root)): (
            p.stat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest(),
        )
        for p in (root / "data").rglob("*")
        if p.is_file()
    }


def test_deterministic_read_only_and_no_body(a_root, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    before = snapshot(a_root)
    original = Path.open

    def guarded(path, *args, **kwargs):
        assert "docs" not in path.parts
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    assert (
        meta_extract.main(
            ["--a-root", str(a_root), "--out", str(out)], expected=EXPECTED
        )
        == 0
    )
    first = {p.name: p.read_bytes() for p in out.iterdir()}
    meta_extract.extract(a_root, out, expected=EXPECTED)
    assert first == {p.name: p.read_bytes() for p in out.iterdir()}
    monkeypatch.setattr(Path, "open", original)
    assert snapshot(a_root) == before
    assert all(
        b"BODY_SENTINEL" not in b and b"INJECTION_SENTINEL" not in b
        for b in first.values()
    )
    meta = load_meta(out / "meta.json")
    assert [d.doc_id for d in meta.docs] == ["syn-1", "syn-2", "syn-3"]
    assert meta.fact_by_id["f-c"].source_kind == "canary"
    assert len(meta.tickets) == 1
    report = json.loads(first["meta.manifest.json"])
    assert report["meta_sha256"] == meta_sha256(out / "meta.json")
    assert type(report["fact_count"]) is int
    assert "SYN_CANARY" not in capsys.readouterr().out


@pytest.mark.parametrize("mutation", ["id", "reference", "banned"])
def test_invalid_keeps_existing(a_root, tmp_path, mutation, capsys):
    out = tmp_path / "out"
    meta_extract.extract(a_root, out, expected=EXPECTED)
    before = (out / "meta.json").read_bytes()
    plan = a_root / "data/synthetic/plan.jsonl"
    rows = [json.loads(line) for line in plan.read_text().splitlines()]
    if mutation == "id":
        rows[0]["doc_id"] = "real-doc"
    elif mutation == "reference":
        rows[0]["fact_ids"] = ["missing"]
    else:
        p = a_root / "data/seed/incidents/one.yaml"
        p.write_text(p.read_text().replace("value:", "value: FORBIDDEN_SAMPLE #"))
    plan.write_text("\n".join(json.dumps(r) for r in rows))
    with pytest.raises(MetaValidationError):
        meta_extract.extract(a_root, out)
    assert meta_extract.main(["--a-root", str(a_root), "--out", str(out)]) == 1
    assert (out / "meta.json").read_bytes() == before
    assert "FORBIDDEN_SAMPLE" not in capsys.readouterr().err


def test_mismatch_and_source_output_rejection(a_root, tmp_path, capsys):
    out = tmp_path / "out"
    assert meta_extract.main(["--a-root", str(a_root), "--out", str(out)]) == 1
    assert "MISMATCH" in capsys.readouterr().out
    assert (
        json.loads((out / "meta.manifest.json").read_text())["expectations_match"]
        is False
    )
    with pytest.raises(MetaValidationError):
        meta_extract.extract(a_root, a_root / "out")


def test_no_docs_directory_and_json_ticket_facts(a_root, tmp_path):
    body = a_root / "data/synthetic/docs/body.txt"
    body.unlink()
    body.parent.rmdir()
    with sqlite3.connect(a_root / "data/synthetic/ledger.sqlite") as db:
        db.execute(
            "UPDATE tickets SET fact_ids=? WHERE is_anchor=1",
            (json.dumps(["f-2", "f-1"]),),
        )
    out = tmp_path / "out"
    meta_extract.extract(a_root, out, expected=EXPECTED)
    assert load_meta(out / "meta.json").tickets[0].fact_ids == ["f-2", "f-1"]


def test_publication_failure_restores_both_files(a_root, tmp_path, monkeypatch):
    import os

    out = tmp_path / "out"
    meta_extract.extract(a_root, out, expected=EXPECTED)
    before = {p.name: p.read_bytes() for p in out.iterdir()}
    original = os.replace
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if Path(source).name == "meta.manifest.json" and not failed:
            failed = True
            raise OSError("synthetic publication failure")
        original(source, destination)

    monkeypatch.setattr(os, "replace", fail_once)
    with pytest.raises(MetaValidationError):
        meta_extract.extract(a_root, out, expected=EXPECTED)
    assert before == {p.name: p.read_bytes() for p in out.iterdir()}

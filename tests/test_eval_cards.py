import ast
import hashlib
import json
from html import escape
from pathlib import Path

from test_eval_taskgen import fixture

from govops.eval.cards import build_cards
from govops.eval.review_cli import main
from govops.eval.spec import dump_specs, spec_hash
from govops.eval.taskgen import generate_tasks


def test_cards_and_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["cards"]) == 1
    assert "make eval-gen" in capsys.readouterr().out
    meta, tickets, users = fixture()
    specs = generate_tasks(meta, tickets, users)
    for name, payload in [
        ("eval/meta", meta.model_dump()),
        ("seed/users", {"users": users}),
        ("seed/tickets", {"tickets": tickets}),
    ]:
        path = tmp_path / f"data/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    dump_specs(specs, tmp_path / "data/eval/tasks.json")
    review = tmp_path / "data/eval/review/confirmed.json"
    review.parent.mkdir()
    review.write_text(
        json.dumps(
            {
                "reviewer": "검수자",
                "reviewed_at": "2026-10-04",
                "tasks": {
                    s.task_id: {
                        "status": "approved",
                        "spec_sha256": spec_hash(s),
                        "note": "",
                    }
                    for s in specs
                },
            }
        )
    )
    before = (review.stat().st_mtime_ns, hashlib.sha256(review.read_bytes()).digest())
    assert main(["status"]) == 0
    assert main(["cards"]) == 0
    assert before == (
        review.stat().st_mtime_ns,
        hashlib.sha256(review.read_bytes()).digest(),
    )
    payload = json.loads(review.read_text())
    payload["tasks"][specs[0].task_id]["status"] = "rejected"
    review.write_text(json.dumps(payload))
    assert main(["status"]) == 1
    review.unlink()
    assert main(["status"]) == 1
    specs[0] = specs[0].model_copy(update={"request": "<script>&"})
    paths = build_cards(specs, meta, users, tickets, tmp_path / "cards")
    assert {p.name for p in paths} == {
        "index.html",
        *(f"T{i}.html" for i in range(1, 7)),
    }
    html = "".join(p.read_text() for p in paths)
    for s in specs:
        assert s.task_id in html and escape(s.request) in html
        for f in s.required_facts:
            assert f.fact_id in html
        for did in s.provenance.source_doc_ids:
            assert did in html
    assert "<script>&" not in html
    assert all(v not in html for v in ["http://", "https://", "src=", "BODY_SENTINEL_"])
    import re

    assert set(re.findall(r"syn-doc-\d+", html)) <= meta.doc_by_id.keys()


def test_review_writes_are_absent():
    root = Path(__file__).resolve().parents[1] / "src/govops/eval"
    for name in ["cards.py", "review.py", "review_cli.py"]:
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in {
                "write_text",
                "write_bytes",
            }:
                assert name == "cards.py" and ast.unparse(node.func.value) == "path"
            text = ast.unparse(node)
            if "confirmed" in text or "REVIEW_PATH" in text:
                assert not (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"write_text", "write_bytes"}
                )
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    assert not any(
                        isinstance(a, ast.Constant)
                        and isinstance(a.value, str)
                        and any(c in a.value for c in "wax+")
                        for a in node.args[1:]
                    )

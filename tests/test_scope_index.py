"""Access-only index regeneration and source fidelity."""

import hashlib
import json
from pathlib import Path

from govops.eval.scope_index import build_index, encode, main

ROOT = Path(__file__).resolve().parents[1]
# Explicit allowlist from A committed current targets and prior version manifest.
REAL_DOC_IDS = [
    "law-2200000111035",
    "law-2200000111091",
    "law-2200000111093",
    "law-2200000111097",
    "law-2200000111365",
    "law-2200000111403",
    "law-2200000111637",
    "law-2200000118161",
    "law-2200000131087",
    "law-2200000132645",
    "law-2200000134309",
    "law-2200000134947",
    "law-2200000135767",
    "law-2200000135887",
    "law-2200000135895",
    "law-2200000136721",
    "law-2200000139795",
    "law-2200000139835",
    "law-2200000140323",
    "law-2200000148259",
    "law-2200000148319",
    "law-2200000148325",
    "law-2200000148327",
    "law-2200000148329",
    "law-2200000148341",
    "law-2200000148343",
    "law-2200000148345",
    "law-2200000148347",
    "law-2200000148533",
    "law-2200000148535",
    "law-2200000148719",
    "law-2200000148721",
    "law-2200000148723",
    "law-2200000148727",
    "law-2200000148729",
    "law-2200000148731",
    "law-2200000148733",
    "law-2200000148757",
    "law-2200000148759",
    "law-2200000148765",
    "law-2200000148767",
    "law-2200000148769",
    "law-2200000148771",
    "law-2200000148773",
    "law-2200000148775",
    "law-2200000148777",
    "law-2200000148779",
    "law-2200000149159",
    "law-2200000149437",
    "law-2200000149619",
    "law-2200000149621",
    "law-2200000149879",
    "law-2200000150467",
    "law-2200000150469",
    "law-2200000150471",
    "law-2200000150473",
    "law-2200000150475",
    "law-2200000150479",
    "law-2200000150481",
    "law-2200000150483",
    "law-2200000150485",
    "law-2200000150495",
    "law-2200000150561",
    "law-2200000150563",
    "law-2200000150565",
    "law-2200000150567",
    "law-2200000150569",
    "law-2200000150573",
    "law-2200000150587",
    "law-2200000150589",
    "law-2200000150591",
    "law-2200000150593",
    "law-2200000150595",
    "law-2200000150597",
    "law-2200000150619",
    "law-2200000150623",
    "law-2200000150625",
    "law-2200000150627",
    "law-2200000150629",
    "law-2200000150631",
    "law-2200000150633",
    "law-2200000150635",
    "law-2200000150649",
    "law-2200000150653",
    "law-2200000150989",
    "law-2200000151383",
    "law-2200000151385",
    "law-2200000151389",
    "law-2200000151391",
    "law-2200000151393",
    "law-2200000151395",
    "law-2200000151397",
    "law-2200000151399",
    "law-2200000151401",
    "law-2200000151403",
    "law-2200000151801",
    "law-2200000151803",
    "law-2200000151831",
    "law-2200000152495",
    "law-2200000156139",
]


def test_committed_index():
    meta_path = ROOT / "data/eval/meta.json"
    ticket_path = ROOT / "data/seed/tickets.json"
    meta = json.loads(meta_path.read_text())
    tickets = json.loads(ticket_path.read_text())["tickets"]
    expected = build_index(meta, tickets)
    expected["generated_from"].update(
        meta_sha256=hashlib.sha256(meta_path.read_bytes()).hexdigest(),
        tickets_sha256=hashlib.sha256(ticket_path.read_bytes()).hexdigest(),
        non_synthetic_doc_count=len(REAL_DOC_IDS),
    )
    expected["docs"].update(
        {d: {"level": "public", "teams": [], "min_grade": 0} for d in REAL_DOC_IDS}
    )
    assert (ROOT / "data/seed/access_index.json").read_bytes() == encode(expected), (
        "make scope-index 재생성 필요"
    )
    assert set(expected) == {"generated_from", "docs", "tickets"}
    users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
    for section in ("docs", "tickets"):
        for key, row in expected[section].items():
            assert set(row) == {"level", "teams", "min_grade"}
            assert key not in {u["label"] for u in users}
    assert all(d.startswith("syn-") or d in REAL_DOC_IDS for d in expected["docs"])
    for section, rows, id_key in [
        ("docs", meta["docs"], "doc_id"),
        ("tickets", tickets, "ticket_id"),
    ]:
        for row in rows:
            assert expected[section][row[id_key]] == {
                "level": row["access_level"],
                "teams": row["access_teams"],
                "min_grade": row["access_min_grade"],
            }


def test_cli_synthetic_offline(tmp_path):
    out = tmp_path / "index.json"
    assert main(["--out", str(out)]) == 0
    index = json.loads(out.read_text())
    assert all(d.startswith("syn-") for d in index["docs"])
    assert out.read_bytes() == encode(index)


def test_committed_a_projection(monkeypatch, tmp_path):
    from govops.eval.scope_index import committed_real_docs

    commit = "a" * 40
    sources = {
        "src/kodocs/chunking/corpus.py": 'def _load_current_document():\n    return Access("public", (), 0)\ndef _law_api_document():\n    return Access("public", (), 0)\n',
        "data/manifest/parse_targets.json": json.dumps(
            {
                "targets": [
                    {"role": "current", "law_serial": d.removeprefix("law-")}
                    for d in REAL_DOC_IDS
                ]
            }
        ),
        "data/manifest/m5_prior_versions.json": json.dumps({"versions": []}),
    }

    def read(command, **kwargs):
        assert command[:4] == ["git", "-C", str(tmp_path), "show"]
        revision, path = command[4].split(":", 1)
        assert revision == commit
        return sources[path]

    monkeypatch.setattr("govops.eval.scope_index.subprocess.check_output", read)
    assert committed_real_docs(tmp_path, commit) == {
        d: {"level": "public", "teams": [], "min_grade": 0} for d in REAL_DOC_IDS
    }
    sources["src/kodocs/chunking/corpus.py"] = sources[
        "src/kodocs/chunking/corpus.py"
    ].replace('"public"', '"team"')
    import pytest

    with pytest.raises(ValueError, match="policy changed"):
        committed_real_docs(tmp_path, commit)

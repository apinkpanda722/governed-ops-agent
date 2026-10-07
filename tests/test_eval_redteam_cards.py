import copy
import json
from collections import Counter
from html import escape
from pathlib import Path

import pytest

from govops.eval import redteam_cards as cards


def inputs():
    return cards.load_inputs()


def test_cards_offline_deterministic_and_complete():
    items, digest, overlay = inputs()
    page = cards.render_cards(items, digest, overlay)
    assert page == cards.render_cards(items, digest, overlay)
    assert page.count("<article ") == 120
    assert Counter(i["category"] for i in items) == {
        "direct_injection": 30,
        "indirect_injection": 40,
        "identity_impersonation": 20,
        "pii_exfil": 30,
    }
    for item in items:
        assert f'id="{item["id"]}"' in page
    for forbidden in (
        "<script src",
        "<link",
        "https://",
        "http://",
        "rules_sha256",
        "fallback_layers",
        "spec_sha256",
        "localStorage",
        "sessionStorage",
    ):
        assert forbidden not in page
    for heading in ("이 검수가 무엇인지", "칸별 판정 기준", "고정 예시", "기록 방법"):
        assert heading in page
    for good, bad in cards.EXAMPLES.values():
        assert escape(good) in page and escape(bad) in page
        assert all(good != i["request"] and bad != i["request"] for i in items)
    for ticket in overlay["tickets"]:
        assert escape(ticket["description"]) in page
        for comment in ticket["comments"]:
            assert escape(comment["body"]) in page


def test_attack_is_escaped():
    items, digest, overlay = inputs()
    items = copy.deepcopy(items)
    items[0]["request"] = '<script>alert("synthetic")</script>&'
    page = cards.render_cards(items, digest, overlay)
    assert "<script>alert" not in page
    assert "&lt;script&gt;alert(&quot;synthetic&quot;)&lt;/script&gt;&amp;" in page


def confirmation():
    items, digest, _ = inputs()
    return {
        "set_sha256": digest,
        "reviewer": "검수자",
        "items": {
            i["id"]: {
                "validity": "ok",
                "synthetic": "ok",
                "layer": "ok",
                "memo": "",
                "verdict": "approved",
            }
            for i in items
        },
    }


def test_status_cases(tmp_path, capsys):
    path = tmp_path / "confirmed.json"
    assert cards.main(["--status", "--confirmed", str(path)]) == 1
    assert "미검수" in capsys.readouterr().out
    raw = confirmation()
    raw["set_sha256"] = "0" * 64
    path.write_text(json.dumps(raw))
    assert cards.main(["--status", "--confirmed", str(path)]) == 1
    assert "무효(세트가 바뀜)" in capsys.readouterr().out
    raw = confirmation()
    row = raw["items"].pop("RT-D-01")
    path.write_text(json.dumps(raw))
    assert cards.main(["--status", "--confirmed", str(path)]) == 1
    assert "미완료(119/120)" in capsys.readouterr().out
    row.update(validity="bad", verdict="excluded")
    raw["items"]["RT-D-01"] = row
    path.write_text(json.dumps(raw))
    before = path.read_bytes()
    assert cards.main(["--status", "--confirmed", str(path)]) == 0
    assert "완료: 승인 119건·제외 1건" in capsys.readouterr().out
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "patch",
    [
        {"validity": "unknown"},
        {"synthetic": "unknown"},
        {"layer": "unknown"},
        {"memo": 1},
        {"verdict": "excluded"},
        {"layer": "input_block", "verdict": "excluded"},
    ],
)
def test_invalid_rows_rejected(tmp_path, capsys, patch):
    raw = confirmation()
    raw["items"]["RT-D-01"].update(patch)
    path = tmp_path / "confirmed.json"
    path.write_text(json.dumps(raw))
    assert cards.main(["--status", "--confirmed", str(path)]) == 1
    assert "형식 오류" in capsys.readouterr().out


def test_cli_writes_only_html(tmp_path):
    root = Path("data/redteam")
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    out = tmp_path / "cards.html"
    assert cards.main(["--out", str(out)]) == 0
    assert out.read_text().count("<article ") == 120
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert not (tmp_path / "confirmed.json").exists()
    assert cards.main(["--out", str(tmp_path / "confirmed.json")]) == 1

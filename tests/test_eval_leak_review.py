import ast
import json
from pathlib import Path

import pytest

from govops.eval import leak_review
from govops.eval.leak_review import (
    LeakReviewFormatError,
    load_leak_confirmations,
    stale_decisions,
)


def write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def valid():
    return {
        "reviewer": "reviewer",
        "reviewed_at": "2026-10-05",
        "decisions": {
            "a" * 16: {"decision": "confirmed_leak", "note": ""},
            "b" * 16: {"decision": "false_positive", "note": "날짜"},
        },
    }


def test_loads_decisions(tmp_path):
    path = tmp_path / "leak_confirmations.json"
    write(path, valid())
    assert load_leak_confirmations(path) == {
        "a" * 16: "confirmed_leak",
        "b" * 16: "false_positive",
    }


def test_missing_file_means_nothing_decided(tmp_path):
    assert load_leak_confirmations(tmp_path / "none.json") == {}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(reviewer=""),
        lambda d: d["decisions"].update({"c" * 16: {"decision": "maybe"}}),
        lambda d: d.pop("decisions"),
        lambda d: d["decisions"].update({"short": {"decision": "false_positive"}}),
    ],
)
def test_bad_format_is_rejected(tmp_path, mutate):
    payload = valid()
    mutate(payload)
    path = tmp_path / "leak_confirmations.json"
    write(path, payload)
    with pytest.raises(LeakReviewFormatError):
        load_leak_confirmations(path)
    path.write_text("not json")
    with pytest.raises(LeakReviewFormatError):
        load_leak_confirmations(path)


def test_stale_decisions_are_reported():
    decisions = {"a" * 16: "confirmed_leak", "z" * 16: "false_positive"}
    assert stale_decisions(["a" * 16, "b" * 16], decisions) == ("z" * 16,)


def test_loader_never_writes_the_file():
    tree = ast.parse(Path(leak_review.__file__).read_text(encoding="utf-8"))
    writers = {"write_text", "write_bytes", "unlink", "rename", "replace"}
    calls = [
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert not writers & set(calls)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
            modes = [a.value for a in node.args[1:] if isinstance(a, ast.Constant)]
            assert not any(set(str(m)) & set("wax+") for m in modes)

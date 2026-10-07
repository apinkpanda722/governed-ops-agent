import hashlib
import json

import pytest

from govops.guardrails.injection import load_rules
from govops.guardrails.registry import RULES_FILE, rules_sha256


def test_hash_bytes_only(tmp_path):
    path = tmp_path / "rules"
    path.write_bytes(b"not json\n")
    assert rules_sha256(path) == hashlib.sha256(path.read_bytes()).hexdigest()
    assert rules_sha256() == hashlib.sha256(RULES_FILE.read_bytes()).hexdigest()


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"version": 2, "rules": []},
        {
            "version": 1,
            "rules": [{"id": "Bad", "scope": ["input"], "family": "x", "pattern": "("}],
        },
    ],
)
def test_invalid_rules(tmp_path, raw):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="Invalid rules"):
        load_rules(path)


@pytest.mark.parametrize(
    "update",
    [
        {"pattern": "("},
        {"pattern": ""},
        {"pattern": "a*"},
        {"scope": []},
        {"scope": ["system"]},
        {"family": ""},
        {"id": "bad-id"},
    ],
)
def test_invalid_rule_fields(tmp_path, update):
    rule = {
        "id": "test",
        "scope": ["input"],
        "pattern": "ignore previous instructions",
        "family": "ignore",
    }
    rule.update(update)
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"version": 1, "rules": [rule]}))
    with pytest.raises(ValueError) as caught:
        load_rules(path)
    assert str(caught.value) == "Invalid rules"


def test_duplicate_ids(tmp_path):
    rule = {
        "id": "test",
        "scope": ["input"],
        "pattern": "ignore previous instructions",
        "family": "ignore",
    }
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"version": 1, "rules": [rule, rule]}))
    with pytest.raises(ValueError):
        load_rules(path)

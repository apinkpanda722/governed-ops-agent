"""Safe metadata schema and invariant diagnostics."""

import json

import pytest

from govops.eval.meta import MetaValidationError, load_meta, validate_meta


def payload():
    return {
        "a_commit": "a" * 40,
        "docs": [
            {
                "doc_id": "syn-1",
                "doc_type": "meeting",
                "team": "platform",
                "access_level": "public",
                "access_teams": [],
                "access_min_grade": 0,
                "fact_ids": ["f-1"],
                "related_doc_ids": [],
                "injection_id": None,
                "canary_fact_id": None,
            }
        ],
        "facts": [
            {
                "fact_id": "f-1",
                "kind": "text",
                "value": "SAFE_VALUE",
                "source_kind": "incident",
                "source_id": "inc-1",
                "owner_team": "platform",
            }
        ],
        "canaries": [],
        "injections": [],
        "tickets": [],
    }


@pytest.mark.parametrize(
    "case",
    ["duplicate_doc", "duplicate_fact", "level", "team", "related", "grade", "extra"],
)
def test_invalid_schema_is_safe(case, tmp_path):
    raw = payload()
    doc = raw["docs"][0]
    if case == "duplicate_doc":
        raw["docs"].append(doc.copy())
    elif case == "duplicate_fact":
        raw["facts"].append(raw["facts"][0].copy())
    elif case == "level":
        doc["access_level"] = "SAFE_VALUE"
    elif case == "team":
        doc["access_teams"] = ["SAFE_VALUE"]
    elif case == "related":
        doc["related_doc_ids"] = ["syn-missing"]
    elif case == "grade":
        doc["access_min_grade"] = True
    else:
        doc["body"] = "SAFE_VALUE"
    path = tmp_path / "meta.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(MetaValidationError) as error:
        load_meta(path)
    assert "SAFE_VALUE" not in str(error.value)


def test_indexes():
    meta = validate_meta(payload())
    assert meta.doc_by_id["syn-1"].fact_ids == ["f-1"]

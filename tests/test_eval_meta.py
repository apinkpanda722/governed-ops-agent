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


def test_service_source_kind_is_accepted():
    raw = payload()
    raw["facts"].append(
        {
            "fact_id": "f-service-1",
            "kind": "service",
            "value": "SAFE_SERVICE",
            "source_kind": "service",
            "source_id": "core-gateway",
            "owner_team": "platform",
        }
    )
    raw["docs"][0]["fact_ids"].append("f-service-1")
    assert validate_meta(raw).fact_by_id["f-service-1"].source_kind == "service"


def test_unknown_source_kind_is_rejected():
    raw = payload()
    raw["facts"][0]["source_kind"] = "unknown"
    with pytest.raises(MetaValidationError):
        validate_meta(raw)


def diagnose(raw):
    with pytest.raises(MetaValidationError) as error:
        validate_meta(raw)
    message = str(error.value)
    assert "SAFE_VALUE" not in message
    assert not any(token in message for token in ("syn-1", "syn-2", "syn-missing"))
    return message


def test_diagnostics_name_the_failed_check_and_count():
    raw = payload()
    second = raw["docs"][0].copy() | {"doc_id": "syn-2"}
    raw["docs"][0]["fact_ids"] = ["f-missing"]
    second["fact_ids"] = ["f-missing", "f-1"]
    raw["docs"].append(second)
    message = diagnose(raw)
    assert message.startswith("invalid metadata schema or references")
    assert "doc fact_ids reference missing facts: 2 docs" in message


def test_diagnostics_cover_other_checks():
    raw = payload()
    raw["docs"].append(raw["docs"][0].copy())
    assert "duplicate doc_id: 1" in diagnose(raw)
    raw = payload()
    raw["docs"][0]["related_doc_ids"] = ["syn-missing"]
    assert "doc related_doc_ids reference missing docs: 1 docs" in diagnose(raw)
    raw = payload()
    raw["docs"][0]["doc_id"] = "real-1"
    assert "doc_id without syn- prefix: 1 docs" in diagnose(raw)
    raw = payload()
    raw["docs"][0]["extra"] = "SAFE_VALUE"
    assert "schema: " in diagnose(raw)
    raw = payload()
    raw["a_commit"] = "short"
    assert "a_commit format" in diagnose(raw)


def test_load_meta_keeps_the_diagnostic(tmp_path):
    raw = payload()
    raw["docs"][0]["fact_ids"] = ["f-missing"]
    path = tmp_path / "meta.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(MetaValidationError) as error:
        load_meta(path)
    assert "doc fact_ids reference missing facts: 1 docs" in str(error.value)
    path.write_text("{not json")
    with pytest.raises(MetaValidationError) as error:
        load_meta(path)
    assert "not json" not in str(error.value)

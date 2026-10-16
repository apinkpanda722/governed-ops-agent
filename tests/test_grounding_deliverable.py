"""Deliverable and citation selection without frozen accuracy helpers or PII scans."""

import ast
import random
from pathlib import Path

import pytest

from govops.eval.observation import Observation
from govops.grounding import deliverable


@pytest.mark.parametrize(
    ("bodies", "answer", "expected"),
    [
        (
            ["  실제 발송 본문  "],
            "승인된 내용대로 실행했습니다",
            ("  실제 발송 본문  ", "effect_body"),
        ),
        ([], "답변", ("답변", "answer")),
        ([" \t", "두 번째 본문"], "답변", ("답변", "answer")),
        (["본문"], None, ("본문", "effect_body")),
        ([], None, (None, "empty")),
        ([" "], "\n\t", (None, "empty")),
        ([], "", (None, "empty")),
    ],
)
def test_select_deliverable(bodies, answer, expected):
    # Only the fields consumed here are needed; no derived citation field is used.
    obs = Observation.model_construct(answer=answer, effect_bodies=bodies)
    assert deliverable.select_deliverable(obs) == expected


def test_effect_priority_is_the_reverse_of_frozen_answer_first_accuracy():
    answer = "완료 안내문"
    obs = Observation.model_construct(answer=answer, effect_bodies=["발송 내용"])
    selected, source = deliverable.select_deliverable(obs)
    # The frozen helper would choose the nonempty answer. Never call/import it.
    assert selected == "발송 내용" and source == "effect_body"
    assert selected != answer


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("xsyn-incident-004 syn-incident-004a syn-incident-0044", []),
        ("xINC-2026-004 INC-2026-004a INC-2026-0044", []),
        ("syn-incident-004를 [syn-policy-012]", ["syn-incident-004", "syn-policy-012"]),
        ("abc-1234 xABC-1234 ABC-1234a ABC-12345 A-1234 ABCDEF-1234", []),
        ("ABC-1234 INC-2026-004 AB-0001", ["ABC-1234", "INC-2026-004", "AB-0001"]),
        (
            "INC-2026-004 syn-incident-004 INC-2026-004 ABC-1234 syn-incident-004",
            ["INC-2026-004", "syn-incident-004", "ABC-1234"],
        ),
        ("", []),
    ],
)
def test_extract_citation_boundaries_order_and_deduplication(text, expected):
    assert deliverable.extract_citations(text) == expected


def test_fabricated_citations_excludes_observed_and_request_ids_in_order():
    citations = [
        "syn-incident-004",
        "ABC-1234",
        "INC-2026-004",
        "syn-policy-012",
        "syn-policy-012",
    ]
    assert deliverable.fabricated_citations(
        citations, {"syn-incident-004"}, "ABC-1234와 INC-2026-004 조회"
    ) == ["syn-policy-012"]
    assert deliverable.fabricated_citations([], [], "") == []


def test_10000_seeded_hex_and_numeric_strings_never_trigger_pii_or_false_citations():
    rng = random.Random(20261016)
    # FP-010/011: 10,000 hex/numeric values, including long digit runs.
    for n in range(10000):
        alphabet = "0123456789abcdef" if n % 2 else "0123456789"
        value = "".join(rng.choices(alphabet, k=rng.randrange(1, 100)))
        assert deliverable.extract_citations(value) == []
        assert deliverable.fabricated_citations([value], [value], "") == []
        assert deliverable.fabricated_citations([value], [], "") == [value]


def test_module_has_no_accuracy_or_pii_dependency():
    tree = ast.parse(Path(deliverable.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module == "govops.eval.observation" or not (
                node.module or ""
            ).startswith("govops.")
        if isinstance(node, ast.Import):
            assert all(not alias.name.startswith("govops.") for alias in node.names)

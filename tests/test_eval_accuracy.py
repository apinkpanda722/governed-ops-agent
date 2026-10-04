from test_eval_observation import observation
from test_eval_tcr import spec

from govops.eval.accuracy import deliverable_text, fact_inclusion, normalize


def test_normalization_and_inclusion():
    assert normalize("e\u0301 \n AbC") == "é AbC"
    s = spec(
        required_facts=[
            {"fact_id": "f1", "kind": "service", "value": "é AbC", "mode": "literal"},
            {"fact_id": "f2", "kind": "id", "value": "ID-X", "mode": "literal"},
            {
                "fact_id": "f3",
                "kind": "description",
                "value": "ignored",
                "mode": "rubric_only",
            },
        ],
        derived_values=[{"name": "count", "value": "12건"}],
    )
    result = fact_inclusion(s, "prefix e\u0301  AbC suffix 12건")
    assert (result.required, result.included, result.missing) == (3, 2, ("f2",))
    assert result.rate == 2 / 3
    assert fact_inclusion(spec(required_facts=[], derived_values=[]), "").rate is None


def test_datetime():
    s = spec(
        required_facts=[
            {
                "fact_id": "dt",
                "kind": "datetime",
                "value": "2026-06-02T09:10",
                "mode": "literal",
            }
        ]
    )
    for text in ["2026-06-02T09:10", "2026-06-02 오전 09:10", "2026년 6월 2일 09:10"]:
        assert fact_inclusion(s, text).included == 1
    for text in ["2026-06-02", "09:10", "2026년 6월 2일", "2026/06/02 09:10"]:
        assert fact_inclusion(s, text).missing == ("dt",)


def test_deliverable():
    assert deliverable_text(observation()) == "합성 답변"
    assert (
        deliverable_text(observation(answer=None, effect_bodies=["first", "second"]))
        == "first"
    )
    assert deliverable_text(observation(answer="", effect_bodies=["first"])) == "first"
    assert deliverable_text(observation(answer=None)) is None

"""Injection invariants use 500 seeded synthetic rows per kind, never live data."""

import random
from collections import Counter
from dataclasses import replace

import pytest

from govops.grounding import inject as injection
from govops.grounding.evidence import EvidenceItem
from govops.grounding.grader import RowAggregate
from govops.grounding.sentences import Sentence, build_sentences, split_sentences


def evidence(*bodies):
    return tuple(EvidenceItem("get_document", body, len(body)) for body in bodies)


def source(value="73194286"):
    return build_sentences(f"합성 수치는 {value}입니다(syn-guide-001).\n감사합니다.")


def aggregate(sid="s3", label="unsupported", doc=None, supports=False):
    return RowAggregate(
        {sid: label},
        {sid: label in {"unsupported", "contradicted"}},
        {(sid, doc): supports} if doc else {},
        label in {"unsupported", "contradicted"},
    )


def test_assignment_sorted_seeded_balanced():
    keys = [f"row-{i}" for i in range(500)]
    assigned = injection.assign_kinds(keys, 20261016)
    assert assigned == injection.assign_kinds(list(reversed(keys)), 20261016)
    assert assigned != injection.assign_kinds(keys, 17)
    counts = Counter(assigned.values())
    assert set(counts) == set(injection.INJECTION_KINDS)
    assert max(counts.values()) - min(counts.values()) <= 1
    shuffled = sorted(keys)
    random.Random(20261016).shuffle(shuffled)
    assert list(assigned) == shuffled
    assert list(assigned.values()) == [
        injection.INJECTION_KINDS[i % 3] for i in range(500)
    ]
    assert injection.assign_kinds([], 1) == {}


@pytest.mark.parametrize("kind", injection.INJECTION_KINDS)
def test_500_seeded_rows_per_kind_guarantee_truth_and_identity(kind):
    rng = random.Random(20261016)
    for i in range(500):
        value = f"{rng.randrange(10000000, 99999999)}입니다"
        sentences = build_sentences(f"합성 수치는 {value}(syn-guide-001).\n감사합니다.")
        items = evidence(
            f"syn-guide-001 합성 수치는 {value}.",
            "syn-guide-002 다른 합성 자료입니다.",
        )
        args = (
            f"row-{i}",
            kind,
            sentences,
            items,
            ["syn-guide-001", "syn-guide-002"],
            i,
        )
        result = injection.inject(*args)
        assert isinstance(result, injection.InjectedRow)
        assert result == injection.inject(*args)
        assert sentences == build_sentences(
            f"합성 수치는 {value}(syn-guide-001).\n감사합니다."
        )
        if kind == "fact":
            assert result.sentences[:-1] == tuple(sentences)
            assert result.target_sentence_id == "s3"
            assert result.target_doc_id is None
            added = result.sentences[-1]
            assert added.sentence_id == "s3"
            assert split_sentences(added.text) == [added.text]
            # Every pool value appearing in the new fact is absent, even as a substring.
            used = [value for value, _ in injection._FACT_POOL if value in added.text]
            assert used
            assert all(value not in item.content for value in used for item in items)
        else:
            assert len(result.sentences) == len(sentences)
            assert [s.sentence_id for s in result.sentences] == ["s1", "s2"]
            rebuilt = build_sentences("\n".join(s.text for s in result.sentences))
            assert tuple(rebuilt) == result.sentences
            assert result.target_sentence_id == "s1"
            assert result.target_doc_id in result.sentences[0].citations
            assert result.sentences[1] == sentences[1]
            if kind == "fabricated_id":
                assert result.target_doc_id not in args[4]
                assert all(result.target_doc_id not in item.content for item in items)
            else:
                assert result.target_doc_id == "syn-guide-002"
                assert value not in items[1].content


def test_fact_substring_collision_reselection_and_exhaustion():
    # All but one value are embedded in longer strings, not isolated words.
    values = [value for value, _ in injection._FACT_POOL]
    items = evidence(" ".join(f"접두{value}접미" for value in values[:-1]))
    result = injection.inject("r", "fact", source(), items, [], 1)
    assert isinstance(result, injection.InjectedRow)
    assert values[-1] in result.sentences[-1].text
    exhausted = injection.inject(
        "r", "fact", source(), evidence(" ".join(values)), [], 1
    )
    assert exhausted == injection.InjectionSkip("r", "no_candidate")


def test_fact_empty_list_starts_at_s1():
    result = injection.inject("r", "fact", [], evidence("합성 근거"), [], 1)
    assert isinstance(result, injection.InjectedRow)
    assert result.target_sentence_id == "s1"


@pytest.mark.parametrize("kind", ["fabricated_id", "wrong_support"])
def test_citation_required_and_resplit_failure(kind):
    assert injection.inject("r", kind, build_sentences("안녕하세요."), [], [], 1) == (
        injection.InjectionSkip("r", "no_candidate")
    )
    # Malformed pre-existing split cannot silently renumber a citation replacement.
    sentences = [
        Sentence("s1", "두 문장. 다음 문장(syn-guide-001).", ("syn-guide-001",))
    ]
    result = injection.inject(
        "r", kind, sentences, evidence("syn-guide-002 합성 근거"), ["syn-guide-002"], 1
    )
    assert result == injection.InjectionSkip("r", "split_count_changed")


def test_wrong_support_requires_key_and_known_different_evidence():
    args = ("r", "wrong_support")
    assert injection.inject(
        *args, build_sentences("값 3(syn-guide-001)."), [], ["syn-guide-002"], 1
    ) == injection.InjectionSkip("r", "no_key_string")
    for items, observed in [
        (evidence("syn-guide-001 원본"), ["syn-guide-001"]),
        (evidence("syn-guide-002 73194286입니다"), ["syn-guide-002"]),
        (evidence("syn-guide-002 다른 자료"), []),
        (evidence("syn-guide-003 다른 자료"), ["syn-guide-002"]),
        # A later copy containing the key makes the candidate unsafe too.
        (
            evidence("syn-guide-002 다른 자료", "syn-guide-002 73194286입니다"),
            ["syn-guide-002"],
        ),
    ]:
        assert injection.inject(*args, source(), items, observed, 1) == (
            injection.InjectionSkip("r", "no_candidate")
        )


def test_wrong_support_checks_decoded_korean_json_and_longest_token():
    import json

    sentences = build_sentences("합성핵심문자열1234 값(syn-guide-001).")
    body = json.dumps({"doc_id": "syn-guide-002", "text": "합성핵심문자열1234"})
    assert injection.inject(
        "r", "wrong_support", sentences, evidence(body), ["syn-guide-002"], 1
    ) == injection.InjectionSkip("r", "no_candidate")


def test_replacement_itself_can_change_korean_sentence_split():
    # sent-v1 splits after 다 before uppercase ASCII, but not before lowercase syn.
    sentences = build_sentences("합성수치1234다 syn-guide-001")
    assert len(sentences) == 1
    assert injection.inject(
        "r",
        "wrong_support",
        sentences,
        evidence("ABCD-1000 다른 합성 자료"),
        ["ABCD-1000"],
        1,
    ) == injection.InjectionSkip("r", "split_count_changed")


def test_fabricated_id_reselects_collisions(monkeypatch):
    # Shrink the synthetic ID pool only to exercise finite exhaustion deterministically.
    monkeypatch.setattr(
        injection, "_FABRICATED_IDS", ("syn-injected-001", "syn-injected-002")
    )
    assert injection.inject(
        "r",
        "fabricated_id",
        source(),
        evidence("syn-injected-002"),
        ["syn-injected-001"],
        1,
    ) == injection.InjectionSkip("r", "no_candidate")
    result = injection.inject(
        "r", "fabricated_id", source(), evidence("prefixsyn-injected-001suffix"), [], 1
    )
    assert isinstance(result, injection.InjectedRow)
    assert result.target_doc_id == "syn-injected-002"


@pytest.mark.parametrize(
    "label,expected",
    [
        ("unsupported", True),
        ("contradicted", True),
        ("supported", False),
        ("not_claim", False),
    ],
)
def test_detection_exact_fact_sentence(label, expected):
    row = injection.InjectedRow("r", "fact", (), "s3", None)
    assert injection.injection_detected(row, aggregate(label=label)) is expected
    assert not injection.injection_detected(row, aggregate("s30", label))
    assert not injection.injection_detected(row, RowAggregate({}, {}, {}, False))


@pytest.mark.parametrize("kind", ["fabricated_id", "wrong_support"])
def test_detection_exact_citation_pair(kind):
    row = injection.InjectedRow("r", kind, (), "s1", "syn-guide-002")
    assert injection.injection_detected(row, aggregate("s1", doc="syn-guide-002"))
    assert not injection.injection_detected(row, aggregate("s10", doc="syn-guide-002"))
    assert not injection.injection_detected(row, aggregate("s1", doc="syn-guide-0020"))
    assert not injection.injection_detected(
        row, aggregate("s1", doc="syn-guide-002", supports=True)
    )
    assert not injection.injection_detected(row, aggregate("s1"))


def test_invalid_kind_and_noncanonical_sentence_numbers():
    with pytest.raises(ValueError):
        injection.inject("r", "other", source(), [], [], 1)
    bad = [replace(source()[0], sentence_id="s2")]
    assert injection.inject("r", "fact", bad, [], [], 1) == (
        injection.InjectionSkip("r", "split_count_changed")
    )

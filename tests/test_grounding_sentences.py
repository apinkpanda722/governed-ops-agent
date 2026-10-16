"""Synthetic cases for the preregistered sent-v1 sentence keys."""

import hashlib
import random
import re
from pathlib import Path

import pytest

from govops.grounding import sentences


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("첫 줄\r\n\r둘째 줄\n \n마지막", ["첫 줄", "둘째 줄", "마지막"]),
        ("", []),
        (" \t\r\n ", []),
        (
            "Alpha. Beta? Gamma! Delta。 End",
            ["Alpha.", "Beta?", "Gamma!", "Delta。", "End"],
        ),
        (
            "3.5 v1.2 a.b syn-incident-004. Next.",
            ["3.5 v1.2 a.b syn-incident-004.", "Next."],
        ),
        ("syn-incident-004.", ["syn-incident-004."]),
        ("a.b.c x?y z!q", ["a.b.c x?y z!q"]),
        ("처리했다 다음 단계", ["처리했다", "다음 단계"]),
        ("처리했어요 Next 단계", ["처리했어요", "Next 단계"]),
        ("맞죠 2개", ["맞죠", "2개"]),
        ("할까 [근거]", ["할까", "[근거]"]),
        ("완료했다 (참고)", ["완료했다", "(참고)"]),
        ("완료했다 next 단계", ["완료했다 next 단계"]),
        ("완료했다 고장 점검", ["완료했다", "고장 점검"]),
        ("처리했다 고 다음 단계", ["처리했다 고 다음 단계"]),
        ("처리했다 니까 다음 단계", ["처리했다 니까", "다음 단계"]),
        ("  Alpha\t  beta.   Gamma  ", ["Alpha beta.", "Gamma"]),
    ],
)
def test_s1_s3_s4_s5(text, expected):
    assert sentences.split_sentences(text) == expected


@pytest.mark.parametrize(
    "marker",
    [
        "-",
        "*",
        "•",
        "·",
        "▪",
        "‣",
        "1.",
        "2)",
        "(3)",
        "12.",
        "99)",
        "(12)",
        *["#" * n for n in range(1, 7)],
    ],
)
def test_s2_removes_one_leading_marker_and_drops_marker_only_lines(marker):
    assert sentences.split_sentences(f"{marker} 항목\n{marker}\n{marker} \t") == [
        "항목"
    ]
    assert sentences.split_sentences(f"{marker} - 항목") == ["- 항목"]


@pytest.mark.parametrize("text", ["3.5", "####### 제목", "#hashtag", "-value"])
def test_s2_leaves_nonmarkers(text):
    assert sentences.split_sentences(text) == [text]


def test_three_digit_number_is_not_stripped_but_still_has_s3_boundary():
    assert sentences.split_sentences("100. 항목") == ["100.", "항목"]


@pytest.mark.parametrize("closer", ['"', "'", "”", "’", ")", "」", "』", "]", '")'])
def test_s3_closers_stay_with_the_previous_sentence(closer):
    assert sentences.split_sentences(f"Alpha.{closer} Beta?{closer}") == [
        f"Alpha.{closer}",
        f"Beta?{closer}",
    ]


@pytest.mark.parametrize(
    "fragment", ["고", "라고", "하고", "며", "면서", "는데", "지만", "니까", "서"]
)
def test_s4_connective_fragments_do_not_split(fragment):
    # Isolate the boundary BEFORE the fragment. Lowercase next cannot itself
    # trigger S4 after a fragment ending in 까 (the registered surface rule).
    assert sentences.split_sentences(f"처리했다 {fragment} next 단계") == [
        f"처리했다 {fragment} next 단계"
    ]


def test_numbered_sentences_citations_and_determinism():
    text = (
        "# 개요\nsyn-incident-004를 참고했다. ABC-1234 INC-2026-004 syn-incident-004."
    )
    expected = [
        sentences.Sentence("s1", "개요", ()),
        sentences.Sentence("s2", "syn-incident-004를 참고했다.", ("syn-incident-004",)),
        sentences.Sentence(
            "s3",
            "ABC-1234 INC-2026-004 syn-incident-004.",
            ("ABC-1234", "INC-2026-004", "syn-incident-004"),
        ),
    ]
    assert sentences.build_sentences(text) == expected
    assert sentences.build_sentences(text) == sentences.build_sentences(text)
    assert sentences.build_sentences("") == []


def test_5000_seeded_random_strings_have_contiguous_sentence_ids():
    rng = random.Random(20261016)
    alphabet = "abcXYZ0123456789가나다요죠까 고라고하고며면서는데지만니까서.!?。\r\n\t -*•·▪‣#()[]\"'”’」』"
    # FP-011: 5,000 independently generated synthetic strings, reproducible seed.
    for _ in range(5000):
        text = "".join(rng.choices(alphabet, k=rng.randrange(160)))
        result = sentences.build_sentences(text)
        assert [row.sentence_id for row in result] == [
            f"s{n}" for n in range(1, len(result) + 1)
        ]
        assert [row.text for row in result] == sentences.split_sentences(text)
        assert all(
            row.text and row.text == " ".join(row.text.split()) for row in result
        )


def test_rules_version_and_source_hash(monkeypatch, tmp_path):
    assert sentences.SPLITTER_RULES_VERSION == "sent-v1"
    digest = sentences.splitter_rules_sha256()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert digest == sentences.splitter_rules_sha256()
    assert digest == hashlib.sha256(Path(sentences.__file__).read_bytes()).hexdigest()
    source = tmp_path / "synthetic_sentences.py"
    source.write_bytes(b"synthetic source v1")
    monkeypatch.setattr(sentences, "__file__", str(source))
    first = sentences.splitter_rules_sha256()
    source.write_bytes(b"synthetic source v2")
    assert sentences.splitter_rules_sha256() != first

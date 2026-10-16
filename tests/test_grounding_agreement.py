"""Citation agreement and deterministic citation counts, reproduced with synthetic rows.

These pin the alignment between grader votes and human citation labels so that a low
agreement can be attributed to the grader/protocol rather than to the harness.
"""

import asyncio
import json

import pytest
from test_eval_observation import observation
from test_eval_tcr import spec

from govops.grounding import cli, sample
from govops.grounding.evidence import Evidence, EvidenceItem
from govops.grounding.grader import GradeResult, SentenceResult
from govops.grounding.sentences import build_sentences

ANSWER = (
    "합성 보고입니다(syn-guide-001).\n"
    "확인했습니다(INC-2026-004).\n"
    "조치했습니다(SYN-1003)."
)


class Spend:
    def total_usd(self, stage=None):
        return 0.0


class Grader:
    """Votes through ``vote(sentence_id, doc_id)``; labels every sentence supported."""

    def __init__(self, vote, *, drop=None):
        self.vote, self.drop = vote, drop
        self.spend = Spend()

    async def grade(self, *, sentences, **kwargs):
        rows = []
        for s in sentences:
            pairs = tuple(
                (doc, self.vote(s.sentence_id, doc))
                for doc in s.citations
                if (s.sentence_id, doc) != self.drop
            )
            rows.append(
                SentenceResult(s.sentence_id, "supported", s.citations, ("E1",), pairs)
            )
        return GradeResult(tuple(rows))


def make_rows(observed=("syn-guide-001", "INC-2026-004", "SYN-1003"), evidence=""):
    obs = observation(task_id="T1-01", answer=ANSWER, observed_ids=list(observed))
    row = sample.PoolRow(
        spec(task_id="T1-01", split="dev"),
        obs,
        Evidence("ok", (EvidenceItem("lookup_id", evidence or "합성 근거", 5),), 5, 0),
    )
    return sample.SampleRows([row])


def human_for(rows, vote):
    sentences = build_sentences(ANSWER)
    task = rows[0].spec.task_id
    labels = {s.sentence_id: "supported" for s in sentences}
    supports = {
        (s.sentence_id, doc): vote(s.sentence_id, doc)
        for s in sentences
        for doc in s.citations
    }
    return {task: labels}, {task: supports}


def evaluate(rows, grader, human, supports):
    return asyncio.run(
        cli._evaluate(
            rows,
            human,
            supports,
            grader=grader,
            version="v3.0",
            stage="calibration",
            repeats=1,
        )
    )


def test_agreement_is_one_when_votes_match_for_every_id_type():
    rows = make_rows()
    human, supports = human_for(rows, lambda sid, doc: doc != "INC-2026-004")
    result = evaluate(
        rows, Grader(lambda sid, doc: doc != "INC-2026-004"), human, supports
    )
    assert result["n_human_citation_pairs"] == 3 and result["citation_agreement"] == 1.0


@pytest.mark.parametrize(
    "grader_vote,human_vote,expected",
    [(True, False, 0.0), (False, True, 0.0), (False, False, 1.0), (True, True, 1.0)],
)
def test_vote_direction_is_not_inverted_and_mismatch_is_not_defaulted(
    grader_vote, human_vote, expected
):
    rows = make_rows()
    human, supports = human_for(rows, lambda sid, doc: human_vote)
    result = evaluate(rows, Grader(lambda sid, doc: grader_vote), human, supports)
    assert result["citation_agreement"] == expected


def test_a_missing_grader_pair_is_an_error_not_a_false_vote():
    rows = make_rows()
    human, supports = human_for(rows, lambda sid, doc: True)
    grader = Grader(lambda sid, doc: True, drop=("s1", "syn-guide-001"))
    with pytest.raises(KeyError):
        evaluate(rows, grader, human, supports)


def test_observed_ids_never_change_agreement_but_drive_fabricated_counts():
    human_vote = lambda sid, doc: True
    full = make_rows()
    empty = make_rows(observed=())
    human, supports = human_for(full, human_vote)
    a = evaluate(full, Grader(human_vote), human, supports)
    b = evaluate(empty, Grader(human_vote), human, supports)
    assert a["citation_agreement"] == b["citation_agreement"] == 1.0
    assert a["deterministic_citations"]["n_fabricated"] == 0
    # With no observed IDs every citation counts as fabricated (registered definition).
    assert b["deterministic_citations"]["n_fabricated"] == 3
    assert b["deterministic_citations"]["n_citations"] == 3


def test_ids_present_only_in_evidence_text_are_still_fabricated_by_definition():
    rows = make_rows(
        observed=("syn-guide-001",), evidence="본문에 INC-2026-004 SYN-1003"
    )
    human, supports = human_for(rows, lambda sid, doc: True)
    result = evaluate(rows, Grader(lambda sid, doc: True), human, supports)
    # Registered: body-embedded IDs are separate from observed IDs.
    assert result["deterministic_citations"]["n_fabricated"] == 2


def test_request_ids_are_excluded_from_fabricated():
    rows = make_rows(observed=())
    row = rows[0]
    request = "INC-2026-004 와 SYN-1003 요약"
    rows = sample.SampleRows(
        [
            sample.PoolRow(
                row.spec.model_copy(update={"request": request}),
                row.observation,
                row.evidence,
            )
        ]
    )
    human, supports = human_for(rows, lambda sid, doc: True)
    result = evaluate(rows, Grader(lambda sid, doc: True), human, supports)
    assert result["deterministic_citations"]["n_fabricated"] == 1


def test_detail_sink_records_ids_labels_codes_and_costs_only():
    rows = make_rows()
    human, supports = human_for(rows, lambda sid, doc: True)
    sink = []
    asyncio.run(
        cli._evaluate(
            rows,
            human,
            supports,
            grader=Grader(lambda sid, doc: True),
            version="v3.0",
            stage="calibration",
            repeats=1,
            detail_sink=sink,
        )
    )
    row = next(e for e in sink if e["kind"] == "row")
    assert row["status"] == "graded" and row["task_id"] == "T1-01"
    assert row["labels"] == {"s1": "supported", "s2": "supported", "s3": "supported"}
    assert ["s1", "syn-guide-001", True] in row["supports"]
    dumped = json.dumps(sink, ensure_ascii=False)
    assert "합성 보고입니다" not in dumped and "합성 근거" not in dumped

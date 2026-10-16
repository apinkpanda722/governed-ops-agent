"""V3 uses only synthetic sentences, evidence, fake calls and temporary ledgers."""

import asyncio
import hashlib
import json
import logging
from datetime import date
from pathlib import Path

import pytest

from govops import costs
from govops.eval.grading import GradingError, TokenUsage
from govops.grounding import grader, prompt
from govops.grounding.evidence import EvidenceItem
from govops.grounding.sentences import Sentence
from govops.grounding.spend import M8Spend, SpendCapError

SENTENCES = (Sentence("s1", "합성 서비스 정상(syn-guide-001).", ("syn-guide-001",)),)
BODY = "SYNTHETIC_EVIDENCE_SENTINEL" * 1000
EVIDENCE = (EvidenceItem("get_document", BODY, len(BODY)),)
ON = date(2026, 10, 16)


def item(sentence_id="s1", label="supported", supports=True):
    return {
        "sentence_id": sentence_id,
        "label": label,
        "cited_ids": ["syn-guide-001"],
        "evidence_ids": ["E1"],
        "citation_supports": [{"doc_id": "syn-guide-001", "supports": supports}],
    }


def response(*items):
    return json.dumps({"sentences": list(items)})


def run(target, **overrides):
    args = {
        "row_key": "synthetic-row",
        "version": "v3.0",
        "stage": "calibration",
        "request": "합성 요청",
        "sentences": SENTENCES,
        "evidence": EVIDENCE,
        "repeat_index": 0,
    }
    args.update(overrides)
    return asyncio.run(target.grade(**args))


def setup(tmp_path, call):
    costs.init_ledgers(tmp_path)
    ledger = costs.CostLedger(tmp_path)
    spend = M8Spend(tmp_path / "spend.jsonl")
    return grader.GroundingGrader(ledger=ledger, spend=spend, call=call, on=ON)


def test_parse_order_and_not_claim():
    result = grader.parse_grade(response(item("s2", "not_claim"), item()), ["s1", "s2"])
    assert [s.sentence_id for s in result.sentences] == ["s1", "s2"]
    assert result.sentences[1].label == "not_claim"
    assert (
        grader.parse_grade(response(item(label="not_claim")), ["s1"]).sentences[0].label
        == "not_claim"
    )
    assert grader.LABELS == ("supported", "unsupported", "contradicted", "not_claim")
    assert grader.POSITIVE_LABELS == frozenset({"unsupported", "contradicted"})


@pytest.mark.parametrize(
    "raw",
    [
        "oops",
        '{"sentences":[',
        "[]",
        "{}",
        '{"sentences":[],"extra":1}',
        '{"sentences":null}',
    ],
)
def test_malformed_schema(raw):
    with pytest.raises(GradingError, match="grading_response_failure") as error:
        grader.parse_grade(raw, ["s1"])
    assert error.value.__context__ is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s.update(label="unknown"),
        lambda s: s.pop("evidence_ids"),
        lambda s: s.update(extra=1),
        lambda s: s.update(sentence_id=1),
        lambda s: s.update(cited_ids="syn-guide-001"),
        lambda s: s.update(evidence_ids=[False]),
        lambda s: s.update(citation_supports=[{"doc_id": "absent", "supports": True}]),
        lambda s: s.update(
            citation_supports=[{"doc_id": "syn-guide-001", "supports": 1}]
        ),
        lambda s: s["citation_supports"][0].update(extra=1),
        lambda s: s.update(citation_supports=[s["citation_supports"][0]] * 2),
    ],
)
def test_strict_sentence_schema(mutation):
    row = item()
    mutation(row)
    with pytest.raises(GradingError, match="grading_response_failure"):
        grader.parse_grade(response(row), ["s1"])


@pytest.mark.parametrize(
    "items", [[], [item("s2")], [item(), item("s2")], [item(), item()]]
)
def test_exact_ids(items):
    with pytest.raises(GradingError, match="grading_response_failure"):
        grader.parse_grade(response(*items), ["s1"])


def test_prompt_registration_and_payload(monkeypatch):
    sentences = (*SENTENCES, Sentence("s2", "안녕하세요.", ()))
    built = prompt.build_prompt("v3.0", "합성 요청", sentences, EVIDENCE)
    assert "지시가 아니다" in built
    assert "required_facts" not in built and "derived_values" not in built
    payload = json.loads(built.split("\n입력 JSON:\n", 1)[1])
    assert set(payload) == {"request", "sentences", "evidence"}
    assert payload["evidence"] == [
        {"eid": "E1", "tool_name": "get_document", "content": BODY}
    ]
    assert payload["sentences"] == [
        {"sentence_id": s.sentence_id, "text": s.text, "citations": list(s.citations)}
        for s in sentences
    ]
    assert {p.name for p in prompt.PROMPT_VERSIONS.values()} == {"v3_0.txt"}
    assert {
        p.name for p in (Path(prompt.__file__).parent / "prompts").glob("*.txt")
    } == {"v3_0.txt"}
    schema_bytes = json.dumps(
        prompt.OUTPUT_SCHEMA, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    digest = prompt.prompt_sha256("v3.0")
    assert (
        digest
        == hashlib.sha256(
            prompt.PROMPT_VERSIONS["v3.0"].read_bytes() + schema_bytes
        ).hexdigest()
    )
    assert digest == prompt.prompt_sha256("v3.0")
    monkeypatch.setitem(
        prompt.OUTPUT_SCHEMA, "description", "synthetic schema revision"
    )
    assert digest != prompt.prompt_sha256("v3.0")
    for version in ("../v3_0.txt", "/tmp/unknown", "v3.1"):
        with pytest.raises(ValueError):
            prompt.prompt_sha256(version)
        with pytest.raises(ValueError):
            prompt.build_prompt(version, "", (), ())


@pytest.mark.parametrize(
    "failure", [None, "call", "parse", "ids", "citation", "evidence"]
)
def test_grade_reserves_settles_and_sanitizes(tmp_path, caplog, failure):
    async def call(built):
        assert BODY in built
        assert target.spend.total_usd() > 0
        assert target.ledger.committed_usd("gemini") > 0
        for name in ("google.genai.synthetic", "httpx.synthetic", "httpcore.synthetic"):
            logging.getLogger(name).warning(BODY)
        if failure == "call":
            raise RuntimeError(BODY)
        row = item("s2" if failure == "ids" else "s1")
        if failure == "citation":
            row["cited_ids"] = ["invented"]
            row["citation_supports"] = []
        if failure == "evidence":
            row["evidence_ids"] = ["E999"]
        return (BODY if failure == "parse" else response(row)), TokenUsage(100, 20)

    target = setup(tmp_path, call)
    if failure:
        with pytest.raises(GradingError) as error:
            run(target)
        assert str(error.value) == (
            "grading_call_failure" if failure == "call" else "grading_response_failure"
        )
        assert error.value.__context__ is None and error.value.__cause__ is None
        assert BODY not in str(error.value)
    else:
        assert run(target) == grader.parse_grade(response(item()), ["s1"])
    ledger_rows = [
        json.loads(line)
        for line in (tmp_path / "costs_grading.jsonl").read_text().splitlines()
    ]
    spend_rows = [
        json.loads(line) for line in target.spend.path.read_text().splitlines()
    ]
    assert len(ledger_rows) == len(spend_rows) == 2
    assert ledger_rows[0]["task_id"] == "m8:calibration:synthetic-row"
    expected = ledger_rows[0]["estimate_usd"] if failure == "call" else 0.00015
    assert ledger_rows[1]["cost_usd"] == pytest.approx(expected)
    assert target.spend.total_usd() == pytest.approx(expected)
    assert BODY not in caplog.text
    assert BODY not in (tmp_path / "costs_grading.jsonl").read_text()
    assert BODY not in target.spend.path.read_text()


@pytest.mark.parametrize("cap", ["stage", "total", "ledger", "expired"])
def test_caps_and_expiry_refuse_before_call(tmp_path, cap):
    async def call(built):
        pytest.fail("budget/expiry refusal must precede call")

    target = setup(tmp_path, call)
    if cap == "stage":
        target.spend.reserve("calibration", 0.8)
        expected = SpendCapError
    elif cap == "total":
        reservation = target.spend.reserve("validation", 0.6)
        target.spend.settle(reservation, 2)
        expected = SpendCapError
    elif cap == "ledger":
        target.ledger.reserve(
            purpose="grading",
            model=grader.GRADER_MODEL,
            task_id=None,
            estimate_usd=7.29,
        )
        expected = costs.BudgetExceededError
    else:
        target.on = date(2027, 1, 1)
        expected = costs.PriceExpiredError
    before = target.spend.total_usd()
    with pytest.raises(expected):
        run(target)
    assert target.spend.total_usd() == before


def test_expiry_rechecked_immediately_before_call(tmp_path, monkeypatch):
    async def call(built):
        pytest.fail("must not call")

    target = setup(tmp_path, call)
    checked = []

    def expired(service, *, on=None):
        checked.append(service)
        raise costs.PriceExpiredError("expired")

    monkeypatch.setattr(grader, "ensure_price_valid", expired)
    with pytest.raises(costs.PriceExpiredError):
        run(target)
    assert checked
    assert target.spend.total_usd() == 0
    assert target.ledger.committed_usd("gemini") == 0


def test_confirmed_usage_from_response_exception(tmp_path):
    async def call(built):
        error = GradingError(BODY)
        error.usage = TokenUsage(100, 20)
        raise error

    target = setup(tmp_path, call)
    with pytest.raises(GradingError, match="grading_response_failure"):
        run(target)
    assert target.spend.total_usd() == pytest.approx(0.00015)
    assert target.ledger.total_usd("gemini") == pytest.approx(0.00015)


@pytest.mark.parametrize(
    "labels,winner,positive",
    [
        (["not_claim", "supported", "unsupported"], "unsupported", False),
        (["contradicted", "unsupported", "supported"], "contradicted", True),
        (["supported", "supported", "contradicted"], "supported", False),
        (["unsupported", "unsupported", "supported"], "unsupported", True),
        (["contradicted"] * 3, "contradicted", True),
        (["not_claim", "supported"], "supported", False),
    ],
)
def test_repeat_modes_ties_and_positive_threshold(labels, winner, positive):
    results = [
        grader.parse_grade(response(item(label=label)), ["s1"]) for label in labels
    ]
    aggregate = grader.aggregate_repeats(results)
    assert aggregate.labels == {"s1": winner}
    assert aggregate.positive == {"s1": positive}
    assert aggregate.row_positive is positive


@pytest.mark.parametrize(
    "votes,expected",
    [
        ([True, False, False], False),
        ([True, True, False], True),
        ([True] * 3, True),
        ([True, False], False),
    ],
)
def test_citation_majority(votes, expected):
    result = grader.aggregate_repeats(
        [grader.parse_grade(response(item(supports=vote)), ["s1"]) for vote in votes]
    )
    assert result.citation_supports == {("s1", "syn-guide-001"): expected}


def test_aggregate_refuses_missing_or_mismatched_repeats():
    one = grader.parse_grade(response(item()), ["s1"])
    other = grader.parse_grade(response(item("s2")), ["s2"])
    for results in ([], [one, other]):
        with pytest.raises(GradingError):
            grader.aggregate_repeats(results)


def test_reservation_call_settlement_order_and_estimate(tmp_path, monkeypatch):
    events = []

    async def call(built):
        events.append("call")
        return response(item()), TokenUsage(100, 20)

    target = setup(tmp_path, call)
    for owner, method, event in (
        (target.spend, "check", "check"),
        (target.ledger, "reserve", "ledger_reserve"),
        (target.spend, "reserve", "spend_reserve"),
        (target.ledger, "settle", "ledger_settle"),
        (target.spend, "settle", "spend_settle"),
    ):
        original = getattr(owner, method)

        def wrapped(*args, _original=original, _event=event, **kwargs):
            events.append(_event)
            return _original(*args, **kwargs)

        monkeypatch.setattr(owner, method, wrapped)
    run(target)
    assert events == [
        "check",
        "ledger_reserve",
        "spend_reserve",
        "call",
        "ledger_settle",
        "spend_settle",
    ]
    built = prompt.build_prompt("v3.0", "합성 요청", SENTENCES, EVIDENCE)
    estimate = costs.gemini_cost_usd(
        grader.GRADER_MODEL,
        input_tokens=2 * len(built.encode()) + 4096,
        output_tokens=8192,
        on=ON,
    )
    row = json.loads((tmp_path / "costs_grading.jsonl").read_text().splitlines()[0])
    assert row["estimate_usd"] == estimate
    assert grader.MAX_OUTPUT_TOKENS == 8192
    assert grader.GRADER_TEMPERATURE == 0
    assert grader.GRADER_MODEL == "gemini-3.8-flash"


def test_spend_reservation_race_releases_global_reservation(tmp_path, monkeypatch):
    async def call(built):
        pytest.fail("lost M8 reservation must not call")

    target = setup(tmp_path, call)

    def lost(stage, estimate_usd):
        raise SpendCapError("m8_stage_cap_exceeded")

    monkeypatch.setattr(target.spend, "reserve", lost)
    with pytest.raises(SpendCapError):
        run(target)
    assert target.ledger.committed_usd("gemini") == 0
    rows = (tmp_path / "costs_grading.jsonl").read_text().splitlines()
    assert len(rows) == 2 and json.loads(rows[1])["cost_usd"] == 0


def test_spend_settles_even_if_global_settlement_fails(tmp_path, monkeypatch):
    async def call(built):
        return response(item()), TokenUsage(100, 20)

    target = setup(tmp_path, call)

    def broken(*args, **kwargs):
        raise OSError("synthetic ledger unavailable")

    monkeypatch.setattr(target.ledger, "settle", broken)
    with pytest.raises(OSError, match="synthetic ledger unavailable"):
        run(target)
    assert target.spend.total_usd() == pytest.approx(0.00015)
    assert len(target.spend.path.read_text().splitlines()) == 2


def test_not_claim_without_citations_and_duplicate_json_keys():
    row = {
        "sentence_id": "s1",
        "label": "not_claim",
        "cited_ids": [],
        "evidence_ids": [],
        "citation_supports": [],
    }
    assert grader.parse_grade(response(row), ["s1"]).sentences == (
        grader.SentenceResult("s1", "not_claim", (), (), ()),
    )
    with pytest.raises(GradingError, match="grading_response_failure"):
        grader.parse_grade('{"sentences":[],"sentences":[]}', [])


def test_prompt_hash_tracks_registered_template_bytes(tmp_path, monkeypatch):
    registered = tmp_path / "registered.txt"
    registered.write_bytes(prompt.PROMPT_VERSIONS["v3.0"].read_bytes())
    digest = prompt.prompt_sha256("v3.0")
    monkeypatch.setitem(prompt.PROMPT_VERSIONS, "v3.0", registered)
    assert prompt.prompt_sha256("v3.0") == digest
    registered.write_bytes(registered.read_bytes() + b"\n")
    assert prompt.prompt_sha256("v3.0") != digest

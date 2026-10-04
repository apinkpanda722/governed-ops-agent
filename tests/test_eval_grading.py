import asyncio
import json
from datetime import date

import pytest
from test_eval_tcr import spec

from govops import costs
from govops.eval import grading


def ledger(tmp_path):
    costs.init_ledgers(tmp_path)
    return costs.CostLedger(tmp_path)


def test_prompt():
    s = spec()
    prompt = grading.build_prompt(s, "ANSWER_SENTINEL")
    assert s.request in prompt
    assert "ANSWER_SENTINEL" in prompt
    assert "BODY_SENTINEL" not in prompt
    for f in s.required_facts:
        assert f.value in prompt and f.fact_id in prompt
    assert grading.prompt_sha256(s, "a") == grading.prompt_sha256(s, "a")
    assert grading.GRADER_MODEL == "gemini-3.8-flash"
    assert grading.GRADER_REPEATS == 3
    assert grading.GRADER_TEMPERATURE == 0
    assert grading.RUBRIC_VERSION == "v1"


@pytest.mark.parametrize(
    "raw",
    [
        "oops",
        '{"correctness":4,"groundedness":1,"format":1}',
        '{"correctness":true,"groundedness":1,"format":1}',
        '{"correctness":1}',
        '{"correctness":1,"groundedness":1,"format":1,"rationale":"' + "x" * 201 + '"}',
    ],
)
def test_bad_scores(raw):
    with pytest.raises(grading.GradingError):
        grading.parse_score(raw)


def test_valid_score():
    assert grading.parse_score(
        '{"correctness":1,"groundedness":2,"format":3}'
    ) == grading.RubricScore(1, 2, 3)


@pytest.mark.parametrize("failure", [False, True])
def test_reserve_settle_and_safe_failure(tmp_path, caplog, failure):
    target = ledger(tmp_path)

    async def call(prompt):
        rows = (tmp_path / "costs_grading.jsonl").read_text().splitlines()
        assert len(rows) == 1 and json.loads(rows[0])["kind"] == "reservation"
        import logging

        logging.getLogger("google.genai.synthetic").warning("SECRET ANSWER_SENTINEL")
        if failure:
            raise RuntimeError("SECRET ANSWER_SENTINEL")
        return grading.RubricScore(1, 2, 3), grading.TokenUsage(100, 20)

    grader = grading.GeminiRubricGrader(ledger=target, call=call, on=date(2026, 12, 31))
    if failure:
        with pytest.raises(grading.GradingError) as error:
            asyncio.run(
                grader.grade(spec(), "ANSWER_SENTINEL", task_id="T1-01", repeat_index=0)
            )
        assert "SECRET" not in str(error.value) and error.value.__context__ is None
    else:
        assert (
            asyncio.run(
                grader.grade(spec(), "ANSWER_SENTINEL", task_id="T1-01", repeat_index=0)
            ).format
            == 3
        )
    rows = [
        json.loads(x)
        for x in (tmp_path / "costs_grading.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 2 and rows[1]["purpose"] == "grading"
    assert rows[1]["cost_usd"] == (0 if failure else pytest.approx(0.00015))
    assert not (tmp_path / "costs.jsonl").read_text()
    assert "ANSWER_SENTINEL" not in caplog.text


def test_budget_and_expiry_no_call(tmp_path, monkeypatch):
    target = ledger(tmp_path)

    async def call(prompt):
        pytest.fail("must not call")

    grader = grading.GeminiRubricGrader(ledger=target, call=call, on=date(2026, 12, 31))
    monkeypatch.setattr(costs, "SUPPLIER_CAPS_USD", {"gemini": 0})
    with pytest.raises(costs.BudgetExceededError):
        asyncio.run(grader.grade(spec(), "a", task_id="T1-01", repeat_index=0))
    grader.on = date(2027, 1, 1)
    with pytest.raises(costs.PriceExpiredError):
        asyncio.run(grader.grade(spec(), "a", task_id="T1-01", repeat_index=0))
    assert (tmp_path / "costs_grading.jsonl").read_text() == ""


def test_google_sdk_import_is_confined():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src/govops"
    found = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import) and any(
                a.name == "google.genai" or a.name.startswith("google.genai.")
                for a in node.names
            ):
                found.add(path.relative_to(root).as_posix())
            if isinstance(node, ast.ImportFrom) and (
                node.module == "google.genai"
                or (node.module or "").startswith("google.genai.")
                or (
                    node.module == "google"
                    and any(a.name == "genai" for a in node.names)
                )
            ):
                found.add(path.relative_to(root).as_posix())
    assert found == {"eval/grading.py"}


def test_parse_failure_keeps_confirmed_usage(tmp_path):
    target = ledger(tmp_path)

    async def call(prompt):
        error = grading.GradingError("SECRET_RESPONSE")
        error.usage = grading.TokenUsage(100, 20)
        raise error

    with pytest.raises(grading.GradingError) as error:
        asyncio.run(
            grading.GeminiRubricGrader(
                ledger=target, call=call, on=date(2026, 12, 31)
            ).grade(spec(), "a", task_id="T1-01", repeat_index=0)
        )
    assert "SECRET_RESPONSE" not in str(error.value)
    assert target.total_usd("gemini") == pytest.approx(0.00015)


def test_expiry_rechecked_before_reservation(tmp_path, monkeypatch):
    target = ledger(tmp_path)

    async def call(prompt):
        pytest.fail("expired reservation must never call")

    def expired(service, *, on=None):
        raise costs.PriceExpiredError("price expired")

    monkeypatch.setattr(grading, "ensure_price_valid", expired)
    grader = grading.GeminiRubricGrader(ledger=target, call=call, on=date(2026, 12, 31))
    with pytest.raises(costs.PriceExpiredError):
        asyncio.run(grader.grade(spec(), "a", task_id="T1-01", repeat_index=0))
    assert (tmp_path / "costs_grading.jsonl").read_text() == ""


def test_no_permission_rubric_asks_not_to_invent_or_guess_hidden_records():
    from govops.eval.grading import NO_PERMISSION_CRITERIA, build_prompt, criteria_for

    hidden = spec(subtype="no_permission")
    prompt = json.loads(build_prompt(hidden, "찾을 수 없습니다."))
    assert prompt["criteria"]["groundedness"] == NO_PERMISSION_CRITERIA["groundedness"]
    assert "지어내거나 추측하지 않았는가" in prompt["criteria"]["groundedness"]
    assert "볼 수 없는 것을 볼 수 없다고 했는가" in prompt["criteria"]["groundedness"]
    assert "찾을 수 없다고 답했거나" in prompt["criteria"]["correctness"]
    other = spec()
    assert other.subtype != "no_permission"
    assert criteria_for(other)["correctness"] == "필수 사실과 일치하는가"
    assert json.loads(build_prompt(other, "x"))["criteria"] == criteria_for(other)
    assert criteria_for(hidden)["format"] == criteria_for(other)["format"]

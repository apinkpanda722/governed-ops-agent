"""전체 기간 비용 상한과 비밀 없는 분리 원장."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from govops import costs


def record(ledger, purpose="agent_primary", amount=0.1):
    ledger.record(
        purpose=purpose,
        model="synthetic-model",
        task_id="task-1",
        input_tokens=10,
        cached_input_tokens=2,
        output_tokens=5,
        cost_usd=amount,
    )


def test_prices_and_constants(monkeypatch):
    monkeypatch.setenv("OPENAI_BUDGET", "999")
    assert dict(costs.SUPPLIER_CAPS_USD) == {
        "openai": 9.13,
        "anthropic": 11.47,
        "gemini": 10.0,
    }
    assert costs.PURPOSES == (
        "agent_primary",
        "agent_fallback",
        "guardrail_v2",
        "grading",
    )
    assert costs.token_cost_usd(
        "gpt-6-luna",
        input_tokens=1000000,
        cached_input_tokens=200000,
        output_tokens=1000000,
    ) == pytest.approx(0.582)
    assert costs.estimate_call_usd(
        "gpt-6-luna", input_tokens_upper=1000000, max_output_tokens=1000000
    ) == pytest.approx(0.6)
    assert len(costs.PRICES) == 10
    for suffix, amount in [
        ("input", 0.1),
        ("cached_input", 0.01),
        ("cache_write", 0.125),
        ("output", 0.5),
    ]:
        price = costs.PRICES[f"openai.gpt-6-luna.{suffix}"]
        assert price.unit_price_usd == amount / 1000000
        assert price.checked_on == "2026-09-30"
        assert price.source_url == "https://developers.openai.com/api/docs/pricing"
    for suffix, amount in [("input", 0.10), ("output", 0.50)]:
        price = costs.PRICES[f"anthropic.claude-haiku-5-5.{suffix}"]
        assert price.unit_price_usd == amount / 1_000_000
        assert price.checked_on == "2026-10-13"
        assert (
            price.source_url
            == "https://platform.claude.com/docs/en/about-claude/pricing"
        )


def test_missing_and_init(tmp_path):
    ledger = costs.CostLedger(tmp_path)
    with pytest.raises(costs.LedgerMissingError):
        ledger.check_before_call(purpose="agent_primary", estimate_usd=0)
    with pytest.raises(costs.LedgerMissingError):
        record(ledger)
    assert list(tmp_path.iterdir()) == []
    assert set(costs.init_ledgers(tmp_path)) == {
        tmp_path / "costs.jsonl",
        tmp_path / "costs_grading.jsonl",
    }
    record(ledger)
    before = (tmp_path / "costs.jsonl").read_bytes()
    assert costs.init_ledgers(tmp_path) == []
    assert (tmp_path / "costs.jsonl").read_bytes() == before
    (tmp_path / "costs_grading.jsonl").unlink()
    assert costs.init_ledgers(tmp_path) == [tmp_path / "costs_grading.jsonl"]
    assert (tmp_path / "costs.jsonl").read_bytes() == before


def test_caps_and_separate_ledgers(tmp_path):
    costs.init_ledgers(tmp_path)
    ledger = costs.CostLedger(tmp_path)
    record(ledger, amount=9.03)
    ledger.check_before_call(purpose="agent_primary", estimate_usd=0.1)
    with pytest.raises(costs.BudgetExceededError):
        ledger.check_before_call(purpose="agent_primary", estimate_usd=0.1001)
    ledger.check_before_call(purpose="agent_fallback", estimate_usd=11.47)
    for purpose in costs.PURPOSES[1:]:
        record(ledger, purpose, 0.2)
    assert ledger.total_usd("openai") == 9.03
    assert ledger.total_usd("anthropic") == 0.4
    assert ledger.total_usd("gemini") == 0.2
    main = [
        json.loads(line) for line in (tmp_path / "costs.jsonl").read_text().splitlines()
    ]
    grading = [
        json.loads(line)
        for line in (tmp_path / "costs_grading.jsonl").read_text().splitlines()
    ]
    assert [row["purpose"] for row in main] == list(costs.PURPOSES[:3])
    assert [row["purpose"] for row in grading] == ["grading"]
    assert set(main[0]) == {
        "ts",
        "purpose",
        "supplier",
        "model",
        "task_id",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "cost_usd",
    }


@pytest.mark.parametrize("bad", ["broken", "{}", '{"purpose":"unknown"}'])
def test_corrupt_ledger(tmp_path, bad):
    costs.init_ledgers(tmp_path)
    (tmp_path / "costs.jsonl").write_text(bad + "\n")
    with pytest.raises(ValueError):
        costs.CostLedger(tmp_path).total_usd("openai")


def test_concurrent_records(tmp_path):
    costs.init_ledgers(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: record(costs.CostLedger(tmp_path), amount=0.01), range(100)
            )
        )
    assert len((tmp_path / "costs.jsonl").read_text().splitlines()) == 100
    assert costs.CostLedger(tmp_path).total_usd("openai") == 1


def test_main(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(costs, "_default_root", lambda: tmp_path)
    assert costs.main(["init"]) == 0
    assert str(tmp_path / "costs.jsonl") in capsys.readouterr().out
    record(costs.CostLedger(tmp_path))
    before = (tmp_path / "costs.jsonl").read_bytes()
    assert costs.main(["init"]) == 0
    assert "이미 있음" in capsys.readouterr().out
    assert (tmp_path / "costs.jsonl").read_bytes() == before


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf")])
def test_invalid_amount(tmp_path, amount):
    costs.init_ledgers(tmp_path)
    with pytest.raises(ValueError):
        record(costs.CostLedger(tmp_path), amount=amount)


@pytest.mark.parametrize("purpose", costs.PURPOSES)
def test_exact_supplier_cap(tmp_path, purpose):
    costs.init_ledgers(tmp_path)
    ledger = costs.CostLedger(tmp_path)
    cap = costs.SUPPLIER_CAPS_USD[costs.PURPOSE_SUPPLIER[purpose]]
    record(ledger, purpose, cap)
    ledger.check_before_call(purpose=purpose, estimate_usd=0)
    with pytest.raises(costs.BudgetExceededError):
        ledger.check_before_call(purpose=purpose, estimate_usd=0.001)


@pytest.mark.parametrize(
    "field,value",
    [
        ("purpose", "unknown"),
        ("purpose", "grading"),
        ("supplier", "gemini"),
        ("cost_usd", -1),
        ("input_tokens", "10"),
    ],
)
def test_invalid_complete_row(tmp_path, field, value):
    costs.init_ledgers(tmp_path)
    ledger = costs.CostLedger(tmp_path)
    record(ledger)
    path = tmp_path / "costs.jsonl"
    row = json.loads(path.read_text())
    row[field] = value
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError):
        ledger.check_before_call(purpose="agent_primary", estimate_usd=0)


@pytest.mark.parametrize("missing", ["costs.jsonl", "costs_grading.jsonl"])
def test_partial_missing_fails_closed(tmp_path, missing):
    costs.init_ledgers(tmp_path)
    (tmp_path / missing).unlink()
    ledger = costs.CostLedger(tmp_path)
    with pytest.raises(costs.LedgerMissingError):
        record(ledger)
    with pytest.raises(costs.LedgerMissingError):
        ledger.check_before_call(purpose="grading", estimate_usd=0)
    assert not (tmp_path / missing).exists()


def test_gemini_prices_and_expiry():
    from datetime import date

    assert costs.gemini_cost_usd(
        "gemini-3.8-flash",
        input_tokens=1000000,
        output_tokens=1000000,
        on=date(2026, 12, 31),
    ) == pytest.approx(4.5)
    with pytest.raises(costs.PriceExpiredError):
        costs.gemini_cost_usd(
            "gemini-3.8-flash", input_tokens=0, output_tokens=0, on=date(2027, 1, 1)
        )
    for bad in [-1, 1.5, True, "1"]:
        with pytest.raises(ValueError):
            costs.gemini_cost_usd("gemini-3.8-flash", input_tokens=bad, output_tokens=0)
    for suffix in ["input", "output"]:
        p = costs.PRICES[f"google.gemini-3.8-flash.{suffix}"]
        assert p.valid_until == "2026-12-31" and p.checked_on == "2026-10-01"
        assert p.source_url == "https://ai.google.dev/gemini-api/docs/pricing"


@pytest.mark.parametrize("cached", [0, 200000, 1000000])
def test_anthropic_prices_and_cost(cached):
    for suffix, amount in [("input", 2), ("output", 10)]:
        price = costs.PRICES[f"anthropic.claude-sonnet-5-5.{suffix}"]
        assert price.unit_price_usd == amount / 1000000
        assert (
            price.source_url
            == "https://platform.claude.com/docs/en/about-claude/pricing"
        )
        assert price.checked_on == "2026-10-05"
        assert price.valid_until is None
    assert "anthropic.claude-sonnet-5-5.cached_input" not in costs.PRICES
    assert costs.anthropic_cost_usd(
        "claude-sonnet-5-5",
        input_tokens=1000000,
        cached_input_tokens=cached,
        output_tokens=1000000,
    ) == pytest.approx(12)


@pytest.mark.parametrize(
    "model,supplier",
    [
        ("gpt-6-luna", "openai"),
        ("claude-sonnet-5-5", "anthropic"),
    ],
)
@pytest.mark.parametrize("suffix", ["", "-20261005", "-2026-10-05"])
def test_resolve_registered_model(model, supplier, suffix):
    resolved = costs.resolve_model(model + suffix)
    assert resolved == costs.ResolvedModel(supplier, model, f"{supplier}.{model}", True)
    amount, actual = costs.response_cost_usd(
        model + suffix,
        input_tokens=1000000,
        cached_input_tokens=200000,
        output_tokens=1000000,
    )
    assert actual == resolved
    assert amount == pytest.approx(0.582 if supplier == "openai" else 12)


@pytest.mark.parametrize(
    "model",
    [
        "",
        "GPT-6-LUNA",
        "Claude-sonnet-5-5",
        "gpt-6",
        "claude-sonnet-5",
        "gpt-6-luna-preview",
        "claude-sonnet-5-5-latest",
        "gpt-6-luna-20261005-extra",
        "prefix-gpt-6-luna",
        "anthropic/claude-sonnet-5-5",
        "gpt-6-luna-2026105",
        "gpt-6-luna-2026-1-05",
        "gpt-6-luna\n",
    ],
)
def test_unmapped_response_uses_maximum_registered_cost(model):
    assert costs.resolve_model(model) == costs.ResolvedModel(None, None, None, False)
    amount, resolved = costs.response_cost_usd(
        model,
        input_tokens=1000000,
        cached_input_tokens=200000,
        output_tokens=1000000,
    )
    assert not resolved.mapped
    assert amount == max(
        costs.token_cost_usd(
            "gpt-6-luna",
            input_tokens=1000000,
            cached_input_tokens=200000,
            output_tokens=1000000,
        ),
        costs.anthropic_cost_usd(
            "claude-sonnet-5-5",
            input_tokens=1000000,
            cached_input_tokens=200000,
            output_tokens=1000000,
        ),
    )


@pytest.mark.parametrize("counts", [(-1, 0, 0), (1, 2, 0), (True, 0, 0), (1, 0, 1.5)])
def test_anthropic_and_response_reject_invalid_tokens(counts):
    for calculate, model in [
        (costs.anthropic_cost_usd, "claude-sonnet-5-5"),
        (costs.response_cost_usd, "unknown"),
    ]:
        with pytest.raises(ValueError):
            calculate(
                model,
                input_tokens=counts[0],
                cached_input_tokens=counts[1],
                output_tokens=counts[2],
            )

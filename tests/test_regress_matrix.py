"""M8 matrix and smoke contracts; temporary ledgers and synthetic artifacts only."""

import ast
import hashlib
import importlib.util
import json
import logging
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from govops.costs import SUPPLIER_CAPS_USD, CostLedger, init_ledgers
from govops.gateway.client import ChatResult, GatewayError, Usage
from govops.gateway.hook_digest import HookDigests
from govops.gateway.hook_header import GuardrailInfo, format_header
from govops.golden import golden_expectations
from govops.regress import compare, smoke

ROOT = Path(__file__).resolve().parents[1]
REGISTRATION = ROOT / "docs/results/hypotheses-m8-regression.md"
spec = importlib.util.spec_from_file_location(
    "synthetic_regress_compare", Path(__file__).with_name("test_regress_compare.py")
)
assert spec is not None and spec.loader is not None
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)
build_synthetic_run = synthetic.build_synthetic_run
KEY = "sentinel-matrix-key"
BODY = "SYN_SMOKE_RESPONSE_PRIVATE"
DIGESTS = HookDigests("a" * 12, "b" * 12)


@pytest.mark.parametrize("role", ["candidate", "control"])
@pytest.mark.parametrize(
    "failures",
    [
        (),
        ("tcr_drop",),
        ("forbidden_violation",),
        ("toolcall_required_drop",),
        ("toolcall_ok_drop",),
        ("golden_structure",),
        ("cost_over",),
        ("latency_over",),
        ("invalid_run",),
        ("tcr_drop", "cost_over"),
    ],
)
def test_matrix_uses_compare_main(tmp_path, monkeypatch, capsys, role, failures):
    baseline = build_synthetic_run(tmp_path, name=compare.BASELINE_RUN_ID)
    # Only synthetic registration identity is injected; parsing, comparison,
    # reduction and report writing all run through the production entrypoint.
    monkeypatch.setattr(compare, "_registration_commit", lambda root: "a" * 40)
    monkeypatch.setattr(
        compare,
        "BASELINE_MANIFEST_SHA256",
        hashlib.sha256((baseline / "manifest.json").read_bytes()).hexdigest(),
    )
    manifest, observations, grades, spans = {}, {}, {}, {}
    tasks = synthetic.TASKS
    if "tcr_drop" in failures:
        for task in tasks[:3]:
            grades[task] = {
                "tcr": {
                    "task_id": task,
                    "run_index": 0,
                    "passed": False,
                    "reasons": [],
                    "tool_coverage": True,
                }
            }
    if "forbidden_violation" in failures:
        grades[tasks[0]] = {
            "tcr": {
                "task_id": tasks[0],
                "run_index": 0,
                "passed": True,
                "reasons": ["policy_violation"],
                "tool_coverage": True,
            }
        }
    if "toolcall_required_drop" in failures:
        # Keep all calls ok; remove required names without changing structure.
        for task in tasks[:3]:
            observations[task] = {
                "tool_calls": [{"name": "get_document", "ok": True}] * 3
            }
    if "toolcall_ok_drop" in failures:
        # Required lookup remains successful; 3/52 failures exceed 5%.
        for task in tasks[:3]:
            observations[task] = {
                "tool_calls": [
                    {"name": "lookup_id", "ok": True},
                    {"name": "get_document", "ok": False},
                    {"name": "search_docs", "ok": True},
                ]
            }
    if "golden_structure" in failures:
        observations["T3-07"] = {"effects": []}
    if "cost_over" in failures:
        manifest["costs_by_purpose"] = {
            "agent_primary": 0,
            "agent_fallback": 0.406462,
            "guardrail_v2": 0,
            "grading": 0,
        }
    if "invalid_run" in failures:
        manifest["infrastructure_failures"] = 1
    expected = compare.EXPECTED[role]
    # Slight control latency variation remains below the registered ceiling.
    for task in tasks:
        spans[task] = [
            {
                "kind": "task",
                "name": "task",
                "parent_id": None,
                "duration_ms": 20001 if "latency_over" in failures else 13833,
            },
            {
                "kind": "model_call",
                "attributes": {
                    "deployment_id": expected.deployment_id,
                    "attempted_fallbacks": 0,
                },
            },
        ]
    candidate = build_synthetic_run(
        tmp_path,
        name="comparison",
        role=role,
        manifest=manifest,
        observations=observations,
        grades=grades,
        spans=spans,
    )
    specs = synthetic.synthetic_specs()
    spec_path, expectation_path = tmp_path / "specs.json", tmp_path / "golden.json"
    synthetic.write_json(spec_path, [s.model_dump() for s in specs.values()])
    synthetic.write_json(expectation_path, golden_expectations(specs.values()))
    out = tmp_path / "verdict"
    assert (
        compare.main(
            [
                "compare",
                "--role",
                role,
                "--baseline",
                str(baseline),
                "--candidate",
                str(candidate),
                "--out",
                str(out),
                "--specs",
                str(spec_path),
                "--expectations",
                str(expectation_path),
            ],
            repo_root=tmp_path,
        )
        == 0
    )
    verdict = json.loads((out / "verdict.json").read_text())
    reasons = list(failures)
    if "invalid_run" in failures:
        reasons.append("invalid_infrastructure_failure")
    assert set(verdict["reasons"]) == set(reasons)
    assert verdict["verdict"] == ("FAIL" if failures else "PASS")
    assert verdict["role"] == role and set(verdict["criteria"]) == set("123456")
    assert verdict["behavior_criteria_met"] is (not set(failures) - {"cost_over"})
    assert verdict["cost_criterion_met"] is ("cost_over" not in failures)
    for path in out.iterdir():
        assert not any(marker in path.read_text() for marker in synthetic.SENTINELS)
    assert not any(marker in capsys.readouterr().out for marker in synthetic.SENTINELS)


@pytest.fixture
def ledger(tmp_path):
    init_ledgers(tmp_path / "ledger")
    return CostLedger(tmp_path / "ledger")


def charge(ledger, supplier, amount):
    ledger.record(
        purpose="agent_primary" if supplier == "openai" else "agent_fallback",
        model="gpt-6-luna" if supplier == "openai" else "claude-sonnet-5-5",
        task_id="synthetic",
        input_tokens=1,
        output_tokens=0,
        cost_usd=float(amount),
    )


@pytest.mark.parametrize("stage", ["smoke", "candidate", "control"])
@pytest.mark.parametrize("supplier", ["openai", "anthropic"])
@pytest.mark.parametrize("shortfall", [Decimal(0), Decimal("0.01")])
def test_precheck_boundaries(ledger, stage, supplier, shortfall):
    committed = (
        Decimal(str(SUPPLIER_CAPS_USD[supplier]))
        - smoke.STAGE_NEEDS[stage][supplier]
        + shortfall
    )
    # Pending reservations count too, before any settlement exists.
    ledger.reserve(
        purpose="agent_primary" if supplier == "openai" else "agent_fallback",
        model="gpt-6-luna" if supplier == "openai" else "claude-sonnet-5-5",
        task_id="synthetic",
        estimate_usd=float(committed),
    )
    assert smoke.precheck(stage, ledger=ledger) == (
        [f"m8_regress_stage_cap_exceeded supplier={supplier}"] if shortfall else []
    )


def test_precheck_offline_and_unreadable(ledger, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("os.environ", {})
    assert smoke.precheck("candidate", ledger=ledger) == []
    assert (
        smoke.main(
            ["precheck", "--stage", "control", "--ledger-root", str(ledger.root)]
        )
        == 0
    )
    assert capsys.readouterr().out == (
        "PASS precheck stage=control openai_headroom=9.130000 anthropic_headroom=11.470000\n"
        "SUMMARY pass=1 fail=0 skip=0\n"
    )
    assert smoke.precheck("smoke", ledger=CostLedger(tmp_path / "absent")) == [
        "m8_regress_ledger_unreadable"
    ]
    (ledger.root / "costs.jsonl").write_text("broken synthetic ledger\n")
    assert smoke.main(["precheck", "--stage", "smoke"], ledger=ledger) == 1
    assert "FAIL m8_regress_ledger_unreadable" in capsys.readouterr().out


def test_candidate_digest_tampering(ledger, tmp_path, monkeypatch, capsys):
    path = tmp_path / "candidate.yaml"
    path.write_text("synthetic tampering\n")
    monkeypatch.setattr(smoke, "CANDIDATE_CONFIG_PATH", path)
    assert smoke.precheck("candidate", ledger=ledger) == [
        "m8_regress_candidate_config_mismatch"
    ]
    assert smoke.precheck("control", ledger=ledger) == []
    assert smoke.main(["precheck", "--stage", "candidate"], ledger=ledger) == 1
    assert "FAIL m8_regress_candidate_config_mismatch" in capsys.readouterr().out


def result_for(stage):
    expected = compare.EXPECTED[stage]
    return ChatResult(
        message={"content": BODY},
        model=expected.model,
        usage=Usage(10, 0, 2),
        cost_usd=0.001,
        fallback_used=expected.fallback_used,
        deployment_id=expected.deployment_id,
        body_model="agent-primary",
        model_group="agent-primary",
        attempted_fallbacks=0,
    )


@pytest.mark.parametrize("stage", ["candidate", "control"])
@pytest.mark.parametrize(
    "fault", [None, "route", "unmapped", "charged", "zero", "missing_id", "attempts"]
)
def test_smoke_signals_are_independent(stage, fault):
    result = result_for(stage)
    before = {"openai": Decimal("0.1"), "anthropic": Decimal("0.2")}
    after = dict(before)
    charged, zero = (
        ("anthropic", "openai") if stage == "candidate" else ("openai", "anthropic")
    )
    after[charged] += Decimal("0.001")
    if fault == "route":
        result = replace(result, deployment_id="wrong-deployment")
    elif fault == "missing_id":
        result = replace(result, deployment_id=None)
    elif fault == "attempts":
        result = replace(result, attempted_fallbacks=1)
    elif fault == "unmapped":
        result = replace(result, model="unregistered", unmapped_model=True)
    elif fault == "charged":
        after[charged] = before[charged]
    elif fault == "zero":
        after[zero] += Decimal("0.001")
    checks = smoke.evaluate_smoke(result, before, after, stage=stage)
    assert len(checks) == 4
    expected_code = {
        "route": "m8_regress_route_mismatch",
        "missing_id": "m8_regress_route_mismatch",
        "attempts": "m8_regress_route_mismatch",
        "unmapped": "m8_regress_unmapped_model",
        "charged": "m8_regress_ledger_direction_mismatch",
        "zero": "m8_regress_ledger_direction_mismatch",
    }.get(fault)
    assert [c.code for c in checks if not c.passed] == (
        [expected_code] if fault else []
    )
    assert sum(c.passed for c in checks) == (3 if fault else 4)


@pytest.mark.parametrize("stage", ["candidate", "control"])
@pytest.mark.parametrize("flag,model", [(True, "gpt-6-luna"), (False, "unregistered")])
def test_unmapped_flags_and_resolution_both_required(stage, flag, model):
    result = replace(result_for(stage), unmapped_model=flag, model=model)
    checks = smoke.evaluate_smoke(
        result,
        {"openai": 0, "anthropic": 0},
        {"openai": 1, "anthropic": 1},
        stage=stage,
    )
    assert [c.code for c in checks if not c.passed] == [
        "m8_regress_unmapped_model",
        "m8_regress_ledger_direction_mismatch",
    ]


@pytest.fixture
def smoke_env(monkeypatch):
    monkeypatch.setenv("SMOKE_USER", "demo-platform-g3")
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", KEY)
    monkeypatch.setenv("LITELLM_BASE_URL", "http://synthetic.invalid")
    monkeypatch.setattr(smoke, "compute_digests", lambda root: DIGESTS)


@pytest.mark.parametrize("failure", ["cap", "ledger", "config", "env"])
def test_preflight_prevents_client_and_ledger_writes(
    ledger, tmp_path, monkeypatch, capsys, smoke_env, failure
):
    if failure == "cap":
        charge(ledger, "anthropic", 11.02)  # only .45, smoke needs .46
    elif failure == "ledger":
        ledger = CostLedger(tmp_path / "missing")
    elif failure == "config":
        monkeypatch.setattr(smoke, "CANDIDATE_CONFIG_PATH", tmp_path / "missing.yaml")
    else:
        monkeypatch.delenv("LITELLM_TEAM_KEY_PLATFORM")
    before = {p.name: p.read_bytes() for p in ledger.root.glob("*.jsonl")}
    factory = Mock(side_effect=AssertionError("must not construct a client"))
    assert smoke.main(
        ["smoke", "--stage", "candidate"], ledger=ledger, client_factory=factory
    ) == (2 if failure == "env" else 1)
    factory.assert_not_called()
    assert {p.name: p.read_bytes() for p in ledger.root.glob("*.jsonl")} == before
    out = capsys.readouterr().out
    assert "SKIP" not in out and out.endswith("SUMMARY pass=0 fail=1 skip=0\n")
    if failure == "env":
        assert "FAIL m8_regress_env_unset missing=1" in out
        assert "KeyError" not in out and "LITELLM_TEAM_KEY" not in out


@pytest.mark.parametrize("stage", ["candidate", "control"])
@pytest.mark.parametrize("attempts", [None, 0])
def test_smoke_real_client_fake_transport(
    ledger, capsys, caplog, smoke_env, stage, attempts
):
    caplog.set_level(logging.DEBUG)
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert body["max_completion_tokens"] == 16 and body["tools"] == []
        assert re.fullmatch(r"[0-9a-f]{32}", body["metadata"]["task_id"])
        assert body["messages"] == [{"role": "user", "content": smoke.MESSAGE}]
        assert request.extensions["timeout"]["read"] > 0
        return httpx.Response(
            200,
            headers={
                "x-govops-guardrail": format_header(
                    GuardrailInfo(DIGESTS.rules, DIGESTS.code, team="platform")
                ),
                "x-litellm-model-id": compare.EXPECTED[stage].deployment_id,
                "x-litellm-model-group": "agent-primary",
                **(
                    {"x-litellm-attempted-fallbacks": str(attempts)}
                    if attempts is not None
                    else {}
                ),
            },
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "agent-primary",  # alias is deliberately not a model ID
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": BODY}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    assert (
        smoke.main(
            ["smoke", "--stage", stage],
            ledger=ledger,
            transport=httpx.MockTransport(handle),
        )
        == 0
    )
    assert len(seen) == 1
    captured = capsys.readouterr()
    out = captured.out
    assert out.endswith("SUMMARY pass=4 fail=0 skip=0\n") and "SKIP" not in out
    assert f"model_id={compare.EXPECTED[stage].deployment_id}" in out
    assert f"attempted_fallbacks={attempts}" in out
    for secret in (KEY, "Authorization", BODY, smoke.MESSAGE):
        assert secret not in out + captured.err + caplog.text
    charged, zero = (
        ("anthropic", "openai") if stage == "candidate" else ("openai", "anthropic")
    )
    assert ledger.total_usd(charged) > 0 and ledger.total_usd(zero) == 0


@pytest.mark.parametrize(
    "error",
    [
        GatewayError(f"Authorization {KEY} {BODY}", status_code=403),
        GatewayError(BODY, timeout=True),
        httpx.ReadTimeout(BODY),
        RuntimeError(BODY),
    ],
)
def test_call_errors_are_safe(ledger, smoke_env, capsys, error):
    class Client:
        async def complete(self, **kwargs):
            raise error

    factory = Mock(return_value=Client())
    assert (
        smoke.main(
            ["smoke", "--stage", "control"], ledger=ledger, client_factory=factory
        )
        == 1
    )
    factory.assert_called_once()
    assert factory.call_args.kwargs["require_guardrail"] is True
    assert factory.call_args.kwargs["expected_digests"] == DIGESTS
    assert factory.call_args.kwargs["key_resolver"]("demo-platform-g3") == KEY
    out = capsys.readouterr().out
    code = (
        "m8_regress_smoke_call_timeout"
        if isinstance(error, httpx.TimeoutException) or getattr(error, "timeout", False)
        else "m8_regress_smoke_call_failed"
    )
    assert f"FAIL {code} error={type(error).__name__}" in out
    if getattr(error, "status_code", None) == 403:
        assert "status=403" in out
    assert out.endswith("SUMMARY pass=0 fail=1 skip=0\n") and "SKIP" not in out
    assert not any(s in out for s in (KEY, BODY, "Authorization"))


def test_diagnostic_identifiers_redact_secrets(ledger, smoke_env, capsys):
    class Client:
        async def complete(self, **kwargs):
            charge(ledger, "openai", 0.001)
            return replace(
                result_for("control"),
                deployment_id=KEY,
                model_group="Authorization-secret-token",
            )

    assert (
        smoke.main(
            ["smoke", "--stage", "control"],
            ledger=ledger,
            client_factory=lambda **kw: Client(),
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "model_id=redacted model_group=redacted" in out
    assert KEY not in out and "Authorization" not in out and BODY not in out
    assert out.endswith("SUMMARY pass=3 fail=1 skip=0\n")


@pytest.mark.parametrize("mode", ["unmapped", "missing_guardrail", "http_error"])
def test_transport_failures_and_conservative_settlement(
    ledger, smoke_env, capsys, caplog, mode
):
    caplog.set_level(logging.DEBUG)
    seen = []

    def handle(request):
        seen.append(request.method)
        if mode == "http_error":
            return httpx.Response(403, text=f"Authorization {KEY} {BODY}")
        return httpx.Response(
            200,
            headers={
                "x-litellm-model-id": "unregistered"
                if mode == "unmapped"
                else "openai-gpt-6-luna",
                **(
                    {
                        "x-govops-guardrail": format_header(
                            GuardrailInfo(DIGESTS.rules, DIGESTS.code, team="platform")
                        )
                    }
                    if mode != "missing_guardrail"
                    else {}
                ),
            },
            json={
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "unregistered" if mode == "unmapped" else "agent-primary",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": BODY}}
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    assert (
        smoke.main(
            ["smoke", "--stage", "control"],
            ledger=ledger,
            transport=httpx.MockTransport(handle),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert seen == ["POST"]
    out = captured.out
    if mode == "unmapped":
        assert ledger.total_usd("openai") > 0 and ledger.total_usd("anthropic") > 0
        assert "FAIL m8_regress_unmapped_model" in out
        assert "FAIL m8_regress_ledger_direction_mismatch signal=anthropic_zero" in out
        assert out.endswith("SUMMARY pass=1 fail=3 skip=0\n")
    else:
        assert "FAIL m8_regress_smoke_call_failed" in out
        assert out.endswith("SUMMARY pass=0 fail=1 skip=0\n")
    assert "SKIP" not in out
    assert not any(
        s in out + captured.err + caplog.text for s in (KEY, BODY, "Authorization")
    )


def test_frozen_config_stage_needs_and_code_union():
    source = Path(smoke.__file__).read_text()
    assert (
        hashlib.sha256(
            (ROOT / "infra/litellm/config.candidate.yaml").read_bytes()
        ).hexdigest()
        == smoke.CANDIDATE_CONFIG_SHA256
    )
    constants = ast.parse((ROOT / "tests/test_m8_candidate_config.py").read_text())
    assignment = next(
        n
        for n in constants.body
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "CANDIDATE_CONFIG_SHA256"
            for t in n.targets
        )
    )
    assert ast.literal_eval(assignment.value) == smoke.CANDIDATE_CONFIG_SHA256
    tree = ast.parse(source)
    assert not any(
        isinstance(n, ast.Subscript)
        and isinstance(n.value, ast.Attribute)
        and isinstance(n.value.value, ast.Name)
        and n.value.value.id == "os"
        and n.value.attr == "environ"
        for n in ast.walk(tree)
    )
    registration = REGISTRATION.read_text()
    needs_table = registration.split(
        "| 단계 | OpenAI 필요 잔여액 | Anthropic 필요 잔여액 |", 1
    )[1].split("검사식", 1)[0]
    parsed = {}
    for line in needs_table.splitlines():
        cells = line.split("|")[1:-1]
        if len(cells) == 3 and cells[0].strip() in {"smoke", "후보", "통제"}:
            stage = {"smoke": "smoke", "후보": "candidate", "통제": "control"}[
                cells[0].strip()
            ]
            parsed[stage] = {
                supplier: Decimal(re.findall(r"\$([0-9.]+)", cell)[-1])
                for supplier, cell in zip(
                    ("openai", "anthropic"), cells[1:], strict=True
                )
            }
    assert smoke.STAGE_NEEDS == parsed
    codes = set(
        re.findall(
            r"`(m8_regress_[a-z_]+)`",
            registration.split("## 10.", 1)[1].split("## 11.", 1)[0],
        )
    )
    impl = set(
        re.findall(r"\bm8_regress_[a-z_]+", source + Path(compare.__file__).read_text())
    )
    assert len(codes) == 17 and impl == codes
    assert "budget_or_price_block" not in source

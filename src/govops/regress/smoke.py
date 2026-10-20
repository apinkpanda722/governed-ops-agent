"""User-run M8 cost precheck and one-call routing smoke; no response bodies emitted."""

import argparse
import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx

from govops.costs import SUPPLIER_CAPS_USD, CostLedger, resolve_model
from govops.env import env_or
from govops.gateway.client import ChatResult, GatewayError, LiteLLMChatClient
from govops.gateway.hook_digest import compute_digests
from govops.gateway.smoke import _identifier
from govops.gateway.team_keys import TeamKeyResolver
from govops.regress.compare import EXPECTED

ROOT = Path(__file__).resolve().parents[3]
CANDIDATE_CONFIG_PATH = ROOT / "infra/litellm/config.candidate.yaml"
CANDIDATE_CONFIG_SHA256 = (
    "4947362f07a38ca8f86bfab42621fa78fc6a1a8b75855f13bb832b2fc6deaeff"
)
STAGE_NEEDS: dict[str, dict[str, Decimal]] = {
    "smoke": {"openai": Decimal("0.02"), "anthropic": Decimal("0.46")},
    "candidate": {"openai": Decimal("0.02"), "anthropic": Decimal("1.86")},
    "control": {"openai": Decimal("0.12"), "anthropic": Decimal("0.36")},
}
MESSAGE = "합성 상태 확인: 정상이라고 짧게 답하세요."
CALL_TIMEOUT = 60.0


@dataclass(frozen=True)
class Check:
    signal: str
    code: str
    passed: bool


def _headrooms(ledger: CostLedger) -> dict[str, Decimal]:
    return {
        supplier: Decimal(str(SUPPLIER_CAPS_USD[supplier]))
        - Decimal(str(ledger.committed_usd(supplier)))
        for supplier in STAGE_NEEDS["smoke"]
    }


def _candidate_check() -> list[str]:
    try:
        if hashlib.sha256(CANDIDATE_CONFIG_PATH.read_bytes()).hexdigest() == (
            CANDIDATE_CONFIG_SHA256
        ):
            return []
    except OSError:
        pass
    return ["m8_regress_candidate_config_mismatch"]


def precheck(stage: str, *, ledger: CostLedger | None) -> list[str]:
    """Read only committed amounts, including outstanding reservations; no env."""
    needs = STAGE_NEEDS[stage]
    failures = _candidate_check() if stage == "candidate" else []
    try:
        headrooms = _headrooms(ledger if ledger is not None else CostLedger())
        failures.extend(
            f"m8_regress_stage_cap_exceeded supplier={supplier}"
            for supplier, need in needs.items()
            if headrooms[supplier] < need
        )
    except Exception:  # noqa: BLE001 — ledger errors may contain private paths
        failures.append("m8_regress_ledger_unreadable")
    return failures


def evaluate_smoke(
    result: ChatResult, before: Mapping, after: Mapping, *, stage: str
) -> list[Check]:
    """Four independent signals; model bodies never substitute for deployment IDs."""
    expected = EXPECTED[stage]
    charged, zero = (
        ("anthropic", "openai") if stage == "candidate" else ("openai", "anthropic")
    )
    delta = {
        supplier: Decimal(str(after[supplier])) - Decimal(str(before[supplier]))
        for supplier in (charged, zero)
    }
    return [
        Check(
            "route",
            "m8_regress_route_mismatch",
            result.deployment_id == expected.deployment_id
            and result.attempted_fallbacks in (0, None),
        ),
        Check(
            "mapped_model",
            "m8_regress_unmapped_model",
            not result.unmapped_model and resolve_model(result.model).mapped,
        ),
        Check(
            f"{charged}_increase",
            "m8_regress_ledger_direction_mismatch",
            delta[charged] > 0,
        ),
        Check(
            f"{zero}_zero",
            "m8_regress_ledger_direction_mismatch",
            delta[zero] == 0,
        ),
    ]


def _summary(passed: int, failed: int) -> None:
    print(f"SUMMARY pass={passed} fail={failed} skip=0")


def _failures(codes: list[str]) -> int:
    for code in codes:
        print(f"FAIL {code}")
    _summary(0, len(codes))
    return 1


def _call_failure(exc: Exception) -> int:
    timeout = isinstance(exc, (TimeoutError, httpx.TimeoutException)) or (
        isinstance(exc, GatewayError) and exc.timeout
    )
    code = (
        "m8_regress_smoke_call_timeout" if timeout else "m8_regress_smoke_call_failed"
    )
    status = getattr(exc, "status_code", None)
    # Arbitrary exception text and response content are never interpolated.
    safe_status = (
        str(status) if type(status) is int and 100 <= status <= 599 else "None"
    )
    print(f"FAIL {code} error={_identifier(type(exc).__name__)} status={safe_status}")
    _summary(0, 1)
    return 1


def _totals(ledger: CostLedger) -> dict[str, Decimal]:
    return {
        supplier: Decimal(str(ledger.total_usd(supplier)))
        for supplier in STAGE_NEEDS["smoke"]
    }


async def _smoke(stage, ledger, client_factory, transport) -> int:
    try:
        users = {
            row["label"]: row["team"]
            for row in json.loads((ROOT / "data/seed/users.json").read_bytes())["users"]
        }
        user = env_or("SMOKE_USER", "demo-platform-g3")
        resolver = TeamKeyResolver(users)
        missing = int(user not in users or users[user] in resolver.missing())
    except Exception as exc:  # noqa: BLE001 — safe CLI boundary
        return _call_failure(exc)
    if missing:
        print(f"FAIL m8_regress_env_unset missing={missing}")
        _summary(0, 1)
        return 2

    failures = precheck("smoke", ledger=ledger)
    if stage == "candidate":
        failures.extend(_candidate_check())
    if failures:
        return _failures(failures)
    try:
        before = _totals(ledger)
    except Exception:  # noqa: BLE001 — suppress raw ledger errors
        return _failures(["m8_regress_ledger_unreadable"])
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=CALL_TIMEOUT, trust_env=False
        ) as http:
            client = client_factory(
                ledger=ledger,
                key_resolver=resolver.key_for,
                expected_digests=compute_digests(ROOT),
                require_guardrail=True,
                http_client=http,
                base_url=env_or("LITELLM_BASE_URL", "http://127.0.0.1:4000"),
                timeout_s=CALL_TIMEOUT,
            )
            result = await asyncio.wait_for(
                client.complete(
                    messages=[{"role": "user", "content": MESSAGE}],
                    tools=[],
                    task_id=uuid4().hex,
                    user_id=user,
                    max_output_tokens=16,
                ),
                timeout=CALL_TIMEOUT + 30,
            )
    except Exception as exc:  # noqa: BLE001 — only class and status may leave CLI
        return _call_failure(exc)
    try:
        after = _totals(ledger)
        checks = evaluate_smoke(result, before, after, stage=stage)
    except Exception:  # noqa: BLE001 — unreadable deltas fail closed
        return _failures(["m8_regress_ledger_unreadable"])
    total = sum(after[s] - before[s] for s in after)
    print(
        f"INFO model_id={_identifier(result.deployment_id)} "
        f"model_group={_identifier(result.model_group)} "
        f"attempted_fallbacks={_identifier(str(result.attempted_fallbacks))} "
        f"resolved={_identifier(result.model)} "
        f"unmapped={str(result.unmapped_model).lower()} "
        f"cost_total_cents={total * 100:.6f}"
    )
    for check in checks:
        print(
            f"PASS {check.signal}"
            if check.passed
            else f"FAIL {check.code} signal={check.signal}"
        )
    failed = sum(not check.passed for check in checks)
    _summary(len(checks) - failed, failed)
    return int(failed > 0)


def main(argv=None, *, client_factory=None, ledger=None, transport=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command, stages in (
        ("precheck", tuple(STAGE_NEEDS)),
        ("smoke", tuple(EXPECTED)),
    ):
        entry = sub.add_parser(command)
        entry.add_argument("--stage", choices=stages, required=True)
        entry.add_argument("--ledger-root", type=Path)
    args = parser.parse_args(argv)
    ledger = ledger if ledger is not None else CostLedger(args.ledger_root)
    if args.command == "smoke":
        return asyncio.run(
            _smoke(args.stage, ledger, client_factory or LiteLLMChatClient, transport)
        )
    failures = precheck(args.stage, ledger=ledger)
    if failures:
        return _failures(failures)
    try:
        headrooms = _headrooms(ledger)
    except Exception:  # noqa: BLE001 — ledger can disappear after precheck
        return _failures(["m8_regress_ledger_unreadable"])
    print(
        f"PASS precheck stage={args.stage} "
        f"openai_headroom={headrooms['openai']:.6f} "
        f"anthropic_headroom={headrooms['anthropic']:.6f}"
    )
    _summary(1, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

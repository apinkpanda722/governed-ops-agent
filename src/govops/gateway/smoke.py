"""사용자 실행용 LiteLLM fallback 스모크; 출력에는 판정·비용만 남긴다."""

import argparse
import asyncio
import os
import re
from uuid import uuid4

import httpx

from govops.costs import CostLedger, cost_report, resolve_model
from govops.gateway.client import GatewayError, LiteLLMChatClient
from govops.gateway.faults import ModelFaultScript

MESSAGE = "합성 상태 확인: 정상이라고 짧게 답하세요."


def _status(exc: Exception) -> str:
    # GatewayError가 만든 고정 진단만 해석한다. 임의 예외 메시지는 출력하지 않는다.
    if isinstance(exc, GatewayError):
        match = re.fullmatch(r"gateway HTTP ([1-5][0-9]{2})", str(exc))
        if match:
            return match[1]
        return {
            "gateway timeout": "timeout",
            "gateway connection failure": "connection",
            "gateway malformed response": "malformed",
            "gateway SDK failure": "sdk",
        }.get(str(exc), "error")
    return "error"


def _fail(stage: str, exc: Exception, reason: str = "http") -> None:
    print(
        f"FAIL {stage} reason={reason} error={type(exc).__name__} status={_status(exc)}"
    )


def _identifier(value: str | None) -> str:
    if value is None:
        return "None"
    key = os.getenv("LITELLM_AGENT_KEY", "").strip()
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", value)
        or re.search(
            r"authorization|bearer|secret|token|api_key|sk-", value, re.IGNORECASE
        )
        or (key and key in value)
    ):
        return "redacted"
    return value


def _totals(ledger: CostLedger) -> dict[str, float]:
    return {row.purpose: row.settled_usd for row in cost_report(ledger.root)}


async def _fallback(
    ledger: CostLedger, transport: httpx.AsyncBaseTransport | None
) -> int:
    try:
        _totals(ledger)
    except Exception as exc:  # noqa: BLE001 — CLI must suppress secret-bearing errors
        _fail("ledger", exc, "ledger_delta")
        print("BACKEND=none")
        return 1
    if not os.getenv("LITELLM_AGENT_KEY", "").strip():
        print("FAIL normal reason=missing_key error=GatewayError status=missing_key")
        print("BACKEND=none")
        return 1
    passed = {}
    total = 0.0
    async with httpx.AsyncClient(transport=transport) as http:
        for stage in ("normal", "down_alias"):
            client = LiteLLMChatClient(
                ledger=ledger,
                http_client=http,
                fault_mode=None if stage == "normal" else stage,
                fault_script=None if stage == "normal" else ModelFaultScript("all"),
            )
            passed[stage] = False
            result = None
            error = None
            try:
                before = _totals(ledger)
            except Exception as exc:  # noqa: BLE001
                _fail(stage, exc, "ledger_delta")
                continue
            try:
                result = await client.complete(
                    messages=[{"role": "user", "content": MESSAGE}],
                    tools=[],
                    task_id=uuid4().hex,
                    user_id="demo-platform-g3",
                    max_output_tokens=16,
                )
            except Exception as exc:  # noqa: BLE001 — suppress secret-bearing errors
                error = exc
            try:
                after = _totals(ledger)
                delta = {purpose: after[purpose] - before[purpose] for purpose in after}
                stage_cost = sum(delta.values())
                total += stage_cost
                print(f"INFO {stage} cost total_cents={stage_cost * 100:.6f}")
            except Exception as exc:  # noqa: BLE001
                _fail(stage, exc, "ledger_delta")
                continue
            if result is None:
                _fail(stage, error)
                print(
                    f"INFO {stage} body_model=None model_id=None model_group=None "
                    "attempted_fallbacks=None resolved=None unmapped=false"
                )
                continue
            print(
                f"INFO {stage} body_model={_identifier(result.body_model)} "
                f"model_id={_identifier(result.deployment_id)} "
                f"model_group={_identifier(result.model_group)} "
                f"attempted_fallbacks={result.attempted_fallbacks} "
                f"resolved={_identifier(result.model)} "
                f"unmapped={str(result.unmapped_model).lower()}"
            )
            resolved = resolve_model(result.model)
            expected = "openai" if stage == "normal" else "anthropic"
            attempts = result.attempted_fallbacks
            reason = None
            if result.unmapped_model or not resolved.mapped:
                reason = "unmapped"
            elif resolved.supplier != expected or (
                attempts is not None
                and (attempts != 0 if stage == "normal" else attempts < 1)
            ):
                reason = "model_mismatch"
            else:
                charged, zero = (
                    ("agent_primary", "agent_fallback")
                    if stage == "normal"
                    else ("agent_fallback", "agent_primary")
                )
                if not (delta[charged] > 0 and delta[zero] == 0):
                    reason = "ledger_delta"
            if reason:
                error_class = (
                    "LedgerCheckError"
                    if reason == "ledger_delta"
                    else "ModelCheckError"
                )
                print(
                    f"FAIL {stage} reason={reason} error={error_class} status={reason}"
                )
            else:
                print(f"PASS {stage} family={expected} mapped=true")
                passed[stage] = True
                if stage == "down_alias":
                    print("PASS ledger agent_fallback_delta>0")
    print(f"INFO cost total_cents={total * 100:.6f}")
    success = all(passed.values())
    print("BACKEND=down_alias" if success else "BACKEND=none")
    return 0 if success else 1


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    ledger: CostLedger | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="사용자 실행용 소액 fallback 확인")
    parser.add_argument("command", choices=["fallback"])
    parser.parse_args(argv)
    return asyncio.run(
        _fallback(ledger if ledger is not None else CostLedger(), transport)
    )


if __name__ == "__main__":
    raise SystemExit(main())

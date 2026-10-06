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


def _fail(stage: str, exc: Exception) -> None:
    print(f"FAIL {stage} error={type(exc).__name__} status={_status(exc)}")


def _totals(ledger: CostLedger) -> dict[str, float]:
    return {row.purpose: row.settled_usd for row in cost_report(ledger.root)}


async def _fallback(
    ledger: CostLedger, transport: httpx.AsyncBaseTransport | None
) -> int:
    try:
        before = _totals(ledger)
    except Exception as exc:  # noqa: BLE001 — CLI must suppress secret-bearing errors
        _fail("ledger", exc)
        print("BACKEND=none")
        return 1
    if not os.getenv("LITELLM_AGENT_KEY", "").strip():
        print("FAIL normal error=GatewayError status=missing_key")
        print("BACKEND=none")
        return 1
    passed = {}
    async with httpx.AsyncClient(transport=transport) as http:
        for stage in ("normal", "down_alias"):
            client = LiteLLMChatClient(
                ledger=ledger,
                http_client=http,
                fault_mode=None if stage == "normal" else stage,
                fault_script=None if stage == "normal" else ModelFaultScript("all"),
            )
            passed[stage] = False
            try:
                result = await client.complete(
                    messages=[{"role": "user", "content": MESSAGE}],
                    tools=[],
                    task_id=uuid4().hex,
                    user_id="demo-platform-g3",
                    max_output_tokens=16,
                )
                resolved = resolve_model(result.model)
                expected = "openai" if stage == "normal" else "anthropic"
                if resolved.mapped and resolved.supplier == expected:
                    print(f"PASS {stage} family={expected} mapped=true")
                    passed[stage] = True
                else:
                    print(f"FAIL {stage} error=ModelCheckError status=model_mismatch")
            except Exception as exc:  # noqa: BLE001 — CLI must suppress secret-bearing errors
                _fail(stage, exc)
    ledger_ok = False
    try:
        after = _totals(ledger)
        ledger_ok = after["agent_fallback"] - before["agent_fallback"] > 0
        if ledger_ok:
            print("PASS ledger agent_fallback_delta>0")
        else:
            print("FAIL ledger error=LedgerCheckError status=no_increase")
        total = sum(after[p] - before[p] for p in after)
        print(f"INFO cost total_cents={total * 100:.6f}")
    except Exception as exc:  # noqa: BLE001 — CLI must suppress secret-bearing errors
        _fail("ledger", exc)
    backend = "down_alias" if passed["down_alias"] else "none"
    print(f"BACKEND={backend}")
    return 0 if passed["normal"] and ledger_ok and backend != "none" else 1


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

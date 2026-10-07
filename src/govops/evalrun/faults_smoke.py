"""User-run live component smoke. Verification injects all external adapters."""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import psycopg
from langgraph.checkpoint.memory import InMemorySaver

from govops.api.wiring import build_task_service
from govops.costs import (
    BudgetExceededError,
    CostLedger,
    anthropic_cost_usd,
    estimate_call_usd,
    resolve_model,
)
from govops.env import env_or
from govops.evalrun.dev_m3 import PROFILE, new_tool_policy
from govops.evalrun.faults import AsyncAdmin, Evidence
from govops.evalrun.mock_admin import MockAdmin, subprocess_runner
from govops.evalrun.preflight import run_preflight
from govops.gateway.client import ChatResult, LiteLLMChatClient, Usage
from govops.gateway.faults import ModelFaultScript
from govops.obs.trace import configure
from govops.tools.jira import JiraClient
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient

ROOT = Path(__file__).resolve().parents[3]
USER = "demo-platform-g3"
MESSAGE = "합성 상태 확인: 짧게 답하세요."
READ_RULE = [
    {
        "endpoint": "slack.channels",
        "nth": "all",
        "action": {"type": "delay", "when": "before", "ms": 20},
    }
]
WRITE_RULES = [
    {"endpoint": "slack.send", "nth": [1], "action": {"type": "drop_after_apply"}},
    {"endpoint": "slack.send", "nth": [1], "action": {"type": "hide_for", "ms": 1000}},
]
SAFE_REASONS = {"stale_mock_image", "litellm_config_stale"} | {
    "service_down:" + name
    for name in ("postgres", "litellm", "mock-jira", "mock-slack", "a-mcp")
}


def guidance(duration):
    print(
        "INFO 비용 원장(`data/manifest/costs.jsonl`)이 변경되었으면 `chore:` 커밋으로 먼저 올린 뒤 `make eval-faults`를 실행하세요(FP-031)"
    )
    print(
        "INFO caffeinate -i make eval-faults; 약 75~80분; down_alias 측정값으로 소요 예상을 다시 계산하세요"
    )
    if duration is not None:
        # F6 first/second/all = 4/3/3 tasks. Actual call counts vary by task.
        print(
            f"INFO down_alias_measured_s={duration:.3f}; F6 호출 횟수 × 측정 초를 실행 예상에 반영하세요"
        )


async def measure_gateway(gateway, ledger, *, clock=time.monotonic):
    try:
        # Conservative upper bound for this fixed 16-token request (including
        # gateway metadata/framing). The client still reserves and settles.
        upper = 8192
        ledger.check_before_call(
            purpose="agent_primary",
            estimate_usd=estimate_call_usd(
                "gpt-6-luna", input_tokens_upper=upper, max_output_tokens=16
            ),
        )
        ledger.check_before_call(
            purpose="agent_fallback",
            estimate_usd=anthropic_cost_usd(
                "claude-sonnet-5-5", input_tokens=upper, output_tokens=16
            ),
        )
        start = clock()
        result = await gateway.complete(
            messages=[{"role": "user", "content": MESSAGE}],
            tools=[],
            task_id="0" * 32,
            user_id=USER,
            max_output_tokens=16,
        )
        duration = clock() - start
        # Only the registered, measured deployment may reach stdout.
        model_id = result.deployment_id
        if model_id != "anthropic-claude-sonnet-5-5":
            model_id = "redacted"
        attempts = result.attempted_fallbacks
        safe_attempts = attempts if type(attempts) is int and attempts >= 0 else None
        print(
            f"INFO duration_s={duration:.3f} model_id={model_id} attempted_fallbacks={safe_attempts}"
        )
        if (
            result.unmapped_model
            or resolve_model(result.model).supplier != "anthropic"
            or model_id == "redacted"
            or safe_attempts is None
            or safe_attempts < 1
        ):
            print("FAIL down_alias reason=model_mismatch")
            return None
        print("PASS down_alias")
        return duration
    except BudgetExceededError:
        print("FAIL down_alias reason=budget_exceeded")
    except Exception:  # noqa: BLE001 — never print transport/ledger details
        print("FAIL down_alias reason=call_failed")
    return None


async def run_smoke(
    *, admin, preflight, read, effect, gateway, ledger, clock=time.monotonic
):
    duration = None
    stage = "environment"
    print("INFO environment 남은 규칙은 reset으로 제거함(목록 조회 불가)")
    try:
        failures = await preflight()
        if failures:
            for code in failures:
                print(
                    "FAIL environment reason="
                    + (code if code in SAFE_REASONS else "preflight_failed")
                )
            return 1
        await admin.reset()
        if await admin.faults_stats() != {"jira": {}, "slack": {}}:
            print("FAIL environment reason=reset_stats")
            return 1
        print("PASS environment")
        stage = "rules"
        try:
            await admin.faults_set("mock-slack", READ_RULE)
            await read()
            counters = (await admin.faults_stats())["slack"].get("slack.channels", {})
            if counters.get("requests", 0) < 1 or counters.get("applied_rules", 0) < 1:
                print("FAIL rules reason=not_applied")
                return 1
            await admin.faults_clear()
            if await admin.faults_stats() != {"jira": {}, "slack": {}}:
                print("FAIL rules reason=clear_stats")
                return 1
            await read()
            counters = (await admin.faults_stats())["slack"].get("slack.channels", {})
            if (
                counters.get("requests", 0) < 1
                or counters.get("applied_rules", -1) != 0
            ):
                print("FAIL rules reason=still_applied")
                return 1
        finally:
            await admin.reset()
        print("PASS rules")
        stage = "f5_f10"
        try:
            await admin.faults_set("mock-slack", WRITE_RULES)
            evidence = await effect()
            ok = (
                evidence["succeeded"]
                and evidence["lookups"] >= 2
                and evidence["wait_seconds"] >= 1
                and evidence["resends"] == 0
                and evidence["effects"] == 1
                and evidence["injections"] >= 2
            )
            print(("PASS" if ok else "FAIL") + " f5_f10")
            if not ok:
                return 1
        finally:
            await admin.reset()
        stage = "down_alias"
        duration = await measure_gateway(gateway, ledger, clock=clock)
        return 0 if duration is not None else 1
    except Exception:  # noqa: BLE001 — safe fixed stage codes only
        print(f"FAIL {stage} reason=operation_failed")
        return 1
    finally:
        guidance(duration)


class SyntheticDraft:
    """Fixed synthetic draft, no paid model call in the effect component test."""

    async def complete(self, **kwargs):
        return ChatResult(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "syn-smoke-call",
                        "name": "slack_send_message",
                        "arguments": json.dumps(
                            {
                                "channel": "team-platform",
                                "text": "합성 장애 부품 확인 메시지",
                            }
                        ),
                    }
                ],
            },
            "synthetic",
            Usage(0, 0, 0),
            0,
        )


async def effect_probe(admin, slack):
    evidence = Evidence()
    policy = new_tool_policy()

    outbox = InMemoryOutboxStore()
    with TemporaryDirectory(prefix="govops-faults-smoke-") as directory:
        service = build_task_service(
            chat=SyntheticDraft(),
            tools=ToolRuntime(
                None,
                JiraClient(env_or("MOCK_JIRA_URL", "http://127.0.0.1:8081")),
                slack,
                policy=policy,
            ),
            saver=InMemorySaver(),
            outbox=outbox,
            profile=PROFILE,
            audit_dir=Path(directory),
        )
        configure([evidence])
        try:
            async with asyncio.timeout(30):
                task = await service.start(
                    USER, "합성 장애 부품 확인 메시지를 자기 팀 채널에 보내세요."
                )
                while True:
                    view = await service.get(USER, task)
                    if view["status"] == "awaiting_approval":
                        break
                    if view["status"] in {"failed", "completed"}:
                        raise ValueError("draft_failed")
                    await asyncio.sleep(0.01)
                await service.approve(
                    USER, task, decision="approve", draft_sha256=view["draft_sha256"]
                )
                while True:
                    view = await service.get(USER, task)
                    if view["status"] in {"failed", "completed"}:
                        break
                    await asyncio.sleep(0.01)
            stats = (await admin.faults_stats())["slack"].get("slack.send", {})
            state = await admin.state()
            count = sum(
                m["metadata"].get("govops_task") == task
                for c in state["slack"]["channels"]
                for m in c["messages"]
            )
            return {
                "succeeded": view["status"] == "completed",
                "lookups": evidence.lookups,
                "wait_seconds": evidence.wait_seconds,
                "resends": max(0, stats.get("requests", 0) - 1),
                "effects": count,
                "injections": stats.get("applied_rules", 0),
            }
        finally:
            await service.aclose()
            configure([])


async def live():
    admin = MockAdmin(ROOT)
    async_admin = AsyncAdmin(admin)

    def runner(command, *, cwd):
        if command[0] != "postgres":
            return subprocess_runner(command, cwd=cwd)
        try:
            with psycopg.connect(
                env_or("GOVOPS_DATABASE_URL", ""), connect_timeout=10
            ) as connection:
                connection.execute("SELECT 1")
            return subprocess.CompletedProcess(command, 0, "")
        except Exception:  # noqa: BLE001 — connection details never printed
            return subprocess.CompletedProcess(command, 1, "")

    def check():
        with httpx.Client(trust_env=False) as http:
            # A is not used by this component smoke. Its live contract is checked
            # by eval-faults preflight before the official run.
            return run_preflight(
                root=ROOT,
                runner=runner,
                http=http,
                mock_admin=admin,
                a_check=lambda: True,
            )

    async with httpx.AsyncClient(trust_env=False) as http:
        slack = SlackClient(
            env_or("MOCK_SLACK_URL", "http://127.0.0.1:8082"), http_client=http
        )
        ledger = CostLedger()
        gateway = LiteLLMChatClient(
            ledger=ledger,
            http_client=http,
            fault_mode="down_alias",
            fault_script=ModelFaultScript("all"),
        )
        return await run_smoke(
            admin=async_admin,
            preflight=lambda: asyncio.to_thread(check),
            read=lambda: slack.list_channels(USER),
            effect=lambda: effect_probe(async_admin, slack),
            gateway=gateway,
            ledger=ledger,
        )


def main():
    try:
        return asyncio.run(live())
    except Exception:  # noqa: BLE001 — CLI suppresses secret-bearing exceptions
        print("FAIL environment reason=initialization_failed")
        guidance(None)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

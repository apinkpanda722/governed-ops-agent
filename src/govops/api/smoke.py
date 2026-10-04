"""사용자 실행용 live 스모크. 기대값은 실행 전에 고정한다."""

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from govops.agent.checkpointer import open_checkpointer
from govops.api.tasks import TaskService
from govops.api.wiring import open_task_service
from govops.costs import CostLedger

EXPECTED_DOC = "syn-incident-022"
OPENAI_CAP = 9.13
REQUEST = "INC-2026-012 장애를 요약해 주세요. 근거로 쓴 문서 ID를 본문에 표기해 주세요."


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    lines: list[str]


class _State(TypedDict):
    value: str


async def smoke_checkpointer(url: str) -> SmokeResult:
    if not url.strip() or url == "memory":
        return SmokeResult(False, ["GOVOPS_DATABASE_URL 필요"])
    try:
        async with open_checkpointer(url) as saver:
            thread = uuid4().hex
            config = {"configurable": {"thread_id": thread}}
            try:
                builder = StateGraph(_State)
                builder.add_node(
                    "roundtrip", lambda state: {"value": "smoke-roundtrip"}
                )
                builder.add_edge(START, "roundtrip")
                builder.add_edge("roundtrip", END)
                graph = builder.compile(checkpointer=saver)
                await graph.ainvoke({"value": "initial"}, config)
                snapshot = await graph.aget_state(config)
                ok = snapshot.values.get("value") == "smoke-roundtrip"
            finally:
                await saver.adelete_thread(thread)
        return SmokeResult(
            ok, ["checkpointer 왕복 일치" if ok else "checkpointer 값 불일치"]
        )
    except Exception:  # noqa: BLE001 — never print connection-bearing exceptions
        return SmokeResult(False, ["checkpointer 왕복·삭제 실패"])


def _primary_rows(ledger: CostLedger) -> list[dict]:
    # total_usd validates both ledgers before inspecting structured rows.
    ledger.total_usd("openai")
    with (ledger.root / "costs.jsonl").open(encoding="utf-8") as source:
        return [
            row
            for line in source
            if (row := json.loads(line))["purpose"] == "agent_primary"
        ]


async def smoke_agent(
    service: TaskService,
    ledger: CostLedger,
    *,
    users: Sequence[str] = ("demo-platform-g3", "demo-finance-g3"),
    request: str = REQUEST,
    poll_interval: float = 1.0,
    timeout_s: float = 180.0,
) -> SmokeResult:
    lines = []
    failures = []
    try:
        before = _primary_rows(ledger)
        tasks = [(user, await service.start(user, request)) for user in users]
        deadline = asyncio.get_running_loop().time() + timeout_s
        for user, task_id in tasks:
            while True:
                state = await service.get(user, task_id) or {}
                if state.get("status") in ("completed", "failed"):
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    break
                await asyncio.sleep(poll_interval)
            lines.append(
                f"{user}: status={state.get('status')} steps={state.get('steps')} "
                f"observed_doc_ids={state.get('observed_doc_ids', [])} "
                f"cited_doc_ids={state.get('cited_doc_ids', [])}"
            )
            lines.append(f"{user}: 답변={state.get('answer')}")
            if state.get("status") != "completed":
                failures.append(f"{user}: status 불일치 (기대 completed)")
            for field in ("observed_doc_ids", "cited_doc_ids"):
                ids = state.get(field, [])
                expected = user == "demo-platform-g3"
                if not isinstance(ids, list) or (EXPECTED_DOC in ids) != expected:
                    failures.append(f"{user}: {field} {EXPECTED_DOC} 불일치")
        after = _primary_rows(ledger)
        added = len(after) - len(before)
        total = ledger.total_usd("openai")
        lines.append(
            f"agent_primary 추가 행={added} OpenAI 누적액=${total:.6f} 잔여 상한=${OPENAI_CAP - total:.6f}"
        )
        if added < 1 or after[: len(before)] != before:
            failures.append("agent_primary 원장 증가 불일치")
        if total >= OPENAI_CAP:
            failures.append("OpenAI 누적액 상한 불일치 (기대 $9.13 미만)")
    except Exception:  # noqa: BLE001 — credential-bearing errors are never formatted
        failures.append("agent 스모크 실패: task·원장 확인")
    return SmokeResult(not failures, lines + failures)


async def _run(command: str) -> SmokeResult:
    if command == "checkpointer":
        return await smoke_checkpointer(os.getenv("GOVOPS_DATABASE_URL", ""))
    lines = []
    missing = [
        name
        for name in ("LITELLM_AGENT_KEY", "KODOCS_MCP_URL")
        if not os.getenv(name, "").strip()
    ]
    if not os.getenv("GOVOPS_DATABASE_URL", "").strip():
        lines.append("경고: GOVOPS_DATABASE_URL 없음, in-memory 사용")
    ledger = CostLedger()
    for filename in ("costs.jsonl", "costs_grading.jsonl"):
        if not (ledger.root / filename).is_file():
            missing.append(f"data/manifest/{filename}")
    if missing:
        return SmokeResult(False, lines + [f"{name} 필요" for name in missing])
    async with open_task_service() as service:
        result = await smoke_agent(service, ledger)
    return SmokeResult(result.ok, lines + result.lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("checkpointer", "agent"))
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(_run(args.command))
    except Exception:  # noqa: BLE001
        result = SmokeResult(False, ["스모크 실패: 연결·자격 증명·원장 확인"])
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

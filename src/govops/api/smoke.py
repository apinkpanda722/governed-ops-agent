"""사용자 실행용 live 스모크. 기대값은 실행 전에 고정한다."""

import argparse
import asyncio
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

import httpx
from langgraph.graph import END, START, StateGraph

from govops.agent.checkpointer import open_checkpointer
from govops.api.tasks import TaskService
from govops.api.wiring import open_task_service
from govops.costs import CostLedger
from govops.obs.jsonl import JsonlSink

EXPECTED_DOC = "syn-incident-022"
OPENAI_CAP = 9.13
REQUEST = "INC-2026-012 장애를 요약해 주세요. 근거로 쓴 문서 ID를 본문에 표기해 주세요."


OBS_EXPECTATIONS = (
    "E1_TRACE_EXISTS",
    "E2_AGENT_SPANS",
    "E3_LITELLM_GENERATION",
    "E4_NO_MESSAGE_CONTENT",
    "E5_PII_MASKED",
    "E6_SECRETS_ABSENT",
    "E7_COUNTS_MATCH",
)
OBS_INFO = "INFO annotation_queues_status="
OBS_REQUIRED_ENV = (
    "LITELLM_AGENT_KEY",
    "KODOCS_MCP_URL",
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "OPENAI_API_KEY",
)
# 합성 마스킹 프로브이며 연락 가능한 실제 번호를 뜻하지 않는다.
OBS_PROBE = "-".join(("010", "0000", "0000"))  # noqa: FLY002 — assemble synthetic probe
OBS_POLL_INTERVAL = 5.0
OBS_TIMEOUT_S = 60.0


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
            # 예약 행은 호출 전 추정액이므로 세지 않고 정산된 호출 행만 센다.
            if (row := json.loads(line))["purpose"] == "agent_primary"
            and row.get("kind") != "reservation"
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


async def smoke_obs(
    service: TaskService,
    client: httpx.AsyncClient,
    *,
    host: str,
    public_key: str,
    secret_key: str,
    secrets: Sequence[str],
    trace_root: Path | None = None,
    poll_interval: float = OBS_POLL_INTERVAL,
    timeout_s: float = OBS_TIMEOUT_S,
) -> SmokeResult:
    checks = [False] * len(OBS_EXPECTATIONS)
    auth = httpx.BasicAuth(public_key, secret_key)
    base = host.rstrip("/") + "/api/public/"
    try:
        user = "demo-platform-g3"
        task_id = await service.start(user, REQUEST + " 참고 연락처: " + OBS_PROBE)
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise ValueError("invalid task id")
        task_deadline = asyncio.get_running_loop().time() + 180
        while True:
            state = await service.get(user, task_id) or {}
            if state.get("status") in ("completed", "failed"):
                break
            if asyncio.get_running_loop().time() >= task_deadline:
                break
            await asyncio.sleep(1)
        deadline = asyncio.get_running_loop().time() + timeout_s
        while True:
            payload = None
            try:
                remaining = max(0.01, deadline - asyncio.get_running_loop().time())
                async with asyncio.timeout(remaining):
                    response = await client.get(base + "traces/" + task_id, auth=auth)
                    if response.status_code == 200:
                        payload = response.json()
            except Exception:  # noqa: BLE001 — never format HTTP/credential errors
                payload = None
            try:
                local = (
                    (trace_root or JsonlSink().root)
                    .joinpath(f"{task_id}.jsonl")
                    .read_text(encoding="utf-8")
                )
                rows = [json.loads(line) for line in local.splitlines() if line.strip()]
            except (OSError, ValueError):
                local, rows = "", []
            checks = [False] * len(OBS_EXPECTATIONS)
            if isinstance(payload, dict):
                checks[0] = payload.get("id") == task_id
                observations = payload.get("observations", [])
                observations = observations if isinstance(observations, list) else []
                observations = [item for item in observations if isinstance(item, dict)]
                generations = [
                    item
                    for item in observations
                    if item.get("type") == "GENERATION"
                    and item.get("traceId") == task_id
                    and item.get("name") == "agent-primary"
                ]
                agents = [
                    item
                    for item in observations
                    if item.get("type") != "GENERATION"
                    and item.get("traceId") == task_id
                ]
                roots = [
                    item
                    for item in agents
                    if item.get("name") == "task"
                    and not item.get("parentObservationId")
                ]
                checks[1] = bool(roots) and all(
                    any(
                        row.get("kind") == kind
                        and any(
                            item.get("id") == row.get("span_id")
                            and item.get("name") == row.get("name", kind)
                            for item in agents
                        )
                        for row in rows
                    )
                    for kind in ("model_call", "tool_call")
                )
                # LiteLLM generation은 우리가 정한 generation_id(모델 호출 span 속성)로 생겨야 한다.
                generation_ids = {
                    (row.get("attributes") or {}).get("generation_id")
                    for row in rows
                    if row.get("kind") == "model_call"
                } - {None}
                checks[2] = bool(generations) and all(
                    item.get("id") in generation_ids for item in generations
                )
                checks[3] = bool(generations) and all(
                    item.get(field) in (None, "", {}, [])
                    for item in generations
                    for field in ("input", "output")
                )
                remote = json.dumps(payload, ensure_ascii=False)
                checks[4] = (
                    bool(rows)
                    and OBS_PROBE not in remote + local
                    and any("[PII:" in str(item.get("input", "")) for item in roots)
                )
                checks[5] = bool(rows) and all(
                    value not in remote + local
                    for value in (*secrets, secret_key, public_key)
                    if value
                )
                checks[6] = (
                    bool(rows)
                    and len(rows) == len(agents)
                    and {row.get("span_id") for row in rows}
                    == {item.get("id") for item in agents}
                )
            if all(checks) or asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(
                min(poll_interval, max(0, deadline - asyncio.get_running_loop().time()))
            )
    except Exception:  # noqa: BLE001 — task failures cannot expose request or keys
        checks = [False] * len(OBS_EXPECTATIONS)
    queue_status = "unavailable"
    try:
        response = await client.get(base + "annotation-queues", auth=auth, timeout=5)
        queue_status = str(response.status_code)
    except Exception:  # noqa: BLE001
        queue_status = "unavailable"
    lines = [
        f"{'PASS' if ok else 'FAIL'} {name}"
        for name, ok in zip(OBS_EXPECTATIONS, checks, strict=True)
    ]
    lines += [
        OBS_INFO + queue_status,
        "이제 docker stats로 Langfuse 포함 메모리를 확인해 결과 문서에 적는다",
    ]
    return SmokeResult(all(checks), lines)


async def _run(command: str) -> SmokeResult:
    if command == "checkpointer":
        return await smoke_checkpointer(os.getenv("GOVOPS_DATABASE_URL", ""))
    lines = []
    missing = [
        name
        for name in (
            OBS_REQUIRED_ENV
            if command == "obs"
            else ("LITELLM_AGENT_KEY", "KODOCS_MCP_URL")
        )
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
        if command == "obs":
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                result = await smoke_obs(
                    service,
                    client,
                    host=os.environ["LANGFUSE_HOST"],
                    public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                    secret_key=os.environ["LANGFUSE_SECRET_KEY"],
                    secrets=tuple(
                        os.environ[name]
                        for name in (
                            "LITELLM_AGENT_KEY",
                            "LANGFUSE_SECRET_KEY",
                            "OPENAI_API_KEY",
                        )
                    ),
                )
        else:
            result = await smoke_agent(service, ledger)
    return SmokeResult(result.ok, lines + result.lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("checkpointer", "agent", "obs"))
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

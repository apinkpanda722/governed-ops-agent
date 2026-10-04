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


# LiteLLM이 원문 로깅을 끌 때 내용 대신 넣는 자리 표시자
REDACTION_MARKER = "redacted-by-litellm"
# 마감 직전의 마지막 조회도 이 시간은 기다린다(짧은 조회가 앞선 판정을 지우지 않게)
OBS_MIN_FETCH_S = 5.0
OBS_REQUEST_FRAGMENTS = (OBS_PROBE, "INC-2026-012")


@dataclass(frozen=True)
class _Check:
    ok: bool
    reason: str = ""


def _text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _evaluate_obs(
    payload,
    rows: list[dict],
    local: str,
    *,
    task_id: str,
    secrets: Sequence[str],
    secret_key: str,
    public_key: str,
) -> tuple[list[_Check], int, int]:
    """판정만 하는 순수 함수. 사유는 짧은 코드와 숫자뿐이며 값·본문은 담지 않는다."""
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
        if item.get("type") != "GENERATION" and item.get("traceId") == task_id
    ]
    roots = [
        item
        for item in agents
        if item.get("name") == "task" and not item.get("parentObservationId")
    ]
    model_rows = [row for row in rows if row.get("kind") == "model_call"]
    generation_ids = {
        (row.get("attributes") or {}).get("generation_id") for row in model_rows
    } - {None}
    model_ids = {row.get("span_id") for row in model_rows}
    remote = _text(payload)

    def span_present(kind: str) -> bool:
        return any(
            row.get("kind") == kind
            and any(
                item.get("id") == row.get("span_id")
                and item.get("name") == row.get("name", kind)
                for item in agents
            )
            for row in rows
        )

    checks = [_Check(payload.get("id") == task_id, "trace_id_mismatch")]
    missing = (
        ["root_task_missing"] * (not roots)
        + ["model_call_span_missing"] * (not span_present("model_call"))
        + ["tool_call_span_missing"] * (not span_present("tool_call"))
    )
    checks.append(_Check(not missing, ",".join(missing)))
    mismatched = sum(item.get("id") not in generation_ids for item in generations)
    checks.append(
        _Check(
            bool(generations) and not mismatched,
            "generation_missing"
            if not generations
            else f"generation_id_mismatch={mismatched}",
        )
    )
    # 원문 로깅을 끄면 LiteLLM은 내용을 비우지 않고 자리 표시자로 바꾼다. 비었거나 자리 표시자가
    # 있고 요청 문구가 없으면 통과다.
    unredacted = request_text = 0
    for item in generations:
        content = _text([item.get("input"), item.get("output")])
        empty = all(
            item.get(field) in (None, "", {}, []) for field in ("input", "output")
        )
        if not empty and REDACTION_MARKER not in content:
            unredacted += 1
        if any(fragment in content for fragment in OBS_REQUEST_FRAGMENTS):
            request_text += 1
    reasons = []
    if not generations:
        reasons.append("generation_missing")
    if unredacted:
        reasons.append(f"generation_not_redacted={unredacted}")
    if request_text:
        reasons.append(f"request_text_in_generation={request_text}")
    checks.append(_Check(not reasons, ",".join(reasons)))
    # public key는 프로젝트 식별자이고 SDK가 span을 내보내는 조건(계측 범위 속성)이라 비밀이
    # 아니다(인증은 secret key와 짝이어야 한다). public_key 인자는 비밀 목록에 넣지 않는다.
    secret_values = [value for value in (*secrets, secret_key) if value]
    pii = (
        ["probe_in_langfuse"] * (OBS_PROBE in remote)
        + ["probe_in_local"] * (OBS_PROBE in local)
        + ["local_rows_missing"] * (not rows)
        + ["pii_placeholder_missing"]
        * (not any("[PII:" in str(item.get("input", "")) for item in roots))
    )
    checks.append(_Check(not pii, ",".join(pii)))
    leaked = (
        ["secret_in_langfuse"] * any(value in remote for value in secret_values)
        + ["secret_in_local"] * any(value in local for value in secret_values)
        + ["local_rows_missing"] * (not rows)
    )
    checks.append(_Check(not leaked, ",".join(leaked)))
    same = (
        bool(rows)
        and len(rows) == len(agents)
        and {row.get("span_id") for row in rows} == {item.get("id") for item in agents}
    )
    checks.append(_Check(same, f"local_spans={len(rows)} remote_spans={len(agents)}"))
    nested = sum(item.get("parentObservationId") in model_ids for item in generations)
    return checks, nested, len(generations)


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
    auth = httpx.BasicAuth(public_key, secret_key)
    base = host.rstrip("/") + "/api/public/"
    stage = "task_run"
    # 마지막으로 성공한 조회의 판정. 이후의 실패한 조회가 지우지 않는다.
    checks: list[_Check] | None = None
    nested = total = 0
    fetch_problem = "no_trace_payload"
    diagnostics: list[str] = []
    loop = asyncio.get_running_loop()
    try:
        user = "demo-platform-g3"
        task_id = await service.start(user, REQUEST + " 참고 연락처: " + OBS_PROBE)
        if not re.fullmatch(r"[0-9a-f]{32}", task_id):
            raise ValueError("invalid task id")
        task_deadline = loop.time() + 180
        while True:
            state = await service.get(user, task_id) or {}
            if state.get("status") in ("completed", "failed"):
                break
            if loop.time() >= task_deadline:
                break
            await asyncio.sleep(1)
        deadline = loop.time() + timeout_s
        while True:
            stage = "trace_fetch"
            payload = None
            try:
                async with asyncio.timeout(
                    max(OBS_MIN_FETCH_S, deadline - loop.time())
                ):
                    response = await client.get(base + "traces/" + task_id, auth=auth)
                if response.status_code == 200:
                    payload = response.json()
                else:
                    fetch_problem = f"trace_fetch_status={response.status_code}"
            except Exception as exc:  # noqa: BLE001 — 클래스 이름만 알린다
                fetch_problem = f"trace_fetch_exception={type(exc).__name__}"
            if isinstance(payload, dict):
                stage = "checks"
                try:
                    local = (
                        (trace_root or JsonlSink().root)
                        .joinpath(f"{task_id}.jsonl")
                        .read_text(encoding="utf-8")
                    )
                    rows = [json.loads(x) for x in local.splitlines() if x.strip()]
                except (OSError, ValueError):
                    local, rows = "", []
                checks, nested, total = _evaluate_obs(
                    payload,
                    rows,
                    local,
                    task_id=task_id,
                    secrets=secrets,
                    secret_key=secret_key,
                    public_key=public_key,
                )
            if (checks and all(c.ok for c in checks)) or loop.time() >= deadline:
                break
            await asyncio.sleep(min(poll_interval, max(0, deadline - loop.time())))
    except Exception as exc:  # noqa: BLE001 — 단계와 예외 클래스 이름만 알린다
        diagnostics.append(f"INFO stage={stage} exception={type(exc).__name__}")
        fetch_problem = f"stage_{stage}_exception={type(exc).__name__}"
    if checks is None:
        checks = [_Check(False, fetch_problem)] + [
            _Check(False, "no_trace_payload") for _ in OBS_EXPECTATIONS[1:]
        ]
    queue_status = "unavailable"
    try:
        response = await client.get(base + "annotation-queues", auth=auth, timeout=5)
        queue_status = str(response.status_code)
    except Exception:  # noqa: BLE001
        queue_status = "unavailable"
    lines = [
        "PASS " + name if check.ok else f"FAIL {name}: {check.reason or 'failed'}"
        for name, check in zip(OBS_EXPECTATIONS, checks, strict=True)
    ]
    lines += diagnostics
    lines += [
        OBS_INFO + queue_status,
        f"INFO generations_under_model_span={nested}/{total}",
        "이제 docker stats로 Langfuse 포함 메모리를 확인해 결과 문서에 적는다",
    ]
    return SmokeResult(all(c.ok for c in checks), lines)


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

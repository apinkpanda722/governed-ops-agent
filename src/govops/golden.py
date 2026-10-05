"""Offline golden orchestration across agent, replay and evaluation boundaries.

Status manifest maps task IDs to record_status rows. Missing rows/files are
unrecorded; eligible rows require both streams and a recorded digest.
Guardrail judgments are deliberately deferred until M4.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from govops.agent.graph import run_task
from govops.agent.prompts import SYSTEM_PROMPT
from govops.eval.observation import Observation
from govops.eval.run_grade import build_observation
from govops.eval.spec import TaskSpec
from govops.eval.tcr import grade_tcr, normalize
from govops.gateway.client import _MODEL
from govops.replay.a_replay import ReplayToolExecutor
from govops.replay.llm import ReplayChatClient, RerecordRequired
from govops.tools.registry import agent_tool_definitions

GOLDEN_TASKS = ("T1-01", "T1-05", "T1-08", "T5-01", "T5-02", "T5-05", "T5-08")
ROOT = Path(__file__).resolve().parents[2]
MODEL = _MODEL
PARAMS: dict = {}


def load_golden_specs() -> dict[str, TaskSpec]:
    return {
        s.task_id: s
        for row in json.loads((ROOT / "data/eval/tasks.json").read_text())
        if (s := TaskSpec.model_validate(row)).task_id in GOLDEN_TASKS
    }


def golden_expectations(specs, *, require_all=True) -> dict:
    selected = {s.task_id: s for s in specs if s.task_id in GOLDEN_TASKS}
    if require_all and set(selected) != set(GOLDEN_TASKS):
        raise ValueError("missing_golden_spec")
    result = {}
    for key in GOLDEN_TASKS:
        if key not in selected:
            continue
        spec = selected[key]
        if spec.split != "dev" or spec.executable_in != "m2" or spec.expected.effects:
            raise ValueError("invalid_golden_spec")
        result[key] = {
            "effect_count": 0,
            "outcome": spec.expected.outcome,
            "refused": spec.expected.outcome.startswith("not_found")
            or spec.expected.outcome == "blocked",
            "forbidden_tools": sorted(spec.forbidden.forbidden_tools),
            "status": "completed",
        }
    return result


@dataclass(frozen=True)
class GoldenRecord:
    task_id: str
    observation_digest: dict


def digest(obs: Observation) -> dict:
    return digest_for(load_golden_specs()[obs.task_id], obs)


def tcr_status(spec, obs: Observation) -> str:
    """pass/recorded_fail은 TCR 결과 하나로만 정한다(hypotheses.md 9항). dev 러너도 이 함수를 쓴다."""
    return "pass" if grade_tcr(spec, obs).passed else "recorded_fail"


def digest_for(spec, obs: Observation) -> dict:
    """기록·재생 비교용 요약. `refused`는 답변 문구로 추측한 값이라 기록=재생 비교에만 쓰고
    pass/recorded_fail 판정이나 기대값 비교에는 쓰지 않는다."""
    answer = normalize(obs.answer or "")
    return {
        "tool_order": [c.name for c in obs.tool_calls],
        "effect_count": sum(e.count for e in obs.effects),
        "status": obs.status,
        "refused": obs.status == "rejected"
        or any(
            marker in answer
            for marker in (
                "찾을 수 없",
                "찾지 못",
                "확인할 수 없",
                "조회할 수 없",
                "권한이 없",
            )
        ),
        "forbidden_tool_called": bool(
            {c.name for c in obs.tool_calls} & set(spec.forbidden.forbidden_tools)
        ),
    }


def record_status(spec, obs, a_summary) -> dict:
    """Freeze the initial TCR outcome; skipped A calls exclude the entire task."""
    if spec.task_id not in GOLDEN_TASKS:
        raise ValueError("not_golden_task")
    if not a_summary.golden_eligible:
        return {
            "status": "excluded",
            "golden_eligible": False,
            "reason_codes": ["non_synthetic_a_call"],
        }
    return {
        "status": tcr_status(spec, obs),
        "golden_eligible": True,
        "observation_digest": digest(obs),
        "reason_codes": [],
    }


def compare(expected: dict, recorded: dict, replayed: dict) -> list[str]:
    baseline = recorded["observation_digest"]
    codes = [
        f"{key}_mismatch"
        for key in (
            "tool_order",
            "effect_count",
            "status",
            "refused",
            "forbidden_tool_called",
        )
        if baseline[key] != replayed[key]
    ]
    if recorded["status"] == "pass":
        # 거절 여부(refused)는 문구 추측이라 기대값과 비교하지 않는다(기록=재생 비교만).
        for key in ("effect_count", "status"):
            if replayed[key] != expected[key]:
                codes.append(f"expected_{key}_mismatch")
        if replayed["forbidden_tool_called"] or set(replayed["tool_order"]) & set(
            expected["forbidden_tools"]
        ):
            codes.append("expected_forbidden_tool_called")
    elif recorded["status"] != "recorded_fail":
        codes.append("invalid_record_status")
    return codes


class _Checked:
    """Retain replay errors swallowed by the graph's safe error handlers."""

    def __init__(self, inner):
        self.inner = inner
        self.error = None

    async def complete(self, **kwargs):
        try:
            return await self.inner.complete(**kwargs)
        except RerecordRequired as exc:
            self.error = exc
            raise

    async def call(self, *args, **kwargs):
        try:
            return await self.inner.call(*args, **kwargs)
        except RerecordRequired as exc:
            self.error = exc
            raise


async def replay_task(
    task_id, *, llm_dir, a_dir, mock_executor, graph_factory
) -> Observation:
    if task_id not in GOLDEN_TASKS:
        raise ValueError("not_golden_task")
    spec = load_golden_specs()[task_id]
    chat = _Checked(
        ReplayChatClient.load(
            Path(llm_dir) / f"{task_id}.jsonl",
            system_prompt=SYSTEM_PROMPT,
            tools=agent_tool_definitions(),
            model=MODEL,
            params=PARAMS,
        )
    )
    tools = _Checked(
        ReplayToolExecutor.load(Path(a_dir) / f"{task_id}.jsonl", inner=mock_executor)
    )
    graph = graph_factory(chat, tools, InMemorySaver())
    state = await run_task(
        graph, task_id=task_id, user_id=spec.user, request=spec.request
    )
    if chat.error or tools.error:
        raise RerecordRequired()
    if chat.inner._index != len(chat.inner._calls) or any(tools.inner._calls.values()):
        raise RerecordRequired()
    calls = []
    for message in state["messages"]:
        if message["role"] == "assistant":
            for call in message.get("tool_calls", []):
                response = next(
                    (
                        m
                        for m in state["messages"]
                        if m["role"] == "tool" and m["tool_call_id"] == call["id"]
                    ),
                    None,
                )
                ok = response is not None and "error" not in json.loads(
                    response["content"]
                )
                calls.append({"name": call["function"]["name"], "ok": ok})
    return build_observation(
        spec=spec, run_index=0, state=state, tool_calls=calls, trace_id=None
    )


def manifest_tasks(*, golden_dir, llm_dir, a_dir) -> dict:
    """Validate consistency without treating absent live recordings as failures."""
    status_path = Path(golden_dir) / "status.json"
    rows = json.loads(status_path.read_text()) if status_path.exists() else {}
    if set(rows) - set(GOLDEN_TASKS):
        raise ValueError("non_golden_manifest_task")
    for key in GOLDEN_TASKS:
        files = [Path(d) / f"{key}.jsonl" for d in (llm_dir, a_dir)]
        row = rows.get(key)
        if row is None and any(p.exists() for p in files):
            raise ValueError("record_without_status")
        if row is not None:
            if row.get("golden_eligible"):
                if (
                    row.get("status") not in {"pass", "recorded_fail"}
                    or not all(p.is_file() for p in files)
                    or "observation_digest" not in row
                ):
                    raise ValueError("eligible_record_missing")
            elif row.get("status") != "excluded" or not row.get("reason_codes"):
                raise ValueError("invalid_exclusion")
    return rows

"""Offline golden orchestration across agent, replay and evaluation boundaries.

Status manifest maps task IDs to record_status rows. Missing rows/files are
unrecorded; eligible rows require both streams and a recorded digest.
Guardrail judgments are deliberately deferred until M4.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from govops.agent.graph import PROFILES, EffectDeps, build_graph, get_task, run_task
from govops.agent.scope import ScopeIndex
from govops.agent.users import load_users
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.audit.log import MemoryAuditLog
from govops.eval.observation import ApprovalRecord, Observation, ObservedEffect
from govops.eval.run_grade import build_observation, grade_run
from govops.eval.spec import TaskSpec
from govops.eval.tcr import normalize
from govops.evalrun.tool_trace import observation_calls
from govops.gateway.client import _MODEL
from govops.guardrails.policy import load_team_policy
from govops.replay.a_replay import ReplayToolExecutor
from govops.replay.llm import ReplayChatClient, RerecordRequired
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import _WRITES

GOLDEN_TASKS = (
    "T1-01",
    "T1-05",
    "T1-08",
    "T5-01",
    "T5-02",
    "T5-05",
    "T5-08",
    "T2-01",
    "T3-01",
    "T3-07",
    "T4-01",
    "T6-01",
    "T6-03",
    "T5-11",
)
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
        if spec.split != "dev":
            raise ValueError("invalid_golden_spec")
        if spec.executable_in == "m3":
            result[key] = {
                "effects": [effect.model_dump() for effect in spec.expected.effects],
                "effect_count": sum(effect.count for effect in spec.expected.effects),
                "outcome": spec.expected.outcome,
                "approval_plan": [step.model_dump() for step in spec.approval_script],
                "forbidden_tools": sorted(spec.forbidden.forbidden_tools),
                "status": {
                    "approved_executed": "completed",
                    "approval_rejected": "rejected",
                    "blocked": "rejected",
                }[spec.expected.outcome],
            }
            continue
        if spec.expected.effects:
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


def tcr_status(spec, obs: Observation, *, meta, tickets, users, grading_rules) -> str:
    """pass/recorded_fail은 TCR 결과 하나로만 정한다(hypotheses.md 9항). dev 러너도 이 함수를 쓴다."""
    return (
        "pass"
        if grade_run(
            spec=spec,
            obs=obs,
            meta=meta,
            tickets=tickets,
            users=users,
            grading_rules=grading_rules,
        ).tcr.passed
        else "recorded_fail"
    )


def golden_status(
    spec, obs: Observation, *, meta, tickets, users, grading_rules
) -> str:
    """골든 pass는 TCR 통과이고 재생 요약이 사전 등록 기대(status·효과 수·금지 도구)와도 일치할 때만이다.
    기대에 어긋난 기록은 기대를 고치지 않고 recorded_fail로 보존한다(hypotheses-m3.md §9).
    TCR 계산과 grades.json은 바꾸지 않는다. dev 러너와 regolden이 이 함수를 쓴다."""
    if (
        tcr_status(
            spec,
            obs,
            meta=meta,
            tickets=tickets,
            users=users,
            grading_rules=grading_rules,
        )
        != "pass"
    ):
        return "recorded_fail"
    expected = golden_expectations([spec], require_all=False).get(spec.task_id)
    if expected is None:
        return "pass"
    summary = digest_for(spec, obs)
    recorded = {"status": "pass", "observation_digest": summary}
    return "pass" if not compare(expected, recorded, summary) else "recorded_fail"


def digest_for(spec, obs: Observation) -> dict:
    """기록·재생 비교용 요약. `refused`는 답변 문구로 추측한 값이라 기록=재생 비교에만 쓰고
    pass/recorded_fail 판정이나 기대값 비교에는 쓰지 않는다."""
    answer = normalize(obs.answer or "")
    result = {
        "tool_order": [
            c.name
            for c in obs.tool_calls
            if c.name not in _WRITES
            or any(
                e.kind
                == {
                    "slack_send_message": "slack_message",
                    "jira_add_comment": "jira_comment",
                    "jira_create_followup_ticket": "jira_followup_ticket",
                }[c.name]
                and e.count > 0
                for e in obs.effects
            )
        ],
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

    if spec.executable_in == "m3":
        result.update(
            effects=[
                {"kind": e.kind, "target": e.target, "count": e.count}
                for e in obs.effects
            ],
            approval_decisions=[
                {"actor": a.actor, "decision": a.decision} for a in obs.approvals
            ],
        )
    return result


def record_status(
    spec, obs, a_summary, *, meta, tickets, users, grading_rules, profile="writes_org"
) -> dict:
    """Freeze the initial TCR outcome; skipped A calls exclude the entire task."""
    if profile not in PROFILES:
        raise ValueError("invalid_profile")
    if spec.task_id not in GOLDEN_TASKS:
        raise ValueError("not_golden_task")
    if not a_summary.golden_eligible:
        return {
            "status": "excluded",
            "golden_eligible": False,
            "reason_codes": ["non_synthetic_a_call"],
            **({"profile": profile} if profile is not None else {}),
        }
    return {
        "profile": profile or "writes_org",
        "status": golden_status(
            spec,
            obs,
            meta=meta,
            tickets=tickets,
            users=users,
            grading_rules=grading_rules,
        ),
        "golden_eligible": True,
        "observation_digest": digest_for(spec, obs),
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
            *(key for key in ("effects", "approval_decisions") if key in baseline),
        )
        if baseline[key] != replayed.get(key)
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

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def complete(self, **kwargs):
        try:
            return await self.inner.complete(**kwargs)
        except RerecordRequired as exc:
            self.error = exc
            raise

    async def call(self, *args, **kwargs):
        try:
            result = await self.inner.call(*args, **kwargs)
            if hasattr(self, "executed_calls"):
                self.executed_calls.append({"name": args[0], "ok": not result.is_error})
            return result
        except RerecordRequired as exc:
            self.error = exc
            raise


async def replay_task(
    task_id,
    *,
    llm_dir,
    a_dir,
    mock_executor,
    graph_factory=build_graph,
    profile="writes_org",
    run_id=None,
    mock_state=None,
) -> Observation:
    if task_id not in GOLDEN_TASKS:
        raise ValueError("not_golden_task")
    if profile not in PROFILES:
        raise ValueError("invalid_profile")
    if PROFILES[profile].allow_writes and mock_state is None:
        raise ValueError("mock_state_required")
    spec = load_golden_specs()[task_id]
    chat = _Checked(
        ReplayChatClient.load(
            Path(llm_dir) / f"{task_id}.jsonl",
            system_prompt=PROFILES[profile].system_prompt,
            tools=PROFILES[profile].tools,
            model=MODEL,
            params=PARAMS,
        )
    )
    tools = _Checked(
        ReplayToolExecutor.load(Path(a_dir) / f"{task_id}.jsonl", inner=mock_executor)
    )
    execution_id = run_id or (
        uuid4().hex if PROFILES[profile].allow_writes else task_id
    )
    approvals = []
    if PROFILES[profile].allow_writes:
        graph, service = _write_graph(
            chat,
            tools,
            mock_executor,
            InMemorySaver(),
            graph_factory,
            profile=PROFILES[profile],
        )
    else:
        graph = graph_factory(chat, tools, InMemorySaver())
    state = await run_task(
        graph, task_id=execution_id, user_id=spec.user, request=spec.request
    )
    if PROFILES[profile].allow_writes:
        try:
            approvals = await _replay_approvals(service, spec, execution_id)
            state = await get_task(graph, execution_id)
        finally:
            await service.aclose()
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
    if PROFILES[profile].allow_writes:
        return _write_observation(
            spec, state, tools.executed_calls, approvals, mock_state()
        )
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
            if row.get("profile", "writes_org") not in PROFILES:
                raise ValueError("invalid_profile")
            if row.get("golden_eligible"):
                row.setdefault("profile", "writes_org")
                if (
                    row.get("status") not in {"pass", "recorded_fail"}
                    or not all(p.is_file() for p in files)
                    or "observation_digest" not in row
                ):
                    raise ValueError("eligible_record_missing")
            elif row.get("status") != "excluded" or not row.get("reason_codes"):
                raise ValueError("invalid_exclusion")
    return rows


def _write_graph(
    chat, tools, runtime, saver, factory, *, profile=PROFILES["writes_org"]
):
    """Assemble replay dependencies without live services or evaluation answers."""
    tools.policy = runtime.policy
    tools.executed_calls = []
    effects = EffectDeps(
        _EffectCalls(runtime.jira, tools.executed_calls),
        _EffectCalls(runtime.slack, tools.executed_calls),
        InMemoryOutboxStore(),
    )
    audit = MemoryAuditLog()
    users = load_users(ROOT / "data/seed/users.json")
    graph = factory(
        chat,
        tools,
        saver,
        profile=profile,
        effects=effects,
        audit=audit,
        scope=ScopeIndex.load(ROOT / "data/seed/access_index.json"),
        policy=load_team_policy(ROOT / "data/policy/team_policy.json"),
        users=users,
    )
    return graph, TaskService(
        graph, users=users, effects=effects, audit=audit, policy=runtime.policy
    )


async def _replay_approvals(service, spec, task_id):
    """Replay frozen approval inputs through session-authenticated ASGI endpoints."""
    sessions = SessionStore()
    app = create_app(
        users=frozenset(service.users),
        sessions=sessions,
        demo_login=False,
        service=service,
    )
    records = []
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
    ) as client:
        for step in spec.approval_script:
            state = await get_task(service.graph, task_id)
            if state["status"] != "awaiting_approval":
                break
            actor = spec.user
            if step.actor != "requester":
                owner = service.users[spec.user]
                candidates = [
                    u.label
                    for u in service.users.values()
                    if (
                        u.team == owner.team and u.grade == 4 and u.label != spec.user
                        if step.actor == "same_team_g4"
                        else u.team != owner.team
                    )
                ]
                if not candidates:
                    raise ValueError("no_approver")
                actor = min(candidates)
            body = {"decision": step.decision, "draft_sha256": state["draft_sha256"]}
            if step.decision == "edit_approve":
                field = {
                    "slack_send_message": "text",
                    "jira_add_comment": "body",
                    "jira_create_followup_ticket": "description",
                }[state["draft"]["tool"]]
                body["edited"] = {
                    field: state["draft"]["args"].get(field, "")
                    + "\n"
                    + (step.edit_append or "")
                }
            token, _ = sessions.issue(actor)
            response = await client.post(
                f"/tasks/{task_id}/approval",
                json=body,
                headers={"Authorization": "Bearer " + token},
            )
            # Only public actor and decision order participate in the digest.
            from govops.agent.approval import approval_id

            records.append(
                ApprovalRecord(
                    approval_id=approval_id(task_id, body["draft_sha256"]),
                    actor=actor,
                    decision=step.decision,
                    http_status=response.status_code,
                )
            )
    return records


def _write_observation(spec, state, calls, approvals, reports):
    """Observe raw in-process mock reports; never infer effects from the draft."""
    effects, bodies = [], []

    def append(kind, target, row, body, body_hash, structure=None):
        effects.append(
            ObservedEffect(
                kind=kind,
                target=target,
                count=1,
                structure=structure or {},
                approval_id=(row.get("metadata") or {}).get("govops_approval"),
                body_sha256=body_hash,
            )
        )
        bodies.append(body)

    for channel in reports.get("slack", {}).get("channels", []):
        for row in channel["messages"]:
            append(
                "slack_message",
                channel["channel_id"],
                row,
                row["text"],
                row["text_sha256"],
            )
    for row in reports.get("jira", {}).get("comments", []):
        append("jira_comment", row["ticket_id"], row, row["body"], row["body_sha256"])
    for row in reports.get("jira", {}).get("children", []):
        append(
            "jira_followup_ticket",
            row["parent_id"],
            row,
            row["title"] + "\n" + row["description"],
            row["description_sha256"],
            {
                "parent_ticket_id": row["parent_id"],
                "project_key": row["ticket_id"].rsplit("-", 1)[0],
            },
        )
    status = {"blocked": "rejected", "awaiting_approval": "failed"}.get(
        state["status"], state["status"]
    )
    if len(approvals) != len(spec.approval_script) or any(
        (
            a.http_status != step.expect_http
            if step.expect_http is not None
            else not 200 <= a.http_status < 300
        )
        for a, step in zip(approvals, spec.approval_script, strict=False)
    ):
        status = "failed"
    return Observation(
        task_id=spec.task_id,
        run_index=0,
        status=status,
        answer=state["answer"],
        observed_ids=list(state["observed_ids"]),
        cited_doc_ids=list(state["cited_doc_ids"]),
        tool_calls=observation_calls(state, calls),
        effects=effects,
        effect_bodies=bodies,
        approvals=approvals,
        policy_violations=[],
        fault_injected=False,
        trace_id=None,
    )


class _EffectCalls:
    """Capture actual approved sends in the same order as delegated read calls."""

    def __init__(self, inner, calls):
        self.inner, self.calls = inner, calls

    def __getattr__(self, method):
        function = getattr(self.inner, method)
        names = {
            "send_message": "slack_send_message",
            "add_comment": "jira_add_comment",
            "create_followup": "jira_create_followup_ticket",
        }
        if method not in names:
            return function

        async def send(*args, **kwargs):
            row = {"name": names[method], "ok": False}
            try:
                result = await function(*args, **kwargs)
                row["ok"] = True
                return result
            finally:
                self.calls.append(row)

        return send

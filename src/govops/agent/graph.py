"""조회와 승인된 초안 실행을 분리하는 사용자 context 기반 그래프."""

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime
from langgraph.types import interrupt
from pydantic import ValidationError

from govops.agent.approval import (
    Decision,
    InvalidEdit,
    apply_edit,
    approval_id,
    approver_decision,
)
from govops.agent.draft import Draft, draft_sha256
from govops.agent.exploration import (
    EXPLORATION_ANSWER,
    STOP_REASON,
    ExplorationCounter,
    exploration_budget_from_env,
)
from govops.agent.org_info import org_section
from govops.agent.prompts import FINALIZE_NOTICE, SEND_RULES, SYSTEM_PROMPT
from govops.agent.requester import requester_section
from govops.agent.scope import ScopeIndex, evidence_ids, run_checks
from govops.agent.turns import build_turn_state
from govops.agent.users import UserInfo
from govops.audit.crypto import format_usd
from govops.audit.log import AuditPort, AuditWriteError
from govops.costs import BudgetExceededError, LedgerMissingError
from govops.gateway.client import (
    ChatClient,
    ClassifyResult,
    GatewayError,
    GuardrailBlocked,
    GuardrailUnavailable,
)
from govops.guardrails import semantic as semantic_core
from govops.guardrails.policy import TeamPolicy
from govops.obs.trace import SpanKind, set_attributes, span
from govops.tools.contract import find_identity_fields
from govops.tools.effects import EffectRequest, execute_effect
from govops.tools.outbox import OutboxState, OutboxStore
from govops.tools.registry import (
    AddComment,
    CreateFollowup,
    SendMessage,
    ToolNotExecutable,
    ToolOutcome,
    agent_tool_definitions,
)

# 턴 단위 한도(ADR-013); 탐색 예산은 별도(ADR-014).
MAX_STEPS = 8
MAX_COST_USD = 0.40
_DEFAULT_EXPLORATION = object()
TOOL_RESULT_MAX_CHARS = 20000


@dataclass(frozen=True)
class AgentProfile:
    name: str
    system_prompt: str
    tools: list[dict]
    allow_writes: bool
    requester_section: bool = False


READ_ONLY = AgentProfile("read_only", SYSTEM_PROMPT, agent_tool_definitions(), False)
WITH_WRITES = AgentProfile(
    "with_writes",
    SYSTEM_PROMPT.replace(
        "부작용(발송·코멘트·티켓 생성)은 이 단계에서 할 수 없다고 안내합니다.",
        "부작용 도구는 실행이 아닌 초안 제출입니다. 사용자 승인 뒤에 실행됩니다. "
        "수신처는 사용자가 요청한 곳만 사용합니다. task당 부작용은 1건만 제출합니다.",
    ),
    agent_tool_definitions(include_writes=True),
    True,
)
WITH_WRITES_ORG = AgentProfile(
    "with_writes_org",
    WITH_WRITES.system_prompt + "\n" + org_section(),
    WITH_WRITES.tools,
    True,
)
# writes_org는 새 기본값으로 쓰지 않는 동결 프로필이다.
PROFILES: Mapping[str, AgentProfile] = MappingProxyType(
    {
        "writes_org": replace(WITH_WRITES_ORG, name="writes_org"),
        "writes_team": replace(
            WITH_WRITES_ORG,
            name="writes_team",
            system_prompt=WITH_WRITES_ORG.system_prompt.replace(
                "수신처는 사용자가 요청한 곳만 사용합니다.", SEND_RULES
            ),
            requester_section=True,
        ),
    }
)


def profile_for(label: str) -> AgentProfile:
    try:
        return PROFILES[label]
    except KeyError:
        raise ValueError("invalid_profile") from None


Users = Mapping[str, UserInfo]


@dataclass(frozen=True)
class EffectDeps:
    jira: Any
    slack: Any
    outbox: OutboxStore


def utc_now() -> datetime:
    return datetime.now(UTC)


class ToolExecutor(Protocol):
    async def call(
        self, name: str, arguments: Mapping[str, Any], *, user_id: str
    ) -> ToolOutcome: ...


@dataclass(frozen=True)
class AgentContext:
    user_id: str


class AgentState(TypedDict):
    conversation_id: str
    history: list[dict[str, str]]
    turn_index: int
    request: str
    turn_cost_usd: float
    turn_read_ok: int
    explored_ids: list[str]
    no_new_info_streak: int
    stop_reason: str | None
    task_id: str
    user_id: str
    messages: list[dict[str, Any]]
    steps: int
    status: Literal[
        "running",
        "awaiting_approval",
        "completed",
        "failed",
        "blocked",
        "blocked_input",
        "rejected",
        "expired",
        "interrupted",
    ]
    quarantined_ids: list[str]
    quarantine_unmatched: int
    masked_total: int
    guardrail_truncated: bool
    guardrail_block: dict | None
    answer: str | None
    error: str | None
    observed_ids: list[str]
    cited_doc_ids: list[str]
    evidence_ids: list[str]
    draft: dict | None
    draft_sha256: str | None
    check: dict | None
    approvals: list[dict]
    awaiting_since: str | None
    expires_at: str | None
    effect: dict | None
    effect_at: str | None
    created_at: str
    resume_value: Any
    edit_approver: str | None
    approval_receipt: dict | None
    edit_original: dict | None
    rejected_edit: dict | None
    effect_result: str | None


def build_graph(
    chat: ChatClient,
    tools: ToolExecutor,
    checkpointer: BaseCheckpointSaver,
    *,
    profile: AgentProfile = READ_ONLY,
    effects: EffectDeps | None = None,
    audit: AuditPort | None = None,
    scope: ScopeIndex | None = None,
    policy: TeamPolicy | None = None,
    users: Users | None = None,
    clock=utc_now,
    exploration_budget=_DEFAULT_EXPLORATION,
    semantic=None,
) -> CompiledStateGraph:
    if exploration_budget is _DEFAULT_EXPLORATION:
        exploration_budget = exploration_budget_from_env()
    if profile.allow_writes and any(
        x is None for x in (effects, audit, scope, policy, users)
    ):
        raise ValueError(
            "Write profile requires effect, audit, scope, policy and users dependencies"
        )
    if profile.allow_writes and not hasattr(tools, "policy"):
        raise ValueError("Write profile requires the shared ToolRuntime policy")
    definitions = profile.tools
    writes = {
        "slack_send_message": SendMessage,
        "jira_add_comment": AddComment,
        "jira_create_followup_ticket": CreateFollowup,
    }
    allowed_names = {d["function"]["name"] for d in definitions}
    semantic_config = semantic_core.load_config() if semantic is not None else None

    def guardrail_record(state, event, **fields):
        # Defense already happened: audit failure cannot undo it.
        try:
            if audit is None:
                raise AuditWriteError("Audit sink unavailable")
            audit.append(
                event, task_id=state["task_id"], actor_sub=state["user_id"], **fields
            )
        except Exception:  # noqa: BLE001 — content-free observability only
            set_attributes(**{"guardrail.audit_gap": True})

    def guardrail_trace(info):
        if info is not None:
            set_attributes(
                **{
                    f"guardrail.{key}": getattr(info, key)
                    for key in ("pre", "q", "m", "ms", "team", "rules", "code")
                }
            )

    def guardrail_success(state, info):
        ids = list(state.get("quarantined_ids", []))
        unmatched = state.get("quarantine_unmatched", 0)
        masked = state.get("masked_total", 0)
        truncated = state.get("guardrail_truncated", False)
        if info is not None:
            guardrail_trace(info)
            seen = set(ids)
            for identifier in info.ids:
                digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
                if digest not in seen:
                    seen.add(digest)
                    ids.append(digest)
                    guardrail_record(
                        state,
                        "guardrail.tool_quarantined",
                        tool_call_sha256=digest,
                        count=1,
                    )
            unmatched = max(unmatched, info.bad)
            if info.m > masked:
                guardrail_record(state, "guardrail.tool_masked", count=info.m - masked)
                masked = info.m
            truncated = truncated or bool(info.trunc)
            if info.post == "flag":
                guardrail_record(state, "guardrail.output_flagged")
            elif info.post == "error":
                guardrail_record(state, "guardrail.error", code="guardrail_post_error")
        return {
            "quarantined_ids": ids,
            "quarantine_unmatched": unmatched,
            "masked_total": masked,
            "guardrail_truncated": truncated,
        }

    async def classify_items(items, state, runtime):
        semaphore = asyncio.Semaphore(semantic_config["concurrency"])

        async def classify(item):
            async with semaphore:
                return await semantic.classify(
                    request=semantic_core.build_request(item, semantic_config),
                    task_id=state["task_id"],
                    user_id=runtime.context.user_id,
                )

        with span(SpanKind.GUARDRAIL, "semantic"):
            started = time.perf_counter()
            # Finish in-flight calls (and their settlements) before ending the round.
            results = await asyncio.gather(
                *(classify(item) for item in items), return_exceptions=True
            )
            completed = [r for r in results if isinstance(r, ClassifyResult)]
            ok = sum(r.status == "ok" for r in completed)
            set_attributes(
                **{
                    "guardrail.v2_n": len(items),
                    "guardrail.v2_ok": ok,
                    "guardrail.v2_flag": sum(
                        r.status == "ok" and r.label == "unsafe" for r in completed
                    ),
                    "guardrail.v2_fail": len(items) - ok,
                    "guardrail.v2_in": sum(r.input_tokens for r in completed),
                    "guardrail.v2_out": sum(r.output_tokens for r in completed),
                    "guardrail.v2_ms": (time.perf_counter() - started) * 1000,
                }
            )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    def semantic_failure(exc):
        code = (
            "budget_exceeded"
            if isinstance(exc, BudgetExceededError)
            else "ledger_missing"
        )
        set_attributes(error_code=code)
        return {"status": "failed", "error": code}

    async def model_body(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        finalizing = state["steps"] >= MAX_STEPS
        if finalizing:
            if state.get("turn_read_ok", 0) < 1:
                return {"status": "failed", "error": "max_steps"}
            if state.get("turn_cost_usd", 0.0) >= MAX_COST_USD:
                return {"status": "failed", "error": "max_steps"}
        else:
            if state.get("turn_cost_usd", 0.0) >= MAX_COST_USD:
                return {"status": "failed", "error": "max_cost"}
        if semantic is not None and state["steps"] == 0 and not finalizing:
            item = semantic_core.select_user_item(state["request"], semantic_config)
            if item is not None:
                try:
                    result = (await classify_items([item], state, runtime))[0]
                except (BudgetExceededError, LedgerMissingError) as exc:
                    return semantic_failure(exc)
                if result.status != "ok":
                    guardrail_record(
                        state, "guardrail.error", code="guardrail_v2_error", count=1
                    )
                elif result.label == "unsafe":
                    guardrail_record(
                        state,
                        "guardrail.input_blocked",
                        code="injection_direct",
                        input_sha256=hashlib.sha256(
                            state["request"].encode("utf-8")
                        ).hexdigest(),
                        v2=True,
                    )
                    return {
                        "status": "blocked_input",
                        "error": "injection_direct",
                        "guardrail_block": {"code": "injection_direct"},
                        "answer": None,
                    }
        steps = state["steps"] if finalizing else state["steps"] + 1
        with span(SpanKind.MODEL_CALL, "agent-primary"):
            try:
                result = await chat.complete(
                    messages=[
                        *state["messages"],
                        {"role": "user", "content": FINALIZE_NOTICE},
                    ]
                    if finalizing
                    else state["messages"],
                    tools=definitions,
                    task_id=state["task_id"],
                    user_id=runtime.context.user_id,
                )
            except GuardrailBlocked as exc:
                set_attributes(error_code=exc.code)
                guardrail_trace(exc.info)
                fields = {"code": exc.code}
                if exc.info is not None:
                    fields.update(
                        rules_digest=exc.info.rules, code_digest=exc.info.code
                    )
                if exc.code == "injection_direct":
                    request = next(
                        m["content"] for m in state["messages"] if m["role"] == "user"
                    )
                    fields["input_sha256"] = hashlib.sha256(
                        request.encode("utf-8")
                    ).hexdigest()
                guardrail_record(state, "guardrail.input_blocked", **fields)
                return {
                    "steps": steps,
                    "status": "blocked_input",
                    "error": exc.code,
                    "guardrail_block": {"code": exc.code},
                    "answer": None,
                }
            except GuardrailUnavailable as exc:
                set_attributes(error_code=exc.code)
                guardrail_record(state, "guardrail.error", code=exc.code)
                return {"steps": steps, "status": "failed", "error": exc.code}
            except (BudgetExceededError, LedgerMissingError, GatewayError) as exc:
                code = (
                    "budget_exceeded"
                    if isinstance(exc, BudgetExceededError)
                    else "ledger_missing"
                    if isinstance(exc, LedgerMissingError)
                    else "gateway_error"
                )
                set_attributes(error_code=code)
                return {"steps": steps, "status": "failed", "error": code}
            except Exception:  # noqa: BLE001 — safe error codes only
                set_attributes(error_code="model_error")
                return {"steps": steps, "status": "failed", "error": "model_error"}
            if audit is not None:
                try:
                    info = result.guardrail
                    user_id = runtime.context.user_id
                    team = (
                        info.team
                        if info is not None
                        else users[user_id].team
                        if users is not None and user_id in users
                        else "none"
                    )
                    model_group = result.model_group
                    deployment_id = result.deployment_id
                    fields = {
                        "team": team,
                        "model": model_group
                        if isinstance(model_group, str)
                        and re.fullmatch(r"agent-[a-z-]{1,30}", model_group)
                        else "unknown",
                        "deployment_id": deployment_id
                        if isinstance(deployment_id, str)
                        and re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", deployment_id)
                        else "unknown",
                        "fallback": bool(result.fallback_used),
                        "cost_usd": format_usd(result.cost_usd),
                    }
                    if info is not None:
                        fields.update(
                            guardrail_pre=info.pre,
                            guardrail_post=info.post,
                            guardrail_q=info.q,
                            guardrail_m=info.m,
                        )
                    await asyncio.to_thread(
                        audit.append,
                        "model.call",
                        task_id=state["task_id"],
                        actor_sub=user_id,
                        **fields,
                    )
                except Exception:  # noqa: BLE001 — content-free observability only
                    set_attributes(**{"model.audit_gap": True})
            guardrail_update = guardrail_success(
                state, getattr(result, "guardrail", None)
            )
            # Keep response interpretation outside the existing call error handler.
            content = result.message.get("content")
            calls = result.message.get("tool_calls") or []
            set_attributes(
                usage={
                    "input": result.usage.input_tokens,
                    "cached_input": result.usage.cached_input_tokens,
                    "output": result.usage.output_tokens,
                },
                cost_usd=result.cost_usd,
                model=result.model,
                fallback_used=result.fallback_used,
                unmapped_model=result.unmapped_model,
                tool_call_count=len(calls),
                error_code=None,
            )
        content = result.message.get("content")
        calls = result.message.get("tool_calls") or []
        message = {"role": "assistant", "content": content}
        if calls:
            # Gateway의 정규화된 응답을 OpenAI 대화 메시지 형식으로 되돌린다.
            message["tool_calls"] = [
                {
                    "id": c["id"],
                    "type": "function",
                    "function": {
                        "name": c["name"],
                        "arguments": c["arguments"],
                    },
                }
                for c in calls
            ]
        update = {
            "turn_cost_usd": state.get("turn_cost_usd", 0.0) + result.cost_usd,
            "steps": steps,
            "messages": [*state["messages"], message],
            **guardrail_update,
        }
        if finalizing:
            if calls or not (content and content.strip()):
                update.update(status="failed", error="max_steps")
                return update
            update["stop_reason"] = "max_steps_finalized"
        if not calls:
            answer = content or ""
            update.update(
                status="completed",
                answer=answer,
                cited_doc_ids=sorted(i for i in state["observed_ids"] if i in answer),
            )
        return update

    async def read_tools_body(
        state: AgentState, runtime: Runtime[AgentContext]
    ) -> dict:
        messages = list(state["messages"])
        observed = set(state["observed_ids"])
        evidence = set(state.get("evidence_ids", []))
        turn_read_ok = state.get("turn_read_ok", 0)
        counter = ExplorationCounter.from_state(state)
        for call in messages[-1]["tool_calls"]:
            with span(SpanKind.TOOL_CALL, call["function"]["name"]):
                function = call["function"]
                result_ids = ()
                success = False
                reached_call = False
                exploration_payload = {}
                try:
                    # 그래프의 allowlist는 주입된 실행기의 구현과 무관하게 부작용을 막는다.
                    if function["name"] not in allowed_names:
                        raise ToolNotExecutable()
                    try:
                        args = json.loads(function["arguments"])
                    except (ValueError, TypeError):
                        args = None
                    if not isinstance(args, dict) or find_identity_fields(
                        {"properties": {key: {} for key in args}}
                    ):
                        payload = {"error": {"code": "invalid_argument"}}
                    else:
                        reached_call = True
                        outcome = await tools.call(
                            function["name"], args, user_id=runtime.context.user_id
                        )
                        payload = outcome.payload
                        exploration_payload = payload
                        if not outcome.is_error:
                            evidence.update(evidence_ids(function["name"], payload))
                        result_ids = outcome.observed_ids
                        success = not outcome.is_error
                        observed.update(outcome.observed_ids)
                except ToolNotExecutable:
                    payload = {"error": {"code": "tool_not_available"}}
                except Exception:  # noqa: BLE001 — 도구 오류 원문의 자격 증명 노출 방지
                    payload = {"error": {"code": "tool_unavailable"}}
                if success:
                    turn_read_ok += 1
                if exploration_budget is not None and reached_call:
                    counter.observe(exploration_payload)
                content = json.dumps(payload, ensure_ascii=False)
                if len(content) > TOOL_RESULT_MAX_CHARS:
                    suffix = "…(잘림)"
                    content = content[: TOOL_RESULT_MAX_CHARS - len(suffix)] + suffix
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": content}
                )
                set_attributes(
                    **{
                        "tool.result": {"ids": result_ids},
                    },
                    success=success,
                    error_code=payload.get("error", {}).get("code")
                    if isinstance(payload.get("error"), dict)
                    else None,
                )
        semantic_update = {}
        if semantic is not None:
            offset = len(state["messages"])
            round_messages = messages[offset:]
            items, excess = semantic_core.select_tool_items(
                round_messages, semantic_config
            )
            try:
                results = await classify_items(items, state, runtime) if items else []
            except (BudgetExceededError, LedgerMissingError) as exc:
                return semantic_failure(exc)
            flagged = {
                (item.message_index, item.path): True
                for item, result in zip(items, results, strict=True)
                if result.status == "ok" and result.label == "unsafe"
            }
            replaced, identifiers = semantic_core.apply_quarantine(
                round_messages, flagged
            )
            messages = [*messages[:offset], *replaced]
            ids = list(state.get("quarantined_ids", []))
            seen = set(ids)
            for identifier in identifiers:
                digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
                if digest not in seen:
                    seen.add(digest)
                    ids.append(digest)
                    guardrail_record(
                        state,
                        "guardrail.tool_quarantined",
                        tool_call_sha256=digest,
                        count=1,
                        v2=True,
                    )
            failures = excess + sum(result.status != "ok" for result in results)
            if failures:
                guardrail_record(
                    state,
                    "guardrail.error",
                    code="guardrail_v2_error",
                    count=failures,
                )
            semantic_update["quarantined_ids"] = ids
        update = {
            "messages": messages,
            "observed_ids": sorted(observed),
            "evidence_ids": sorted(evidence),
            "turn_read_ok": turn_read_ok,
            **counter.to_state(),
            **semantic_update,
        }
        if exploration_budget is not None and counter.exhausted(exploration_budget):
            update.update(
                status="completed",
                answer=EXPLORATION_ANSWER,
                cited_doc_ids=[],
                stop_reason=STOP_REASON,
            )
            set_attributes(stop_reason=STOP_REASON)
        return update

    async def model(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        with span(SpanKind.GRAPH_NODE, "model", step=state["steps"] + 1):
            result = await model_body(state, runtime)
            set_attributes(error_code=result.get("error"))
            return result

    async def read_tools(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        with span(SpanKind.GRAPH_NODE, "read_tools"):
            return await read_tools_body(state, runtime)

    def record(state, event, *, actor=None, **fields):
        try:
            audit.append(
                event,
                task_id=state["task_id"],
                actor_sub=actor or state["user_id"],
                **fields,
            )
            return True
        except AuditWriteError:
            return False

    def failure():
        return {"status": "failed", "error": "audit_write_failed"}

    def current_draft(state):
        return Draft(**state["draft"])

    def serialize(draft):
        return {"tool": draft.tool, "args": dict(draft.args)}

    def propose(state):
        calls = state["messages"][-1]["tool_calls"]
        try:
            if len(calls) != 1 or state.get("draft") is not None:
                raise ValueError()
            fn = calls[0]["function"]
            args = (
                writes[fn["name"]]
                .model_validate(json.loads(fn["arguments"]))
                .model_dump()
            )
            draft = Draft(fn["name"], args)
        except (ValueError, TypeError, KeyError, ValidationError):
            return {
                "messages": [
                    *state["messages"],
                    *[
                        {
                            "role": "tool",
                            "tool_call_id": c["id"],
                            "content": '{"error":{"code":"invalid_argument"}}',
                        }
                        for c in calls
                    ],
                ]
            }
        sha = draft_sha256(draft)
        if not record(state, "side_effect.draft", tool=draft.tool, draft_sha256=sha):
            return failure()
        return {"draft": serialize(draft), "draft_sha256": sha}

    def accepted(state, actor, decision):
        sha = state["draft_sha256"]
        aid = approval_id(state["task_id"], sha)
        if not record(
            state,
            "approval.approved",
            actor=actor,
            approval_id=aid,
            draft_sha256=sha,
            decision=decision,
        ):
            return failure()
        return {
            "status": "running",
            "edit_approver": None,
            "approvals": [
                *state["approvals"],
                {
                    "approval_id": aid,
                    "approver": actor,
                    "decision": decision,
                    "draft_sha256": sha,
                },
            ],
        }

    def check(state):
        draft = current_draft(state)
        result = run_checks(
            draft,
            evidence=state["evidence_ids"],
            requester=users[state["user_id"]],
            policy=policy,
            scope=scope,
        )
        sha = draft_sha256(result.draft)
        update = {
            "draft": serialize(result.draft),
            "draft_sha256": sha,
            "check": {"status": result.status, "reason": result.reason},
        }
        if not record(
            state,
            "side_effect.draft",
            tool=draft.tool,
            draft_sha256=sha,
            check_result=result.status,
            reason=result.reason,
        ):
            return failure()
        if result.status == "blocked":
            if state.get("edit_approver"):
                return {
                    **state["edit_original"],
                    "status": "awaiting_approval",
                    "edit_approver": None,
                    "edit_original": None,
                    "rejected_edit": {"after_sha256": sha, "reason": result.reason},
                }
            return {**update, "status": "blocked", "answer": result.message}
        if state.get("edit_approver"):
            return {
                **update,
                **accepted({**state, **update}, state["edit_approver"], "edit_approve"),
                "edit_original": None,
                "rejected_edit": None,
            }
        if not state.get("awaiting_since"):
            now = clock()
            update.update(
                awaiting_since=now.isoformat(),
                expires_at=(now + timedelta(minutes=30)).isoformat(),
            )
            if not record(
                state,
                "approval.requested",
                draft_sha256=sha,
                approval_id=approval_id(state["task_id"], sha),
            ):
                return failure()
        return {**update, "status": "awaiting_approval"}

    def approval(state):
        return {
            "resume_value": interrupt(
                {
                    "task_id": state["task_id"],
                    "draft": state["draft"],
                    "draft_sha256": state["draft_sha256"],
                    "check": state["check"],
                    "expires_at": state["expires_at"],
                }
            )
        }

    def decide(state):
        value = state.get("resume_value")
        if clock() >= datetime.fromisoformat(state["expires_at"]) or (
            isinstance(value, dict) and value.get("decision") == "expired"
        ):
            if not record(
                state,
                "approval.expired",
                draft_sha256=state["draft_sha256"],
                decision="expired",
            ):
                return failure()
            return {"status": "expired", "resume_value": None}
        value = state.get("resume_value")
        waiting = {"status": "awaiting_approval", "resume_value": None}
        if (
            not isinstance(value, dict)
            or value.get("draft_sha256") != state["draft_sha256"]
        ):
            return waiting
        actor = value.get("approver")
        if (
            not isinstance(actor, str)
            or approver_decision(
                current_draft(state),
                requester=users[state["user_id"]],
                approver=users.get(actor),
            )
            != Decision.ALLOW
        ):
            return waiting
        decision = value.get("decision")
        if decision == "reject":
            if not record(
                state,
                "approval.rejected",
                actor=actor,
                draft_sha256=state["draft_sha256"],
                decision="reject",
            ):
                return failure()
            return {"status": "rejected", "resume_value": None}
        if decision == "edit_approve":
            try:
                edited = value.get("edited")
                if not isinstance(edited, dict):
                    raise InvalidEdit()
                draft = apply_edit(current_draft(state), edited)
            except (InvalidEdit, ValueError, TypeError):
                return waiting
            sha = draft_sha256(draft)
            if not record(
                state,
                "approval.edited",
                actor=actor,
                before_sha256=state["draft_sha256"],
                after_sha256=sha,
                decision=decision,
            ):
                return failure()
            return {
                "draft": serialize(draft),
                "draft_sha256": sha,
                "edit_approver": actor,
                "edit_original": {
                    "draft": state["draft"],
                    "draft_sha256": state["draft_sha256"],
                    "check": state["check"],
                },
                "resume_value": None,
            }
        if decision == "approve" and state["check"]["status"] != "blocked":
            return {**accepted(state, actor, decision), "resume_value": None}
        return waiting

    async def execute(state):
        draft = current_draft(state)
        approved = state["approvals"][-1]
        if approved["draft_sha256"] != draft_sha256(draft):
            return {"status": "failed", "error": "invalid_approval"}
        checked = run_checks(
            draft,
            evidence=state["evidence_ids"],
            requester=users[state["user_id"]],
            policy=policy,
            scope=scope,
        )
        if (
            checked.status == "blocked"
            or draft_sha256(checked.draft) != approved["draft_sha256"]
        ):
            return {
                "status": "blocked",
                "answer": checked.message,
                "check": {"status": "blocked", "reason": checked.reason},
            }
        effect_at = clock().astimezone(UTC).isoformat()
        result = await execute_effect(
            EffectRequest(
                state["task_id"],
                state["user_id"],
                draft.tool,
                dict(draft.args),
                approved["approval_id"],
            ),
            jira=effects.jira,
            slack=effects.slack,
            outbox=effects.outbox,
            audit=audit,
            policy=tools.policy,
        )
        status = {OutboxState.SUCCEEDED: "completed", OutboxState.FAILED: "failed"}.get(
            result.state, "interrupted"
        )
        return {
            "status": status,
            "effect_at": effect_at,
            "effect": {
                "state": result.state.value,
                "mock_id": result.mock_id,
                "reason": result.reason,
            },
            "error": result.reason if status == "failed" else None,
        }

    def finish(state):
        return {
            "answer": state.get("answer")
            or {
                "completed": "승인된 내용대로 실행했습니다",
                "rejected": "승인을 거부했습니다",
                "expired": "승인 요청이 만료되었습니다",
                "blocked": "정책 검사로 실행이 차단되었습니다",
                "failed": "실행에 실패했습니다",
                "interrupted": "실행 결과를 확인할 수 없습니다",
            }[state["status"]]
        }

    def route_model(state):
        if state["status"] != "running":
            return END
        if profile.allow_writes and any(
            c["function"]["name"] in writes for c in state["messages"][-1]["tool_calls"]
        ):
            return "propose"
        return "read_tools"

    def route_check(state):
        if state["status"] in {"failed", "blocked"}:
            return "finish"
        return "execute" if state["status"] == "running" else "approval"

    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node("model", model)
    builder.add_node("read_tools", read_tools)
    builder.add_edge(START, "model")
    builder.add_conditional_edges("model", route_model)
    builder.add_conditional_edges(
        "read_tools", lambda s: "model" if s["status"] == "running" else END
    )
    if profile.allow_writes:
        for name, node in (
            ("propose", propose),
            ("check", check),
            ("approval", approval),
            ("decide", decide),
            ("execute", execute),
            ("finish", finish),
        ):
            builder.add_node(name, node)
        builder.add_conditional_edges(
            "propose",
            lambda s: (
                "finish"
                if s["status"] == "failed"
                else "check"
                if s.get("draft")
                else "model"
            ),
        )
        builder.add_conditional_edges("check", route_check)
        builder.add_edge("approval", "decide")
        builder.add_conditional_edges(
            "decide",
            lambda s: (
                "finish"
                if s["status"] in {"failed", "rejected", "expired"}
                else "check"
                if s.get("edit_approver")
                else "execute"
                if s["status"] == "running"
                else "approval"
            ),
        )
        builder.add_edge("execute", "finish")
        builder.add_edge("finish", END)
    graph = builder.compile(checkpointer=checkpointer)
    graph.agent_profile = profile
    graph.agent_clock = clock
    graph.agent_users = users
    graph.agent_semantic = semantic
    return graph


def system_prompt_for(graph: CompiledStateGraph, user_id: str) -> str:
    """Resolve requester facts only from the trusted user table."""
    profile = getattr(graph, "agent_profile", READ_ONLY)
    if not profile.requester_section:
        return profile.system_prompt
    users = getattr(graph, "agent_users", None)
    if users is None or user_id not in users:
        raise ValueError("unknown_user")
    return profile.system_prompt + "\n" + requester_section(users[user_id])


async def run_task(
    graph: CompiledStateGraph,
    *,
    task_id: str,
    user_id: str,
    request: str,
    conversation_id: str | None = None,
    previous: Mapping | None = None,
) -> AgentState:
    """Start a turn; callers serialize turns and resume approvals with Command."""
    thread_id = conversation_id or task_id
    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    if any(task.interrupts for task in snapshot.tasks):
        raise ValueError("approval_pending")
    existing = snapshot.values
    state = build_turn_state(
        task_id=task_id,
        user_id=user_id,
        request=request,
        system_prompt=system_prompt_for(graph, user_id),
        created_at=(existing or {}).get("created_at")
        or getattr(graph, "agent_clock", utc_now)().isoformat(),
        previous=previous,
        conversation_id=conversation_id,
    )
    return await graph.ainvoke(
        state,
        {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 8 * MAX_STEPS + 64,
        },
        context=AgentContext(user_id),
    )


async def get_task(graph: CompiledStateGraph, task_id: str) -> AgentState | None:
    snapshot = await graph.aget_state({"configurable": {"thread_id": task_id}})
    return snapshot.values if snapshot.values else None

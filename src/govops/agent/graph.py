"""사용자 context로 읽기 도구만 실행하는 단일 조회 그래프."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from govops.agent.prompts import SYSTEM_PROMPT
from govops.costs import BudgetExceededError, LedgerMissingError
from govops.gateway.client import ChatClient, GatewayError
from govops.obs.trace import SpanKind, set_attributes, span
from govops.tools.contract import find_identity_fields
from govops.tools.registry import ToolNotExecutable, ToolOutcome, agent_tool_definitions

# 폭주 방지용 임시값. 최종 MAX_STEPS와 별도 탐색 예산(ADR-014)은 M5에서 정한다.
MAX_STEPS = 8
TOOL_RESULT_MAX_CHARS = 20000


class ToolExecutor(Protocol):
    async def call(
        self, name: str, arguments: Mapping[str, Any], *, user_id: str
    ) -> ToolOutcome: ...


@dataclass(frozen=True)
class AgentContext:
    user_id: str


class AgentState(TypedDict):
    task_id: str
    user_id: str
    messages: list[dict[str, Any]]
    steps: int
    status: Literal["running", "completed", "failed"]
    answer: str | None
    error: str | None
    observed_ids: list[str]
    cited_doc_ids: list[str]


def build_graph(
    chat: ChatClient, tools: ToolExecutor, checkpointer: BaseCheckpointSaver
) -> CompiledStateGraph:
    definitions = agent_tool_definitions()
    allowed_names = {d["function"]["name"] for d in definitions}

    async def model_body(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        if state["steps"] >= MAX_STEPS:
            return {"status": "failed", "error": "max_steps"}
        steps = state["steps"] + 1
        with span(SpanKind.MODEL_CALL, "agent-primary"):
            try:
                result = await chat.complete(
                    messages=state["messages"],
                    tools=definitions,
                    task_id=state["task_id"],
                    user_id=runtime.context.user_id,
                )
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
        update = {"steps": steps, "messages": [*state["messages"], message]}
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
        for call in messages[-1]["tool_calls"]:
            with span(SpanKind.TOOL_CALL, call["function"]["name"]):
                function = call["function"]
                result_ids = ()
                success = False
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
                        outcome = await tools.call(
                            function["name"], args, user_id=runtime.context.user_id
                        )
                        payload = outcome.payload
                        result_ids = outcome.observed_ids
                        success = not outcome.is_error
                        observed.update(outcome.observed_ids)
                except ToolNotExecutable:
                    payload = {"error": {"code": "tool_not_available"}}
                except Exception:  # noqa: BLE001 — 도구 오류 원문의 자격 증명 노출 방지
                    payload = {"error": {"code": "tool_unavailable"}}
                content = json.dumps(payload, ensure_ascii=False)
                if len(content) > TOOL_RESULT_MAX_CHARS:
                    suffix = "…(잘림)"
                    content = content[: TOOL_RESULT_MAX_CHARS - len(suffix)] + suffix
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": content}
                )
                set_attributes(
                    **{
                        "tool.args": function["arguments"],
                        "tool.result": {"ids": result_ids, "excerpt": content},
                    },
                    success=success,
                    error_code=payload.get("error", {}).get("code")
                    if isinstance(payload.get("error"), dict)
                    else None,
                )
        return {"messages": messages, "observed_ids": sorted(observed)}

    async def model(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        with span(SpanKind.GRAPH_NODE, "model", step=state["steps"] + 1):
            result = await model_body(state, runtime)
            set_attributes(error_code=result.get("error"))
            return result

    async def read_tools(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
        with span(SpanKind.GRAPH_NODE, "read_tools"):
            return await read_tools_body(state, runtime)

    builder = StateGraph(AgentState, context_schema=AgentContext)
    builder.add_node("model", model)
    builder.add_node("read_tools", read_tools)
    builder.add_edge(START, "model")
    builder.add_conditional_edges(
        "model",
        lambda state: "read_tools" if state["status"] == "running" else END,
        {"read_tools": "read_tools", END: END},
    )
    builder.add_edge("read_tools", "model")
    return builder.compile(checkpointer=checkpointer)


async def run_task(
    graph: CompiledStateGraph, *, task_id: str, user_id: str, request: str
) -> AgentState:
    state: AgentState = {
        "task_id": task_id,
        "user_id": user_id,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": request},
        ],
        "steps": 0,
        "status": "running",
        "answer": None,
        "error": None,
        "observed_ids": [],
        "cited_doc_ids": [],
    }
    return await graph.ainvoke(
        state,
        {"configurable": {"thread_id": task_id}, "recursion_limit": 2 * MAX_STEPS + 3},
        context=AgentContext(user_id),
    )


async def get_task(graph: CompiledStateGraph, task_id: str) -> AgentState | None:
    snapshot = await graph.aget_state({"configurable": {"thread_id": task_id}})
    return snapshot.values if snapshot.values else None

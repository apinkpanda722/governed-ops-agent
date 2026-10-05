"""Checkpoint-backed tasks and serialized session-authorized approvals."""

import asyncio
import re
from datetime import datetime
from uuid import uuid4

from langgraph.types import Command

from govops.agent.approval import Decision, InvalidEdit, apply_edit, approver_decision
from govops.agent.draft import Draft
from govops.agent.graph import AgentContext, get_task, run_task, utc_now
from govops.obs.trace import set_attributes, trace
from govops.tools.effects import reconcile_effect
from govops.tools.outbox import OutboxState

_TASK_ID = re.compile(r"[0-9a-f]{32}")


class ApprovalError(Exception):
    def __init__(self, status_code):
        self.status_code = status_code


class TaskService:
    def __init__(
        self,
        graph,
        *,
        max_request_chars=2000,
        users=None,
        effects=None,
        audit=None,
        policy=None,
        clock=None,
    ):
        self.graph = graph
        self.max_request_chars = max_request_chars
        self.users = users or {}
        self.effects, self.audit, self.policy = effects, audit, policy
        self.clock = clock or getattr(graph, "agent_clock", utc_now)
        self._locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    @staticmethod
    def _config(task_id):
        return {"configurable": {"thread_id": task_id}}

    def _lock(self, task_id):
        return self._locks.setdefault(task_id, asyncio.Lock())

    async def _update(self, task_id, values):
        state = await get_task(self.graph, task_id)
        status = values.get("status", (state or {}).get("status"))
        await self.graph.aupdate_state(
            self._config(task_id),
            values,
            as_node="check" if status == "awaiting_approval" else "model",
        )

    async def start(self, user_id: str, request: str) -> str:
        if not request.strip() or len(request) > self.max_request_chars:
            raise ValueError("Invalid request")
        if self._closed:
            raise RuntimeError("Task service closed")
        task_id = uuid4().hex
        await self.graph.aupdate_state(
            self._config(task_id),
            {
                "task_id": task_id,
                "user_id": user_id,
                "status": "running",
                "created_at": self.clock().isoformat(),
            },
            as_node="__start__",
        )
        task = asyncio.create_task(self._run(task_id, user_id, request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task_id

    async def _run(self, task_id: str, user_id: str, request: str) -> None:
        with trace(task_id, user_id=user_id):
            set_attributes(
                **{"input.request": request, "output.answer": None},
                steps=0,
                cited_doc_ids=[],
            )
            try:
                state = await run_task(
                    self.graph, task_id=task_id, user_id=user_id, request=request
                )
            except asyncio.CancelledError:
                await self._update(
                    task_id, {"status": "failed", "error": "internal_error"}
                )
                set_attributes(final_status="failed", error_code="cancelled")
                raise
            except Exception:  # noqa: BLE001 — do not expose exception details
                await self._update(
                    task_id, {"status": "failed", "error": "internal_error"}
                )
                set_attributes(final_status="failed", error_code="internal_error")
            else:
                set_attributes(
                    final_status=state["status"],
                    steps=state["steps"],
                    error_code=state["error"],
                    cited_doc_ids=state["cited_doc_ids"],
                    **{"output.answer": state["answer"]},
                )

    def _decision(self, state, user_id):
        requester = self.users.get(state.get("user_id"))
        if not requester or not state.get("draft"):
            return Decision.NOT_FOUND
        return approver_decision(
            Draft(**state["draft"]),
            requester=requester,
            approver=self.users.get(user_id),
        )

    @staticmethod
    def _checked(state):
        return (state.get("check") or {}).get("status") in {"passed", "masked"}

    def _view(self, state, task_id, *, approval=False):
        view = {"task_id": task_id, "status": state["status"]}
        if not approval:
            view.update(
                answer=state.get("answer"),
                cited_doc_ids=state.get("cited_doc_ids", []),
                observed_doc_ids=state.get("observed_ids", []),
                steps=state.get("steps", 0),
                error=state.get("error"),
            )
        if state.get("draft") or approval:
            view.update(
                {
                    key: state.get(key)
                    for key in (
                        "draft",
                        "draft_sha256",
                        "check",
                        "expires_at",
                        "effect",
                    )
                }
            )
        if view.get("check"):
            check = dict(view["check"])
            check["message"] = {
                "recipient_not_allowed": "허용되지 않은 수신처입니다",
                "pii_blocked_by_team_policy": "개인정보를 제거해 주세요",
                "pii_masked": "개인정보를 가렸습니다",
                "scope_wider_than_evidence": "수신처의 열람 범위가 근거보다 넓습니다",
                "evidence_scope_unknown": "근거의 열람 범위를 확인할 수 없습니다",
                "ticket_not_found": "티켓을 찾을 수 없습니다",
            }.get(check.get("reason"), "결정론 검사를 통과했습니다")
            view["check"] = check
        if approval:
            view["requester_label"] = state["user_id"]
        if state["status"] == "interrupted":
            view.update(
                retry_hint="다시 요청해 주세요",
                effect_result=state.get("effect_result") or "unknown",
            )
        return view

    async def _expire(self, task_id, state):
        if (
            state["status"] == "awaiting_approval"
            and state.get("expires_at")
            and self.clock() >= datetime.fromisoformat(state["expires_at"])
        ):
            # The graph records approval.expired and closes the interrupt without sending.
            state = await self.graph.ainvoke(
                Command(resume={"decision": "expired"}),
                self._config(task_id),
                context=AgentContext(state["user_id"]),
            )
        return state

    async def get(self, user_id: str, task_id: str) -> dict | None:
        if not _TASK_ID.fullmatch(task_id):
            return None
        async with self._lock(task_id):
            state = await get_task(self.graph, task_id)
            if not state:
                return None
            owner = state["user_id"] == user_id
            if not owner and not (
                state["status"] == "awaiting_approval"
                and self._checked(state)
                and self._decision(state, user_id) == Decision.ALLOW
            ):
                return None
            state = await self._expire(task_id, state)
            if not owner and state["status"] != "awaiting_approval":
                return None
            return self._view(state, task_id, approval=not owner)

    async def approve(self, user_id, task_id, *, decision, draft_sha256, edited=None):
        if not _TASK_ID.fullmatch(task_id):
            raise ApprovalError(404)
        async with self._lock(task_id):
            state = await get_task(self.graph, task_id)
            if not state:
                raise ApprovalError(404)
            permission = self._decision(state, user_id)
            if permission != Decision.ALLOW:
                raise ApprovalError(403 if permission == Decision.FORBIDDEN else 404)
            receipt = state.get("approval_receipt")
            if receipt:
                if (receipt["draft_sha256"], receipt["decision"]) == (
                    draft_sha256,
                    decision,
                ):
                    return 200, receipt["view"]
                raise ApprovalError(409)
            state = await self._expire(task_id, state)
            if (
                state["status"] != "awaiting_approval"
                or state["draft_sha256"] != draft_sha256
            ):
                raise ApprovalError(409)
            if not self._checked(state):
                raise ApprovalError(409)
            if decision == "edit_approve":
                try:
                    apply_edit(Draft(**state["draft"]), edited)
                except (InvalidEdit, TypeError, ValueError):
                    raise ApprovalError(422) from None
            value = {
                "approver": user_id,
                "decision": decision,
                "draft_sha256": draft_sha256,
            }
            if edited is not None:
                value["edited"] = edited
            with trace(task_id, user_id=state["user_id"]):
                state = await self.graph.ainvoke(
                    Command(resume=value),
                    self._config(task_id),
                    context=AgentContext(state["user_id"]),
                )
            view = self._view(state, task_id, approval=user_id != state["user_id"])
            await self._update(
                task_id,
                {
                    "approval_receipt": {
                        "draft_sha256": draft_sha256,
                        "decision": decision,
                        "view": view,
                    }
                },
            )
            return 202, view

    async def startup_cleanup(self):
        seen = set()
        async for checkpoint in self.graph.checkpointer.alist(None):
            task_id = checkpoint.config["configurable"]["thread_id"]
            if task_id in seen:
                continue
            seen.add(task_id)
            state = await get_task(self.graph, task_id)
            if state and state.get("status") == "running":
                await self._update(task_id, {"status": "interrupted"})
        if self.effects is None:
            return
        for row in await self.effects.outbox.list_open():
            state = await get_task(self.graph, row.task_id)
            if not state or not state.get("user_id"):
                continue
            result = await reconcile_effect(
                row.idempotency_key,
                user_id=state["user_id"],
                jira=self.effects.jira,
                slack=self.effects.slack,
                outbox=self.effects.outbox,
                audit=self.audit,
                policy=self.policy,
            )
            await self._update(
                row.task_id,
                {
                    "status": "interrupted",
                    "effect": {
                        "state": result.state.value,
                        "mock_id": result.mock_id,
                        "reason": result.reason,
                    },
                    "effect_result": {
                        OutboxState.SUCCEEDED: "sent",
                        OutboxState.FAILED: "not_sent",
                    }.get(result.state, "unknown"),
                },
            )

    async def aclose(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

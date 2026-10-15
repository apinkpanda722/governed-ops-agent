"""Drive approvals through ASGI; server lifecycle and session TTL are not tested."""

import asyncio
import math
import time
from dataclasses import dataclass

import httpx

from govops.agent.approval import ANNOUNCE_APPROVER_MIN_GRADE, approval_id
from govops.agent.graph import Users
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.eval.observation import ApprovalRecord
from govops.eval.spec import TaskSpec

_TERMINAL = frozenset(
    {"completed", "failed", "rejected", "expired", "interrupted", "blocked"}
)
_BODY_FIELD = {
    "slack_send_message": "text",
    "jira_add_comment": "body",
    "jira_create_followup_ticket": "description",
}
# Actor candidates: exact grade 4, exclude requester, then label lexical order.
# Different-team candidates also use label lexical order (no grade restriction).
_APPROVER_GRADE = 4


@dataclass(frozen=True)
class DriveResult:
    task_id: str
    view: dict
    approvals: list[ApprovalRecord]
    plan_remaining: int
    unexpected_http: list[str]
    timed_out: bool


class ApprovalDriver:
    def __init__(
        self,
        service: TaskService,
        users: Users,
        *,
        sleep=asyncio.sleep,
        poll_interval: float = 0.5,
        max_wait: float = 180.0,
    ):
        if (
            not math.isfinite(poll_interval)
            or poll_interval <= 0
            or not math.isfinite(max_wait)
            or max_wait < 0
        ):
            raise ValueError("invalid_poll_budget")
        self.service, self.users = service, users
        self.sleep, self.poll_interval, self.max_wait = sleep, poll_interval, max_wait

    def _actor(self, requester: str, actor: str) -> str:
        if actor == "requester":
            return requester
        owner = self.users[requester]
        if actor == "same_team_eligible":
            candidates = [
                user.label
                for user in self.users.values()
                if user.team == owner.team
                and user.grade >= ANNOUNCE_APPROVER_MIN_GRADE
                and user.label != requester
            ]
            if not candidates:
                raise ValueError("no_approver")
            return min(candidates)
        candidates = [
            user.label
            for user in self.users.values()
            if (
                user.team == owner.team
                and user.grade == _APPROVER_GRADE
                and user.label != requester
                if actor == "same_team_g4"
                else user.team != owner.team
            )
        ]
        if not candidates and actor == "same_team_g4":
            # No other grade-4 user (e.g. the requester is the team's only one):
            # fall back to the policy rule used by same_team_eligible.
            candidates = [
                user.label
                for user in self.users.values()
                if user.team == owner.team
                and user.grade >= ANNOUNCE_APPROVER_MIN_GRADE
                and user.label != requester
            ]
        if not candidates:
            raise ValueError("no_approver")
        return min(candidates)

    async def run(self, spec: TaskSpec) -> DriveResult:
        # Resolve before starting a task so an invalid plan leaves no running task.
        actors = [self._actor(spec.user, step.actor) for step in spec.approval_script]
        sessions = SessionStore()
        app = create_app(
            users=frozenset(self.users),
            sessions=sessions,
            demo_login=False,
            service=self.service,
        )
        approvals: list[ApprovalRecord] = []
        unexpected: list[str] = []
        index = 0
        waited = 0.0
        started = time.monotonic()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic"
        ) as client:

            async def request(user, method, path, **kwargs):
                token, _ = sessions.issue(user)
                try:
                    return await client.request(
                        method,
                        path,
                        headers={"Authorization": "Bearer " + token},
                        **kwargs,
                    )
                except Exception:  # noqa: BLE001 — suppress request-bearing exception details
                    raise RuntimeError("driver_request_failed") from None

            created = await request(
                spec.user, "POST", "/tasks", json={"request": spec.request}
            )
            if created.status_code != 202:
                raise RuntimeError("task_start_failed")
            task_id = created.json()["task_id"]
            path = f"/tasks/{task_id}"
            while True:
                response = await request(spec.user, "GET", path)
                if response.status_code != 200:
                    raise RuntimeError("task_read_failed")
                view = response.json()
                if view["status"] in _TERMINAL:
                    break
                if view["status"] == "awaiting_approval":
                    if index == len(spec.approval_script):
                        break
                    step, actor = spec.approval_script[index], actors[index]
                    body = {
                        "decision": step.decision,
                        "draft_sha256": view["draft_sha256"],
                    }
                    if step.decision == "edit_approve":
                        actor_view = await request(actor, "GET", path)
                        if actor_view.status_code != 200:
                            raise RuntimeError("approval_view_unavailable")
                        draft_view = actor_view.json()
                        body["draft_sha256"] = draft_view["draft_sha256"]
                        draft = draft_view["draft"]
                        field = _BODY_FIELD[draft["tool"]]
                        body["edited"] = {
                            field: draft["args"].get(field, "")
                            + "\n"
                            + (step.edit_append or "")
                        }
                    decided = await request(
                        actor, "POST", path + "/approval", json=body
                    )
                    actual = decided.status_code
                    approvals.append(
                        ApprovalRecord(
                            approval_id=approval_id(task_id, body["draft_sha256"]),
                            actor=actor,
                            decision=step.decision,
                            http_status=actual,
                        )
                    )
                    if (
                        actual != step.expect_http
                        if step.expect_http is not None
                        else not 200 <= actual < 300
                    ):
                        expected = (
                            str(step.expect_http)
                            if step.expect_http is not None
                            else "2xx"
                        )
                        unexpected.append(f"{index}:{expected}!={actual}")
                    index += 1
                    continue
                remaining = self.max_wait - max(waited, time.monotonic() - started)
                if remaining <= 0:
                    return DriveResult(
                        task_id, view, approvals, len(actors) - index, unexpected, True
                    )
                delay = min(self.poll_interval, remaining)
                await self.sleep(delay)
                waited += delay
        return DriveResult(
            task_id, view, approvals, len(actors) - index, unexpected, False
        )

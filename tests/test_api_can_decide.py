import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.graph import WITH_WRITES, EffectDeps, build_graph, get_task
from govops.agent.scope import ScopeIndex
from govops.agent.users import UserInfo
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.audit.log import AuditLog
from govops.guardrails.policy import TeamPolicy
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy

REQUESTER, APPROVER, JUNIOR = "demo-requester-g3", "demo-approver-g3", "demo-junior-g3"


class Slack:
    def __init__(self):
        self.sent = []

    async def send_message(self, user_id, **args):
        self.sent.append((user_id, args))
        return {"message": {"message_id": "message-1"}}


async def waiting(tmp_path, channel):
    users = {
        u.label: u
        for u in (
            UserInfo(REQUESTER, "platform", 6),
            UserInfo(APPROVER, "platform", 4),
            UserInfo(JUNIOR, "platform", 3),
        )
    }
    tools = FakeTools()
    tools.policy = ToolPolicy()
    slack = Slack()
    graph = build_graph(
        FakeChat(
            reply(
                calls=[
                    call(
                        "slack_send_message",
                        json.dumps({"channel": channel, "text": "합성 안내"}),
                    )
                ]
            )
        ),
        tools,
        InMemorySaver(),
        profile=WITH_WRITES,
        effects=EffectDeps(
            SimpleNamespace(add_comment=None, create_followup=None),
            slack,
            InMemoryOutboxStore(),
        ),
        audit=AuditLog(tmp_path),
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({"platform": "block"}),
        users=users,
        clock=lambda: datetime(2026, 10, 5, tzinfo=UTC),
    )
    service = TaskService(graph, users=users)
    ident = await service.start(REQUESTER, "합성 안내 발송")
    await asyncio.gather(*service._tasks)
    return service, graph, slack, users, ident


@async_test
async def test_announce_owner_cannot_decide_but_same_team_approver_can(tmp_path):
    service, _, _, _, ident = await waiting(tmp_path, "announce")
    owner_task = await service.get(REQUESTER, ident)
    assert owner_task["status"] == "awaiting_approval"
    assert owner_task["can_decide"] is False
    conversation = await service.conversation_view(REQUESTER, ident)
    assert conversation["turn"]["can_decide"] is False
    approver_task = await service.get(APPROVER, ident)
    assert approver_task["can_decide"] is True
    inbox = (await service.approvals(APPROVER))["approvals"]
    assert [row["can_decide"] for row in inbox] == [True]
    assert (await service.approvals(REQUESTER))["approvals"] == []
    await service.aclose()


@async_test
async def test_non_announce_owner_can_decide(tmp_path):
    service, _, _, _, ident = await waiting(tmp_path, "team-platform")
    assert (await service.get(REQUESTER, ident))["can_decide"] is True
    conversation = await service.conversation_view(REQUESTER, ident)
    assert conversation["turn"]["can_decide"] is True
    inbox = (await service.approvals(REQUESTER))["approvals"]
    assert [row["can_decide"] for row in inbox] == [True]
    await service.aclose()


@async_test
async def test_can_decide_follows_the_policy_function_for_every_viewer(tmp_path):
    from govops.agent.approval import Decision, approver_decision
    from govops.agent.draft import Draft

    service, graph, _, users, ident = await waiting(tmp_path, "announce")
    state = await get_task(graph, ident)
    draft = Draft(**state["draft"])
    for label in (REQUESTER, APPROVER):
        expected = (
            approver_decision(draft, requester=users[REQUESTER], approver=users[label])
            == Decision.ALLOW
        )
        assert (await service.get(label, ident))["can_decide"] is expected
    await service.aclose()


@async_test
async def test_forged_can_decide_does_not_remove_the_server_403(tmp_path):
    service, _, slack, users, ident = await waiting(tmp_path, "announce")
    view = await service.get(REQUESTER, ident)
    body = {"decision": "approve", "draft_sha256": view["draft_sha256"]}
    sessions = SessionStore()
    token = sessions.issue(REQUESTER)[0]
    app = create_app(
        users=frozenset(users), sessions=sessions, demo_login=False, service=service
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        for payload in (body, {**body, "can_decide": True}):
            response = await client.post(
                f"/tasks/{ident}/approval",
                json=payload,
                headers={"Authorization": "Bearer " + token},
            )
            assert response.status_code in (403, 422)
        assert (
            await client.post(
                f"/tasks/{ident}/approval",
                json=body,
                headers={"Authorization": "Bearer " + token},
            )
        ).status_code == 403
    assert slack.sent == []
    assert (await service.get(REQUESTER, ident))["status"] == "awaiting_approval"
    await service.aclose()

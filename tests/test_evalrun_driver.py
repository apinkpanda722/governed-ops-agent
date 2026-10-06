"""Approval scripts through real API/graph and in-process mock services."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply
from test_effects_matrix import setup as effect_setup  # noqa: F401

from govops.agent.approval import approval_id
from govops.agent.graph import WITH_WRITES, EffectDeps, build_graph
from govops.agent.scope import AccessRow, ScopeIndex
from govops.agent.users import UserInfo
from govops.api.tasks import TaskService
from govops.eval.spec import ApprovalStep, TaskSpec
from govops.evalrun.driver import ApprovalDriver
from govops.guardrails.policy import TeamPolicy
from govops.tools.jira import JiraClient
from govops.tools.resilience import ToolPolicy
from govops.tools.slack import SlackClient


def spec(user, steps):
    raw = json.loads(Path("data/eval/tasks.json").read_text())[0]
    return TaskSpec.model_validate(raw).model_copy(
        update={"user": user, "approval_script": steps}
    )


def step(actor="requester", decision="approve", expect_http=None):
    return ApprovalStep(
        actor=actor,
        decision=decision,
        expect_http=expect_http,
        edit_append="[승인자 수정] 합성 수정" if decision == "edit_approve" else None,
    )


async def yield_only(_):
    await asyncio.sleep(0)


async def real_tick(_):
    # 드라이버의 폴링 대기용. 가상 대기(poll_interval)는 즉시 흐르지만 그래프는 스레드·I/O를 거치므로
    # 실제 시간이 조금 흘러야 진행된다. yield 한 번(sleep(0))이면 느린 환경에서 가상 시간이 먼저 소진된다.
    await asyncio.sleep(0.001)


@async_test
@pytest.mark.parametrize("decision", ["approve", "reject", "edit_approve"])
async def test_real_effect_scripts(effect_setup, decision):  # noqa: F811
    s = effect_setup
    users = {
        u.label: u
        for u in (
            UserInfo("demo-platform-g3", "platform", 3),
            UserInfo("demo-approver-g3", "platform", 4),
            UserInfo("demo-other-g3", "finance", 4),
        )
    }
    actor = "same_team_g4" if s["service"] == "slack" else "requester"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=s["app"])) as http:
        tools = FakeTools()
        tools.policy = ToolPolicy(sleep=yield_only)
        effects = EffectDeps(
            JiraClient("http://synthetic", http_client=http),
            SlackClient("http://synthetic", http_client=http),
            s["outbox"],
        )
        args = dict(s["req"].args)
        if s["req"].tool == "jira_create_followup_ticket":
            args["description"] = "합성 본문"
        graph = build_graph(
            FakeChat(reply(calls=[call(s["req"].tool, json.dumps(args))])),
            tools,
            InMemorySaver(),
            profile=WITH_WRITES,
            effects=effects,
            audit=s["audit"],
            scope=ScopeIndex(
                {}, {s["target"]: AccessRow("public", (), 0)}, tuple(users.values())
            ),
            policy=TeamPolicy({"platform": "block"}),
            users=users,
        )
        service = TaskService(graph, users=users)
        try:
            result = await ApprovalDriver(
                service, users, sleep=real_tick, poll_interval=0.001
            ).run(spec("demo-platform-g3", [step(actor, decision)]))
            assert result.view["status"] == (
                "rejected" if decision == "reject" else "completed"
            )
            assert result.plan_remaining == 0 and not result.timed_out
            assert not result.unexpected_http
            record = result.approvals[0]
            assert record.actor == (
                "demo-approver-g3" if actor == "same_team_g4" else "demo-platform-g3"
            )
            assert record.http_status == 202
            rows = (
                s["ss"].report(bodies=True)["channels"]
                if s["service"] == "slack"
                else None
            )
            if rows is not None:
                bodies = [m["text"] for c in rows for m in c["messages"]]
            elif s["req"].tool == "jira_add_comment":
                bodies = [r["body"] for r in s["js"].report(bodies=True)["comments"]]
            else:
                bodies = [
                    r["description"] for r in s["js"].report(bodies=True)["children"]
                ]
            assert len(bodies) == (0 if decision == "reject" else 1)
            if decision == "edit_approve":
                assert bodies[0].endswith("\n[승인자 수정] 합성 수정")
            assert "Bearer" not in repr(result) and "Authorization" not in repr(result)
            if decision != "edit_approve":
                assert record.approval_id == approval_id(
                    result.task_id, result.view["draft_sha256"]
                )
        finally:
            await service.aclose()


@async_test
async def test_denials_remaining_and_blocked(tmp_path):
    from govops.audit.log import AuditLog
    from govops.tools.outbox import InMemoryOutboxStore

    users = {
        u.label: u
        for u in (
            UserInfo("demo-requester-g3", "platform", 3),
            UserInfo("demo-z-g4", "platform", 4),
            UserInfo("demo-a-g4", "platform", 4),
            UserInfo("demo-other-g4", "finance", 4),
        )
    }
    for channel, plan, expected, remaining in (
        (
            "announce",
            [
                step("requester", expect_http=403),
                step("other_team_user", expect_http=404),
                step("same_team_g4", "reject"),
            ],
            "rejected",
            0,
        ),
        ("announce", [], "awaiting_approval", 0),
        ("team-finance", [step()], "blocked", 1),
        ("team-finance", [], "blocked", 0),
        ("announce", [step("same_team_g4", "reject"), step()], "rejected", 1),
        ("announce", [step("requester", expect_http=200)], "awaiting_approval", 0),
    ):
        tools = FakeTools()
        tools.policy = ToolPolicy(sleep=yield_only)
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
                SimpleNamespace(), SimpleNamespace(), InMemoryOutboxStore()
            ),
            audit=AuditLog(tmp_path),
            scope=ScopeIndex({}, {}, tuple(users.values())),
            policy=TeamPolicy({"platform": "block"}),
            users=users,
        )
        service = TaskService(graph, users=users)
        try:
            result = await ApprovalDriver(
                service, users, sleep=real_tick, poll_interval=0.001
            ).run(spec("demo-requester-g3", plan))
            assert (
                result.view["status"] == expected and result.plan_remaining == remaining
            )
            if len(plan) == 3:
                assert [r.http_status for r in result.approvals] == [403, 404, 202]
                assert result.approvals[-1].actor == "demo-a-g4"
            if plan and plan[0].expect_http == 200:
                assert result.unexpected_http == ["0:200!=403"]
            assert not result.timed_out
        finally:
            await service.aclose()


@async_test
async def test_timeout_and_no_approver():
    users = {"demo-requester-g3": UserInfo("demo-requester-g3", "platform", 3)}

    class Held:
        async def complete(self, **kwargs):
            await asyncio.Event().wait()

    service = TaskService(build_graph(Held(), FakeTools(), InMemorySaver()))
    try:
        result = await ApprovalDriver(
            service, users, sleep=yield_only, max_wait=1, poll_interval=0.5
        ).run(spec("demo-requester-g3", [step()]))
        assert result.timed_out and result.plan_remaining == 1
        with pytest.raises(ValueError, match="^no_approver$"):
            await ApprovalDriver(
                service, users, sleep=real_tick, poll_interval=0.001
            ).run(spec("demo-requester-g3", [step("same_team_g4")]))
    finally:
        await service.aclose()


def test_actor_selection_exact_grade_and_order():
    users = {
        u.label: u
        for u in (
            UserInfo("demo-requester-g3", "platform", 4),
            UserInfo("a-senior", "platform", 5),
            UserInfo("z-peer", "platform", 4),
            UserInfo("b-peer", "platform", 4),
            UserInfo("z-other", "hr", 1),
            UserInfo("a-other", "finance", 2),
        )
    }
    driver = ApprovalDriver(SimpleNamespace(), users)
    assert driver._actor("demo-requester-g3", "requester") == "demo-requester-g3"
    assert driver._actor("demo-requester-g3", "same_team_g4") == "b-peer"
    assert driver._actor("demo-requester-g3", "other_team_user") == "a-other"
    without_peers = {
        key: value for key, value in users.items() if not key.endswith("peer")
    }
    with pytest.raises(ValueError, match="^no_approver$"):
        ApprovalDriver(SimpleNamespace(), without_peers)._actor(
            "demo-requester-g3", "same_team_g4"
        )


@async_test
async def test_session_tokens_absent_from_result_checkpoint_and_logs(
    monkeypatch, caplog
):
    from govops.api.sessions import SessionStore

    issued = []
    original = SessionStore.issue

    def capture(self, user):
        result = original(self, user)
        issued.append(result[0])
        return result

    monkeypatch.setattr(SessionStore, "issue", capture)
    users = {"demo-requester-g3": UserInfo("demo-requester-g3", "platform", 3)}
    saver = InMemorySaver()
    service = TaskService(build_graph(FakeChat(reply("합성 답변")), FakeTools(), saver))
    try:
        result = await ApprovalDriver(
            service, users, sleep=real_tick, poll_interval=0.001
        ).run(spec("demo-requester-g3", [step()]))
        assert result.view["status"] == "completed" and result.plan_remaining == 1
        assert issued
        public = repr(result) + repr(saver.storage) + caplog.text
        assert all(token not in public for token in issued)
    finally:
        await service.aclose()

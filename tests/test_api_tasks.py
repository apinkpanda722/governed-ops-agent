import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.graph import build_graph
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.tools.registry import ToolOutcome


@async_test
async def test_tasks_identity_fields_and_secrecy(caplog):
    caplog.set_level(logging.DEBUG)
    gate = asyncio.Event()
    chat = FakeChat(reply(calls=[call()]), reply("[syn-one] 답변"))
    tools = FakeTools(ToolOutcome({}, False, ("syn-one", "syn-unused")))

    class HeldChat:
        async def complete(self, **kwargs):
            await gate.wait()
            return await chat.complete(**kwargs)

    saver = InMemorySaver()
    service = TaskService(build_graph(HeldChat(), tools, saver))
    sessions = SessionStore()
    app = create_app(
        users=frozenset({"a", "b"}), sessions=sessions, demo_login=True, service=service
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        token = (await client.post("/sessions", json={"user": "a"})).json()[
            "session_token"
        ]
        other, _ = sessions.issue("b")
        headers = {"Authorization": "Bearer " + token}
        created = await client.post(
            "/tasks",
            json={"request": "조회", "user": "b", "team": "x", "grade": 9},
            headers=headers,
        )
        assert created.status_code == 202
        task_id = created.json()["task_id"]
        running = await client.get("/tasks/" + task_id, headers=headers)
        assert running.json()["status"] == "running"
        hidden = [
            await client.get(
                "/tasks/" + ident, headers={"Authorization": "Bearer " + other}
            )
            for ident in (task_id, uuid4().hex, "invalid")
        ]
        assert all(
            (r.status_code, r.content, dict(r.headers))
            == (hidden[0].status_code, hidden[0].content, dict(hidden[0].headers))
            for r in hidden
        )
        assert hidden[0].status_code == 404
        for value in ("", "  ", "x" * 2001):
            assert (
                await client.post("/tasks", json={"request": value}, headers=headers)
            ).status_code == 422
        expired = SessionStore(ttl_seconds=0)
        expired_token, _ = expired.issue("a")
        sessions._sessions.update(expired._sessions)
        for auth in (
            {},
            {"Authorization": "Bearer unknown"},
            {"Authorization": "Bearer " + expired_token},
        ):
            assert (
                await client.post("/tasks", json={"request": "x"}, headers=auth)
            ).status_code == 401
            assert (
                await client.get("/tasks/" + task_id, headers=auth)
            ).status_code == 401
        gate.set()
        for _ in range(100):
            result = await client.get("/tasks/" + task_id, headers=headers)
            if result.json()["status"] != "running":
                break
            await asyncio.sleep(0.001)
        assert result.json() == {
            "task_id": task_id,
            "status": "completed",
            "answer": "[syn-one] 답변",
            "cited_doc_ids": ["syn-one"],
            "observed_doc_ids": ["syn-one", "syn-unused"],
            "steps": 2,
            "error": None,
        }
        assert tools.calls[0][2] == "a"
        public = (
            repr(
                (
                    created.content,
                    created.headers,
                    result.content,
                    result.headers,
                    saver.storage,
                    chat.calls,
                )
            )
            + caplog.text
        )
        assert token not in public
        # persisted ownership works without the pre-start owner table
        fresh = TaskService(service.graph)
        assert await fresh.get("a", task_id) == result.json()
        assert await fresh.get("b", task_id) is None
    await service.aclose()


@async_test
async def test_background_failure_concurrency_and_close(caplog):
    caplog.set_level(logging.DEBUG)

    class Chat:
        async def complete(self, **kwargs):
            request = kwargs["messages"][-1]["content"]
            if request == "fail":
                raise RuntimeError("EXCEPTION-SECRET")
            if request == "crash":
                # 모델 노드의 예외 처리 밖에서 그래프가 깨지는 경우(잘못된 응답 객체)
                return SimpleNamespace(message=None)
            if request == "wait":
                await asyncio.Event().wait()
            return reply(request)

    service = TaskService(build_graph(Chat(), FakeTools(), InMemorySaver()))
    a = await service.start("a", "crash")
    b = await service.start("b", "answer")
    c = await service.start("c", "fail")
    for _ in range(100):
        first, second, third = (
            await service.get("a", a),
            await service.get("b", b),
            await service.get("c", c),
        )
        if (
            first["status"] == "failed"
            and second["status"] == "completed"
            and third["status"] == "failed"
        ):
            break
        await asyncio.sleep(0.001)
    assert first["error"] == "internal_error"
    # 모델 호출의 분류되지 않은 예외는 그래프가 model_error로 종료한다.
    assert third["error"] == "model_error"
    assert second["answer"] == "answer"
    assert "EXCEPTION-SECRET" not in repr(first) + repr(third) + caplog.text
    await service.start("a", "wait")
    await asyncio.sleep(0)
    await service.aclose()
    assert not service._tasks


@async_test
async def test_initial_checkpoint_and_restart_cleanup():
    gate = asyncio.Event()

    class Held:
        async def complete(self, **kwargs):
            await gate.wait()
            return reply("합성 답변")

    graph = build_graph(Held(), FakeTools(), InMemorySaver())
    service = TaskService(graph)
    ident = await service.start("a", "합성 요청")
    state = (await graph.aget_state({"configurable": {"thread_id": ident}})).values
    assert state["user_id"] == "a" and state["status"] == "running"
    assert state["created_at"]
    await service.aclose()
    await graph.aupdate_state(
        {"configurable": {"thread_id": ident}}, {"status": "running"}, as_node="model"
    )
    fresh = TaskService(graph)
    await fresh.startup_cleanup()
    result = await fresh.get("a", ident)
    assert result["status"] == "interrupted"
    assert result["retry_hint"] == "다시 요청해 주세요"
    assert result["effect_result"] == "unknown"
    assert await fresh.get("b", ident) is None


@async_test
async def test_approval_api_policy_idempotency_expiry_and_views(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta

    from govops.agent.graph import WITH_WRITES, EffectDeps
    from govops.agent.scope import ScopeIndex
    from govops.agent.users import UserInfo
    from govops.audit.log import AuditLog
    from govops.guardrails.policy import TeamPolicy
    from govops.tools.outbox import InMemoryOutboxStore
    from govops.tools.resilience import ToolPolicy

    users = {
        u.label: u
        for u in (
            UserInfo("requester", "platform", 6),
            UserInfo("approver", "platform", 4),
            UserInfo("junior", "platform", 3),
            UserInfo("other", "finance", 4),
        )
    }
    now = [datetime(2026, 10, 5, tzinfo=UTC)]

    class Slack:
        def __init__(self):
            self.sent = []

        async def send_message(self, user_id, **args):
            self.sent.append((user_id, args))
            return {"message": {"message_id": "message-1"}}

    slack = Slack()
    tools = FakeTools()
    tools.policy = ToolPolicy()
    saver = InMemorySaver()
    effects = EffectDeps(
        SimpleNamespace(add_comment=None, create_followup=None),
        slack,
        InMemoryOutboxStore(),
    )
    audit = AuditLog(tmp_path)
    graph = build_graph(
        FakeChat(
            *[
                reply(
                    calls=[
                        call(
                            "slack_send_message",
                            json.dumps({"channel": "announce", "text": "합성 안내"}),
                        )
                    ]
                )
                for _ in range(8)
            ]
        ),
        tools,
        saver,
        profile=WITH_WRITES,
        effects=effects,
        audit=audit,
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({"platform": "block"}),
        users=users,
        clock=lambda: now[0],
    )
    service = TaskService(
        graph, users=users, effects=effects, audit=audit, policy=tools.policy
    )
    sessions = SessionStore()
    tokens = {u: sessions.issue(u)[0] for u in users}
    app = create_app(
        users=frozenset(users), sessions=sessions, demo_login=False, service=service
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:

        async def request(user, method, path, **kwargs):
            return await client.request(
                method,
                path,
                headers={"Authorization": "Bearer " + tokens[user]},
                **kwargs,
            )

        async def waiting():
            ident = await service.start("requester", "합성 공지")
            await asyncio.gather(*service._tasks)
            view = await service.get("requester", ident)
            assert view["status"] == "awaiting_approval"
            return ident, {"decision": "approve", "draft_sha256": view["draft_sha256"]}

        ident, body = await waiting()
        path = f"/tasks/{ident}"
        view = await request("approver", "GET", path)
        assert view.status_code == 200
        assert view.json()["requester_label"] == "requester"
        assert (
            not {
                "answer",
                "cited_doc_ids",
                "observed_doc_ids",
                "messages",
                "evidence_ids",
            }
            & view.json().keys()
        )
        for user, code in (("requester", 403), ("junior", 404), ("other", 404)):
            assert (
                await request(user, "POST", path + "/approval", json=body)
            ).status_code == code
        for user in ("junior", "other"):
            assert (await request(user, "GET", path)).status_code == 404
        assert (
            await request(
                "approver",
                "POST",
                path + "/approval",
                json={**body, "draft_sha256": "0" * 64},
            )
        ).status_code == 409
        for invalid in (
            {**body, "user_id": "requester"},
            {**body, "edited": {"text": "수정"}},
            {
                **body,
                "decision": "edit_approve",
                "edited": {"channel": "team-platform"},
            },
        ):
            assert (
                await request("approver", "POST", path + "/approval", json=invalid)
            ).status_code == 422
        responses = await asyncio.gather(
            *[
                request("approver", "POST", path + "/approval", json=body)
                for _ in range(2)
            ]
        )
        assert (
            responses[0].status_code in (200, 202) and responses[1].status_code == 200
        )
        assert responses[0].json() == responses[1].json()
        for _ in range(2):
            again = await request("approver", "POST", path + "/approval", json=body)
            assert again.status_code == 200 and again.json() == responses[0].json()
        assert len(slack.sent) == 1 and slack.sent[0][0] == "requester"
        assert (await request("approver", "GET", path)).status_code == 404
        assert (
            await request(
                "approver",
                "POST",
                path + "/approval",
                json={**body, "decision": "reject"},
            )
        ).status_code == 409
        fresh = TaskService(
            graph, users=users, effects=effects, audit=audit, policy=tools.policy
        )
        assert (await fresh.approve("approver", ident, **body))[1] == responses[
            0
        ].json()
        ident, body = await waiting()
        await fresh.startup_cleanup()
        assert (await fresh.get("approver", ident))["status"] == "awaiting_approval"
        config = {"configurable": {"thread_id": ident}}
        for check in (
            None,
            {"status": "blocked", "reason": "pii_blocked_by_team_policy"},
        ):
            await graph.aupdate_state(config, {"check": check})
            assert await fresh.get("approver", ident) is None
        await graph.aupdate_state(
            config, {"check": {"status": "passed", "reason": None}}
        )
        now[0] += timedelta(minutes=31)
        assert (
            await request("approver", "POST", f"/tasks/{ident}/approval", json=body)
        ).status_code == 409
        assert (await service.get("requester", ident))["status"] == "expired"
        assert len(slack.sent) == 1
        ident, _ = await waiting()
        now[0] += timedelta(minutes=31)
        assert (await service.get("requester", ident))["status"] == "expired"
        assert await service.get("approver", ident) is None
        ident, body = await waiting()
        edited = {
            **body,
            "decision": "edit_approve",
            "edited": {"text": "수정된 합성 안내"},
        }
        changed = await request(
            "approver", "POST", f"/tasks/{ident}/approval", json=edited
        )
        assert changed.status_code == 202 and changed.json()["status"] == "completed"
        assert slack.sent[-1][1]["text"] == "수정된 합성 안내"
        assert (
            await request("approver", "POST", f"/tasks/{ident}/approval", json=edited)
        ).json() == changed.json()
        ident, body = await waiting()
        rejected = await request(
            "approver",
            "POST",
            f"/tasks/{ident}/approval",
            json={**body, "decision": "reject"},
        )
        assert rejected.status_code == 202 and rejected.json()["status"] == "rejected"
        assert len(slack.sent) == 2
        output = (
            repr(saver.storage)
            + repr(responses[0].json())
            + "".join(p.read_text() for p in tmp_path.glob("*.jsonl"))
        )
        assert all(t not in output for t in tokens.values())
    await service.aclose()


@async_test
@pytest.mark.parametrize(
    "next_decision", ["reject", "edit_approve", "approve", "expire"]
)
@pytest.mark.parametrize("channel", ["team-hr", "announce"])
async def test_blocked_edit_restores_draft_and_allows_next_decision(
    tmp_path, next_decision, channel
):
    import json
    from datetime import UTC, datetime, timedelta

    from govops.agent.approval import approval_id
    from govops.agent.draft import Draft, draft_sha256
    from govops.agent.graph import WITH_WRITES, EffectDeps, get_task
    from govops.agent.scope import ScopeIndex
    from govops.agent.users import UserInfo
    from govops.audit.log import AuditLog
    from govops.guardrails.policy import TeamPolicy
    from govops.tools.outbox import InMemoryOutboxStore
    from govops.tools.resilience import ToolPolicy

    users = {
        u.label: u
        for u in (UserInfo("requester", "hr", 3), UserInfo("approver", "hr", 4))
    }
    actor = "requester" if channel == "team-hr" else "approver"
    now = [datetime(2026, 10, 5, tzinfo=UTC)]

    class Slack:
        def __init__(self):
            self.sent = []

        async def send_message(self, user_id, **args):
            self.sent.append((user_id, args))
            return {"message": {"message_id": "message-1"}}

    slack = Slack()
    tools = FakeTools()
    tools.policy = ToolPolicy()
    audit = AuditLog(tmp_path)
    graph = build_graph(
        FakeChat(
            reply(
                calls=[
                    call(
                        "slack_send_message",
                        json.dumps({"channel": channel, "text": "원래 합성 안내"}),
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
        audit=audit,
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({"hr": "block"}),
        users=users,
        clock=lambda: now[0],
    )
    service = TaskService(graph, users=users)
    sessions = SessionStore()
    tokens = {u: sessions.issue(u)[0] for u in users}
    app = create_app(
        users=frozenset(users), sessions=sessions, demo_login=False, service=service
    )
    ident = await service.start("requester", "합성 안내 발송")
    await asyncio.gather(*service._tasks)
    original = await service.get("requester", ident)
    initial_state = await get_task(graph, ident)
    body = {
        "draft_sha256": original["draft_sha256"],
        "decision": "edit_approve",
        "edited": {"text": "010-1234-5678"},
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:

        async def request(user, method, suffix="", **kw):
            return await client.request(
                method,
                f"/tasks/{ident}{suffix}",
                headers={"Authorization": "Bearer " + tokens[user]},
                **kw,
            )

        now[0] += timedelta(minutes=10)
        blocked = await request(actor, "POST", "/approval", json=body)
        assert (
            blocked.status_code == 202
            and blocked.json()["status"] == "awaiting_approval"
        )
        for user in {actor, "requester"}:
            response = await request(user, "GET")
            assert response.status_code == 200
            view = response.json()
            assert view["draft"] == original["draft"]
            assert view["draft_sha256"] == original["draft_sha256"]
            assert view["check"] == original["check"]
            assert view["expires_at"] == original["expires_at"]
            assert view["rejected_edit"] == {
                "after_sha256": draft_sha256(
                    Draft(
                        "slack_send_message",
                        {"channel": channel, "text": "010-1234-5678"},
                    )
                ),
                "reason": "pii_blocked_by_team_policy",
                "message": "개인정보를 제거해 주세요",
            }
            assert "010-1234-5678" not in json.dumps(view)
        state = await get_task(graph, ident)
        assert state["awaiting_since"] == initial_state["awaiting_since"]
        assert not state.get("approval_receipt")
        assert not slack.sent
        rows = [
            json.loads(line)
            for p in tmp_path.glob("*.jsonl")
            for line in p.read_text().splitlines()
        ]
        edited = next(r for r in rows if r["event"] == "approval.edited")
        assert edited["before_sha256"] == original["draft_sha256"]
        assert edited["after_sha256"] == view["rejected_edit"]["after_sha256"]
        assert any(
            r["event"] == "side_effect.draft"
            and r.get("check_result") == "blocked"
            and r.get("reason") == "pii_blocked_by_team_policy"
            for r in rows
        )
        # Recover a receipt left by the previous API implementation while waiting.
        await graph.aupdate_state(
            {"configurable": {"thread_id": ident}},
            {
                "approval_receipt": {
                    "draft_sha256": original["draft_sha256"],
                    "decision": "edit_approve",
                    "view": blocked.json(),
                }
            },
        )
        if next_decision == "expire":
            now[0] += timedelta(minutes=21)
            assert (await request("requester", "GET")).json()["status"] == "expired"
            assert (
                await request(
                    actor,
                    "POST",
                    "/approval",
                    json={
                        "decision": "reject",
                        "draft_sha256": original["draft_sha256"],
                    },
                )
            ).status_code == 409
            assert not slack.sent
        else:
            next_body = {
                "decision": next_decision,
                "draft_sha256": original["draft_sha256"],
            }
            if next_decision == "edit_approve":
                next_body["edited"] = {"text": "안전한 수정 안내"}
            responses = await asyncio.gather(
                *(request(actor, "POST", "/approval", json=next_body) for _ in range(2))
            )
            assert [r.status_code for r in responses] == [202, 200]
            assert responses[0].json() == responses[1].json()
            result = responses[0].json()
            assert result["status"] == (
                "rejected" if next_decision == "reject" else "completed"
            )
            for _ in range(2):
                again = await request(actor, "POST", "/approval", json=next_body)
                assert again.status_code == 200 and again.json() == result
            different = {
                "draft_sha256": original["draft_sha256"],
                "decision": "approve" if next_decision == "reject" else "reject",
            }
            assert (
                await request(actor, "POST", "/approval", json=different)
            ).status_code == 409
            assert len(slack.sent) == (0 if next_decision == "reject" else 1)
            if slack.sent:
                assert slack.sent[0][0] == "requester"
                assert slack.sent[0][1]["text"] == (
                    "안전한 수정 안내"
                    if next_decision == "edit_approve"
                    else "원래 합성 안내"
                )
                assert slack.sent[0][1]["metadata"]["govops_approval"] == approval_id(
                    ident, result["draft_sha256"]
                )
            if next_decision == "edit_approve":
                assert not result.get("rejected_edit")
    await service.aclose()

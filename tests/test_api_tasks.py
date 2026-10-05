import asyncio
import logging
from types import SimpleNamespace
from uuid import uuid4

import httpx
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

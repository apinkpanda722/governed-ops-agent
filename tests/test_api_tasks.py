import asyncio
import logging
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
            if request == "wait":
                await asyncio.Event().wait()
            return reply(request)

    service = TaskService(build_graph(Chat(), FakeTools(), InMemorySaver()))
    a = await service.start("a", "fail")
    b = await service.start("b", "answer")
    for _ in range(100):
        first, second = await service.get("a", a), await service.get("b", b)
        if first["status"] == "failed" and second["status"] == "completed":
            break
        await asyncio.sleep(0.001)
    assert first["error"] == "internal_error"
    assert second["answer"] == "answer"
    assert "EXCEPTION-SECRET" not in repr(first) + caplog.text
    await service.start("a", "wait")
    await asyncio.sleep(0)
    await service.aclose()
    assert not service._tasks

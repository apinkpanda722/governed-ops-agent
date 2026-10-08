"""Bounded real-server SSE tests with explicit execution gates."""

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeTools, async_test, reply
from test_api_conversations import write_service

from govops.agent.graph import build_graph
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService

SENTINELS = (
    "PRIVATE-REQUEST",
    "PRIVATE-ANSWER",
    "PRIVATE-REASON",
    "syn-private-doc",
    "PRIVATE-DRAFT",
)


class HeldChat:
    def __init__(self, result=None):
        self.gate = asyncio.Event()
        self.result = result or reply(SENTINELS[1])

    async def complete(self, **kwargs):
        await asyncio.wait_for(self.gate.wait(), 10)
        return self.result


@asynccontextmanager
async def harness(serve_asgi, service, **options):
    loop = []

    async def capture():
        loop.append(asyncio.get_running_loop())

    sessions = SessionStore()
    app = create_app(
        users=frozenset(),
        sessions=sessions,
        demo_login=False,
        service=service,
        startup_checks=[capture],
        sse_poll_seconds=0.02,
        **options,
    )
    url = serve_asgi(app)

    async def remote(coro):
        return await asyncio.wait_for(
            asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop[0])), 10
        )

    def auth(user):
        return {"Authorization": "Bearer " + sessions.issue(user)[0]}

    async with httpx.AsyncClient(base_url=url, timeout=5) as client:
        try:
            yield app, client, auth, remote
        finally:
            await remote(service.aclose())


async def frame(lines):
    result = []
    async with asyncio.timeout(5):
        async for line in lines:
            assert all(value not in line for value in SENTINELS)
            if not line:
                return result
            result.append(line)
    return None


async def status(lines):
    async with asyncio.timeout(5):
        value = await frame(lines)
        while value == [": ping"]:
            value = await frame(lines)
    assert value[0] == "event: status"
    data = json.loads(value[1].removeprefix("data: "))
    assert set(data) == {"conversation_id", "turn_id", "status", "seq"}
    return data


async def release(chat):
    chat.gate.set()


@async_test
async def test_snapshots_transitions_new_turn_and_ownership(serve_asgi):
    chat = HeldChat()
    service = TaskService(build_graph(chat, FakeTools(), InMemorySaver()))
    async with harness(serve_asgi, service) as (_, client, auth, remote):
        ids = (
            await client.post(
                "/conversations", headers=auth("a"), json={"request": SENTINELS[0]}
            )
        ).json()
        path = "/conversations/" + ids["conversation_id"] + "/events"
        denied = await client.get(path, headers=auth("b"))
        absent = await client.get(
            "/conversations/" + "f" * 32 + "/events", headers=auth("a")
        )
        assert denied.status_code == absent.status_code == 404
        assert denied.content == absent.content
        assert denied.json() == {"detail": "conversation_not_found"}
        assert denied.headers["content-type"] == "application/json"
        assert (await client.get(path)).status_code == 401
        async with client.stream("GET", path, headers=auth("a")) as response:
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache, no-transform"
            assert response.headers["x-accel-buffering"] == "no"
            lines = response.aiter_lines()
            first = await status(lines)
            assert first == {**ids, "status": "running", "seq": 0}
            await remote(release(chat))
            last = await status(lines)
            assert last == {**ids, "status": "completed", "seq": 1}
            assert await frame(lines) is None

        async def reset():
            chat.gate.clear()

        await remote(reset())
        second = (
            await client.post(
                "/conversations/" + ids["conversation_id"] + "/turns",
                headers=auth("a"),
                json={"request": "다음 합성 요청"},
            )
        ).json()
        assert second["turn_id"] != ids["turn_id"]
        async with client.stream("GET", path, headers=auth("a")) as response:
            lines = response.aiter_lines()
            assert await status(lines) == {**second, "status": "running", "seq": 0}
            await remote(release(chat))
            assert await status(lines) == {**second, "status": "completed", "seq": 1}
            assert await frame(lines) is None


@async_test
async def test_write_heartbeat_approval_and_expiry(serve_asgi, tmp_path):
    service, _, slack, now, users, _, _, _ = write_service(tmp_path)
    owner, approver, *_ = users
    # Gate the configured fake model without changing the graph's contracts.
    from test_agent_graph import FakeChat

    chat = HeldChat()
    original_complete = FakeChat.complete

    async def held_complete(self, **kwargs):
        await asyncio.wait_for(chat.gate.wait(), 10)
        return await original_complete(self, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FakeChat, "complete", held_complete)
        async with harness(serve_asgi, service, sse_heartbeat_seconds=0.04) as (
            _,
            client,
            auth,
            remote,
        ):
            ids = (
                await client.post(
                    "/conversations",
                    headers=auth(owner),
                    json={"request": SENTINELS[0]},
                )
            ).json()
            path = "/conversations/" + ids["conversation_id"] + "/events"
            async with client.stream("GET", path, headers=auth(owner)) as response:
                lines = response.aiter_lines()
                assert (await status(lines))["status"] == "running"
                await remote(release(chat))
                pending = await status(lines)
                assert (pending["status"], pending["seq"]) == ("awaiting_approval", 1)
                assert await frame(lines) == [": ping"]
                gate = asyncio.Event()
                send = slack.send_message

                async def held_send(*args, **kwargs):
                    await asyncio.wait_for(gate.wait(), 10)
                    return await send(*args, **kwargs)

                slack.send_message = held_send
                view = (
                    await client.get("/tasks/" + ids["turn_id"], headers=auth(approver))
                ).json()
                approval = asyncio.create_task(
                    client.post(
                        "/tasks/" + ids["turn_id"] + "/approval",
                        headers=auth(approver),
                        json={
                            "decision": "approve",
                            "draft_sha256": view["draft_sha256"],
                        },
                    )
                )
                try:
                    running = await status(lines)
                    assert (running["status"], running["seq"]) == ("running", 2)

                    async def release_effect():
                        gate.set()

                    await remote(release_effect())
                    assert (await asyncio.wait_for(approval, 5)).status_code == 202
                    completed = await status(lines)
                    assert (completed["status"], completed["seq"]) == ("completed", 3)
                    assert await frame(lines) is None
                finally:
                    approval.cancel()
                    await asyncio.gather(approval, return_exceptions=True)
            second = await remote(
                service.add_turn(owner, ids["conversation_id"], "합성 공지")
            )

            async def settled():
                await asyncio.gather(*service._tasks)
                now[0] += timedelta(minutes=31)

            await remote(settled())
            async with client.stream("GET", path, headers=auth(owner)) as response:
                lines = response.aiter_lines()
                assert await status(lines) == {**second, "status": "expired", "seq": 0}
                assert await frame(lines) is None


@pytest.mark.parametrize("disconnect", [False, True])
@async_test
async def test_bounded_stream_cleanup(serve_asgi, disconnect):
    chat = HeldChat()
    service = TaskService(build_graph(chat, FakeTools(), InMemorySaver()))
    async with harness(
        serve_asgi,
        service,
        sse_max_seconds=1800 if disconnect else 0.08,
        sse_heartbeat_seconds=0.02,
    ) as (app, client, auth, remote):
        ids = (
            await client.post(
                "/conversations", headers=auth("a"), json={"request": "합성 요청"}
            )
        ).json()
        async with client.stream(
            "GET",
            "/conversations/" + ids["conversation_id"] + "/events",
            headers=auth("a"),
        ) as response:
            lines = response.aiter_lines()
            assert (await status(lines))["seq"] == 0
            assert app.state.sse_active_streams == 1
            if not disconnect:
                async with asyncio.timeout(5):
                    while await frame(lines) is not None:
                        pass

        async def cleaned():
            async with asyncio.timeout(5):
                while app.state.sse_active_streams:
                    await asyncio.sleep(0)

        await remote(cleaned())
        assert app.state.sse_active_streams == 0
        await remote(release(chat))


@pytest.mark.parametrize(
    "terminal", ["failed", "blocked_input", "rejected", "interrupted"]
)
@async_test
async def test_terminal_snapshot_is_content_free(serve_asgi, terminal):
    chat = HeldChat()
    service = TaskService(build_graph(chat, FakeTools(), InMemorySaver()))
    async with harness(serve_asgi, service) as (_, client, auth, remote):
        ids = await remote(service.start_conversation("a", SENTINELS[0]))
        await remote(release(chat))

        async def finish():
            await asyncio.wait_for(asyncio.gather(*service._tasks), 10)
            await service._update(
                ids["conversation_id"],
                {
                    "status": terminal,
                    "error": SENTINELS[2],
                    "evidence_ids": [SENTINELS[3]],
                    "draft": {"text": SENTINELS[4]},
                },
            )

        await remote(finish())
        async with client.stream(
            "GET",
            "/conversations/" + ids["conversation_id"] + "/events",
            headers=auth("a"),
        ) as response:
            lines = response.aiter_lines()
            assert await status(lines) == {**ids, "status": terminal, "seq": 0}
            assert await frame(lines) is None


@async_test
async def test_disappearing_conversation_closes_stream(serve_asgi):
    chat = HeldChat()
    service = TaskService(build_graph(chat, FakeTools(), InMemorySaver()))
    original = service.progress
    calls = 0

    async def disappearing(*args):
        nonlocal calls
        calls += 1
        return await original(*args) if calls == 1 else None

    service.progress = disappearing
    async with harness(serve_asgi, service) as (_, client, auth, remote):
        ids = await remote(service.start_conversation("a", "합성 요청"))
        async with client.stream(
            "GET",
            "/conversations/" + ids["conversation_id"] + "/events",
            headers=auth("a"),
        ) as response:
            lines = response.aiter_lines()
            assert (await status(lines))["status"] == "running"
            assert await frame(lines) is None
        await remote(release(chat))

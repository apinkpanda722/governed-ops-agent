import asyncio
from datetime import timedelta

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, reply
from test_api_conversations import write_service

from govops.agent.graph import build_graph
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.gateway.client import GuardrailBlocked


def safe(value, *, keys=(), strings=()):
    if isinstance(value, dict):
        assert not set(value).intersection(keys)
        for key, item in value.items():
            safe(key, keys=keys, strings=strings)
            safe(item, keys=keys, strings=strings)
    elif isinstance(value, list):
        for item in value:
            safe(item, keys=keys, strings=strings)
    elif isinstance(value, str):
        assert all(text not in value for text in strings)


def client_for(service):
    sessions = SessionStore()
    app = create_app(
        users=frozenset(), sessions=sessions, demo_login=False, service=service
    )
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    )

    def auth(user):
        return {"Authorization": "Bearer " + sessions.issue(user)[0]}

    return client, auth


async def settled(service):
    await asyncio.wait_for(asyncio.gather(*service._tasks), 10)


@pytest.mark.parametrize("status", ["completed", "rejected", "failed", "blocked_input"])
@async_test
async def test_history_and_current_turn(status):
    service = TaskService(
        build_graph(
            FakeChat(reply("첫 답변"), reply("둘째 답변")), FakeTools(), InMemorySaver()
        )
    )
    client, auth = client_for(service)
    async with client:
        ident = (
            await client.post("/tasks", json={"request": "첫 요청"}, headers=auth("a"))
        ).json()["task_id"]
        await settled(service)
        first = (await client.get("/conversations/" + ident, headers=auth("a"))).json()
        assert first["history"] == []
        assert first["turn"]["answer"] == "첫 답변"
        await service._update(ident, {"status": status})
        second = await service.add_turn("a", ident, "둘째 요청")
        await settled(service)
        view = (await client.get("/conversations/" + ident, headers=auth("a"))).json()
        assert view["conversation_id"] == ident and view["turn_index"] == 1
        assert view["history"] == (
            [
                {"role": "user", "content": "첫 요청"},
                {"role": "assistant", "content": "첫 답변"},
            ]
            if status in {"completed", "rejected"}
            else []
        )
        assert view["turn"]["turn_id"] == second["turn_id"]
        assert view["turn"]["request"] == "둘째 요청"
        assert view["turn"]["answer"] == "둘째 답변"
        safe(view, keys={"messages", "evidence_ids", "stop_reason", "user_id"})
    await service.aclose()


@async_test
async def test_block_and_operational_details_are_private():
    service = TaskService(
        build_graph(
            FakeChat(GuardrailBlocked("injection_direct", None)),
            FakeTools(),
            InMemorySaver(),
        )
    )
    client, auth = client_for(service)
    async with client:
        ident = await service.start("a", "UNIQUE-INPUT-SENTINEL")
        await settled(service)
        view = (await client.get("/conversations/" + ident, headers=auth("a"))).json()
        assert view["turn"]["request"] is None
        assert set(view["turn"]["block"]) == {"code", "message"}
        safe(
            view,
            keys={"rules", "patterns", "digest", "guardrail_block"},
            strings={"UNIQUE-INPUT-SENTINEL"},
        )
        await service._update(
            ident,
            {
                "status": "completed",
                "answer": "합성 답변",
                "stop_reason": "exploration_budget",
            },
        )
        view = (await client.get("/conversations/" + ident, headers=auth("a"))).json()
        safe(
            view,
            keys={"stop_reason", "guardrail_post", "rules", "messages"},
            strings={"exploration_budget"},
        )
        assert set(view["turn"]["guardrail"]) == {
            "quarantined",
            "quarantined_at_least",
            "masked",
        }
    await service.aclose()


@async_test
async def test_approvals_policy_ownership_and_execution(tmp_path):
    service, _, slack, _, users, _, _, _ = write_service(tmp_path)
    client, auth = client_for(service)
    owner, approver, junior, other = users
    async with client:
        ident = await service.start(owner, "원문 비공개")
        await settled(service)
        hidden = [
            await client.get("/conversations/" + value, headers=auth(user))
            for value, user in [
                (ident, approver),
                (ident, other),
                ("f" * 32, owner),
                ("invalid", owner),
            ]
        ]
        assert {
            (r.status_code, r.content, tuple(r.headers.items())) for r in hidden
        } == {(404, hidden[0].content, tuple(hidden[0].headers.items()))}
        assert hidden[0].json() == {"detail": "conversation_not_found"}
        for user in (owner, junior, other):
            assert (await client.get("/approvals", headers=auth(user))).json() == {
                "approvals": []
            }
        items = (await client.get("/approvals", headers=auth(approver))).json()[
            "approvals"
        ]
        assert {item["task_id"] for item in items} == {ident}
        safe(
            items,
            keys={"history", "request", "answer", "messages"},
            strings={"원문 비공개"},
        )
        result = await client.post(
            "/tasks/" + items[0]["task_id"] + "/approval",
            headers=auth(approver),
            json={"decision": "approve", "draft_sha256": items[0]["draft_sha256"]},
        )
        assert result.status_code == 202 and len(slack.sent) == 1
        assert (await client.get("/approvals", headers=auth(approver))).json() == {
            "approvals": []
        }
        view = (await client.get("/conversations/" + ident, headers=auth(owner))).json()
        assert view["turn"]["effect"]["mock_id"] == "message-1"
        assert (await client.get("/approvals")).status_code == 401
        assert (await client.get("/conversations/" + ident)).status_code == 401
    await service.aclose()


@async_test
async def test_approval_hints_checks_expiry_sort_and_self(tmp_path):
    service, _, _, now, users, _, _, _ = write_service(tmp_path)
    owner, approver, *_ = users
    client, auth = client_for(service)
    async with client:
        ids = [await service.start(owner, "합성 공지") for _ in range(4)]
        await settled(service)
        await service._update(ids[0], {"check": {"status": "blocked"}})
        await service._update(
            ids[1], {"expires_at": (now[0] - timedelta(seconds=1)).isoformat()}
        )
        await service._update(
            ids[2], {"expires_at": (now[0] + timedelta(minutes=2)).isoformat()}
        )
        await service._update(
            ids[3], {"expires_at": (now[0] + timedelta(minutes=1)).isoformat()}
        )
        stale = "e" * 32
        await service.turns.add(
            turn_id=stale,
            conversation_id=ids[3],
            owner_sub=owner,
            turn_index=0,
            created_at=now[0].isoformat(),
        )
        await service.turns.set_awaiting(stale, True)
        items = (await client.get("/approvals", headers=auth(approver))).json()[
            "approvals"
        ]
        assert len(items) == 2 and {i["task_id"] for i in items} == set(ids[2:])
        assert [(i["expires_at"], i["task_id"]) for i in items] == sorted(
            (i["expires_at"], i["task_id"]) for i in items
        )
        assert not service.turns.rows[stale]["awaiting"]
        assert not service.turns.rows[ids[1]]["awaiting"]
        assert (await service.get(owner, ids[1]))["status"] == "expired"
        await service.approve(
            approver, ids[2], decision="reject", draft_sha256=items[1]["draft_sha256"]
        )
        assert {
            i["task_id"]
            for i in (await client.get("/approvals", headers=auth(approver))).json()[
                "approvals"
            ]
        } == {ids[3]}
        state = await service.get(owner, ids[3])
        await service._update(
            ids[3],
            {
                "draft": {
                    **state["draft"],
                    "args": {"channel": "platform", "text": "합성 안내"},
                },
                "check": {"status": "masked"},
            },
        )
        items = (await client.get("/approvals", headers=auth(owner))).json()[
            "approvals"
        ]
        assert {i["task_id"] for i in items} == {ids[3]}
        safe(items, keys={"history", "request", "answer", "messages"})
    await service.aclose()


@async_test
async def test_post_detection_is_not_in_conversation_response():
    from dataclasses import replace

    from govops.audit.log import MemoryAuditLog
    from govops.gateway.hook_header import GuardrailInfo

    audit = MemoryAuditLog()
    result = replace(
        reply("합성 답변"),
        guardrail=GuardrailInfo("a" * 12, "b" * 12, "platform", post="flag"),
    )
    service = TaskService(
        build_graph(FakeChat(result), FakeTools(), InMemorySaver(), audit=audit)
    )
    client, auth = client_for(service)
    async with client:
        ident = await service.start("demo-platform-g3", "합성 요청")
        await settled(service)
        assert any(row.get("guardrail_post") == "flag" for row in audit.records)
        assert any(row["event"] == "guardrail.output_flagged" for row in audit.records)
        view = (
            await client.get(
                "/conversations/" + ident, headers=auth("demo-platform-g3")
            )
        ).json()
        safe(
            view,
            keys={"guardrail_post", "stop_reason", "guardrail_output_flagged"},
            strings={"guardrail.output_flagged"},
        )
        assert view["turn"]["answer"] == "합성 답변"
        assert set(view["turn"]["guardrail"]) == {
            "quarantined",
            "quarantined_at_least",
            "masked",
        }
    await service.aclose()

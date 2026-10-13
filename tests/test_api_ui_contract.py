import copy
from datetime import datetime, timedelta

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test, reply
from test_api_conversation_views import client_for, safe, settled
from test_api_conversations import write_service

from govops.agent import checks
from govops.agent.graph import build_graph, get_task
from govops.agent.scope import SCOPE_REASONS
from govops.agent.users import UserInfo
from govops.api.tasks import TaskService


@pytest.mark.parametrize(
    "reason", [*sorted(checks.REASONS | SCOPE_REASONS), None, "unknown"]
)
def test_check_messages(reason):
    service = TaskService(None, users={"a": UserInfo("a", "platform", 3)})
    state = {
        "status": "blocked",
        "user_id": "a",
        "draft": {},
        "check": {"status": "blocked", "reason": reason},
        "rejected_edit": {"status": "blocked", "reason": reason},
    }
    view = service._view(state, "a" * 32, approval=True)
    for field in ("check", "rejected_edit"):
        assert view[field]["message"] and "통과했습니다" not in view[field]["message"]
        if reason == "recipient_not_allowed":
            assert "team-platform" in view[field]["message"]
            assert "team-finance" not in view[field]["message"]
    state["check"] = {"status": "passed"}
    assert (
        "통과했습니다"
        in service._view(state, "a" * 32, approval=True)["check"]["message"]
    )


def test_tool_steps_private_and_bounded():
    service = TaskService(None)
    messages = [
        {
            "role": "assistant",
            "tool_calls": [{"id": "old", "function": {"name": "jira_get_ticket"}}],
        },
        {"role": "user", "content": "request"},
    ]
    for i in range(25):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": str(i),
                            "function": {
                                "name": "jira_get_ticket",
                                "arguments": "ARG-SENTINEL",
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": str(i),
                    "content": '{"error": "RESULT-SENTINEL"}'
                    if i == 0
                    else '{"doc_id": "DOC-SENTINEL"}',
                },
            ]
        )
    messages.append(
        {
            "role": "assistant",
            "tool_calls": [{"id": "write", "function": {"name": "slack_send_message"}}],
        }
    )
    state = {
        "status": "completed",
        "user_id": "a",
        "messages": messages,
        "effect": {"mock_id": "synthetic"},
        "approvals": [{"decision": "edit_approve"}],
        "effect_at": "2026-10-08T00:00:00+00:00",
    }
    view = service._view(state, "a" * 32)
    assert len(view["tool_steps"]) == 20
    assert all(
        set(step) == {"name", "ok"} and step["name"] == "jira_get_ticket"
        for step in view["tool_steps"]
    )
    assert view["tool_steps"][0]["ok"] is False
    assert all(step["ok"] for step in view["tool_steps"][1:])
    assert view["approval_method"] == "edit_approve"
    assert datetime.fromisoformat(view["effect_at"]).utcoffset() == timedelta(0)
    safe(view, strings={"ARG-SENTINEL", "RESULT-SENTINEL", "DOC-SENTINEL"})
    safe(
        service._view(state, "a" * 32, approval=True),
        keys={"tool_steps", "approval_method", "effect_at", "limited"},
    )


@async_test
async def test_list_contract_and_read_only(tmp_path):
    service, _, _, now, users, *_ = write_service(tmp_path)
    owner, approver, *_ = users
    ids = [
        await service.start(owner, "첫\n  요청"),
        await service.start(approver, "다른 요청"),
    ]
    await settled(service)
    now[0] += timedelta(minutes=31)
    before = copy.deepcopy(await get_task(service.graph, ids[0]))
    for ident in ("e" * 32, ids[1]):
        await service.turns.add(
            turn_id="hint-" + ident,
            conversation_id=ident,
            owner_sub=owner,
            turn_index=0,
            created_at=(now[0] - timedelta(days=1)).isoformat(),
        )
    client, auth = client_for(service)
    async with client:
        for limit in (1, 50):
            response = await client.get(
                "/conversations", params={"limit": limit}, headers=auth(owner)
            )
            assert response.status_code == 200
            rows = response.json()["conversations"]
            assert len(rows) == 1
            assert set(rows[0]) == {
                "conversation_id",
                "title",
                "status",
                "awaiting_approval",
                "turn_count",
                "updated_at",
            }
            assert rows[0]["conversation_id"] == ids[0]
            assert rows[0]["title"] == "첫 요청"
            assert (
                rows[0]["status"] == "expired" and rows[0]["awaiting_approval"] is False
            )
            safe(rows, keys={"request", "history", "answer", "messages", "draft"})
        for limit in ("0", "51", "abc"):
            response = await client.get(
                "/conversations", params={"limit": limit}, headers=auth(owner)
            )
            assert response.status_code == 422 and response.json() == {
                "detail": "invalid_request"
            }
        assert (await client.get("/conversations")).status_code == 401
    assert await get_task(service.graph, ids[0]) == before
    assert any(
        task.interrupts
        for task in (await service.graph.aget_state(service._config(ids[0]))).tasks
    )
    await service.aclose()


@async_test
async def test_titles_sort_and_index():
    service = TaskService(
        build_graph(
            FakeChat(*[reply("답변") for _ in range(4)]), FakeTools(), InMemorySaver()
        )
    )
    first = await service.start("a", "가" * 41)
    await settled(service)
    await service.add_turn("a", first, "둘째 요청")
    await settled(service)
    second = await service.start("a", "INPUT-SENTINEL")
    await settled(service)
    await service._update(second, {"status": "blocked_input"})
    rows = (await service.conversations("a", 50))["conversations"]
    assert len(rows) == 2
    assert [r["conversation_id"] for r in rows] == [second, first]
    assert {r["title"] for r in rows} == {"가" * 40 + "…", "새 대화"}
    assert next(r for r in rows if r["conversation_id"] == first)["turn_count"] == 2
    safe(rows, strings={"INPUT-SENTINEL", "둘째 요청"})
    candidates = await service.turns.list_for_owner("a", 1)
    assert len(candidates) == 1 and candidates[0]["conversation_id"] == second
    assert set(candidates[0]) == {"conversation_id", "last_created_at", "turn_count"}
    await service.aclose()


@pytest.mark.parametrize("decision", ["approve", "edit_approve"])
@async_test
async def test_effect_fields_and_approver_privacy(tmp_path, decision):
    service, _, _, _, users, *_ = write_service(tmp_path)
    owner, approver, *_ = users
    ident = await service.start(owner, "합성 요청")
    await settled(service)
    rows = (await service.approvals(approver))["approvals"]
    assert len(rows) == 1
    safe(rows, keys={"tool_steps", "approval_method", "effect_at", "limited"})
    other = await service.get(approver, ident)
    assert other is not None
    safe(other, keys={"tool_steps", "approval_method", "effect_at", "limited"})
    await service.approve(
        approver,
        ident,
        decision=decision,
        draft_sha256=rows[0]["draft_sha256"],
        edited={"text": "수정 합성 안내"} if decision == "edit_approve" else None,
    )
    view = await service.get(owner, ident)
    assert view["approval_method"] == decision
    assert datetime.fromisoformat(view["effect_at"]).utcoffset() == timedelta(0)
    await service.aclose()


@async_test
async def test_second_turn_requester_prompt(tmp_path):
    from govops.agent.graph import PROFILES
    from govops.agent.requester import requester_section
    from govops.agent.scope import ScopeIndex
    from govops.guardrails.policy import TeamPolicy

    seed, _, _, _, _, tools, effects, audit = write_service(tmp_path)
    users = {
        u.label: u
        for u in (
            UserInfo("demo-platform-g3", "platform", 3),
            UserInfo("demo-finance-g3", "finance", 3),
        )
    }
    await seed.aclose()
    service = TaskService(
        build_graph(
            FakeChat(*[reply("답변") for _ in range(4)]),
            tools,
            InMemorySaver(),
            profile=PROFILES["writes_team"],
            users=users,
            effects=effects,
            audit=audit,
            scope=ScopeIndex({}, {}, tuple(users.values())),
            policy=TeamPolicy({"platform": "block", "finance": "block"}),
        )
    )
    prompts = []
    for user, info in users.items():
        ident = await service.start(user, "첫 요청")
        await settled(service)
        await service.add_turn(user, ident, "다음 요청")
        await settled(service)
        state = await get_task(service.graph, ident)
        prompt = state["messages"][0]["content"]
        assert state["messages"][0]["role"] == "system"
        assert requester_section(info) in prompt
        prompts.append(prompt)
    assert len(set(prompts)) == 2
    await service.aclose()


@async_test
async def test_memory_index_groups_owner_and_ties():
    from govops.api.conversations import InMemoryTurnIndex

    index = InMemoryTurnIndex()
    for turn, conversation, owner, time in (
        ("t1", "c1", "a", "01"),
        ("t2", "c1", "a", "03"),
        ("t3", "c2", "a", "03"),
        ("t4", "c3", "b", "04"),
    ):
        await index.add(
            turn_id=turn,
            conversation_id=conversation,
            owner_sub=owner,
            turn_index=0,
            created_at=time,
        )
    rows = await index.list_for_owner("a", 50)
    assert rows == [
        {"conversation_id": "c1", "last_created_at": "03", "turn_count": 2},
        {"conversation_id": "c2", "last_created_at": "03", "turn_count": 1},
    ]
    assert await index.list_for_owner("a", 1) == rows[:1]
    assert len(await index.list_for_owner("b", 50)) == 1


def test_tool_steps_partial_invalid_and_current_turn():
    from govops.api.steps import tool_steps

    def call(ident, name):
        return {"id": ident, "function": {"name": name}}

    messages = [
        {"role": "assistant", "tool_calls": [call("old", "jira_get_ticket")]},
        {"role": "user", "content": "합성 요청"},
        {
            "role": "assistant",
            "tool_calls": [
                call("pending", "jira_get_ticket"),
                call("bad", "jira_search_tickets"),
                call("write", "slack_send_message"),
                call("unknown", "unknown_tool"),
                call("invalid", "Invalid-Name"),
            ],
        },
        {"role": "tool", "tool_call_id": "bad", "content": "invalid-json"},
    ]
    assert tool_steps(messages) == [
        {"name": "jira_get_ticket", "ok": False},
        {"name": "jira_search_tickets", "ok": False},
    ]
    messages.append({"role": "tool", "tool_call_id": "pending", "content": "{}"})
    assert tool_steps(messages)[0]["ok"] is True

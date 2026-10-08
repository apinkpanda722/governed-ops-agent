"""Requester prompts share deterministic recipient and approval policy."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from test_agent_graph import FakeChat, FakeTools, async_test, call, reply

from govops.agent.approval import (
    ANNOUNCE_APPROVER_MIN_GRADE,
    Decision,
    approver_decision,
)
from govops.agent.checks import (
    allowed_channels,
    check_recipient_and_pii,
    recipient_not_allowed_message,
)
from govops.agent.draft import Draft
from govops.agent.graph import (
    PROFILES,
    READ_ONLY,
    AgentContext,
    EffectDeps,
    build_graph,
    run_task,
    system_prompt_for,
)
from govops.agent.org_info import TEAMS, team_name_ko
from govops.agent.prompts import SEND_RULES
from govops.agent.requester import requester_section
from govops.agent.scope import ScopeIndex
from govops.agent.users import UserInfo, load_users
from govops.audit.log import AuditLog
from govops.guardrails.policy import TeamPolicy
from govops.replay.llm import prompt_hash
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy


def test_all_seed_requesters_and_channel_policy():
    users = load_users()
    assert len(users) > 0
    for user in users.values():
        section = requester_section(user)
        assert section == requester_section(user)
        assert team_name_ko(user.team) in section and user.team in section
        assert user.label not in section
        # Only the shared approval threshold is numeric; personal grade is omitted.
        assert re.findall(r"\d+", section) == [str(ANNOUNCE_APPROVER_MIN_GRADE)]
        assert requester_section(replace(user, grade=user.grade + 1)) == section
        assert "syn-" not in section and not re.search(r"[A-Z]{3}-\d+", section)
        assert set(re.findall(r"team-[a-z-]+|announce", section)) == set(
            allowed_channels(user.team)
        )
        for team in TEAMS:
            channel = f"team-{team.owner_team}"
            if team.owner_team != user.team:
                assert team.name_ko not in section and channel not in section
                assert channel not in recipient_not_allowed_message(user.team)
            draft = Draft(
                "slack_send_message", {"channel": channel, "text": "합성 안내"}
            )
            checked = check_recipient_and_pii(
                draft, requester=user, policy=TeamPolicy({})
            )
            assert checked.status == (
                "passed" if channel in allowed_channels(user.team) else "blocked"
            )
            assert checked.reason == (
                None
                if channel in allowed_channels(user.team)
                else "recipient_not_allowed"
            )
        announce = Draft(
            "slack_send_message", {"channel": "announce", "text": "합성 안내"}
        )
        assert (
            check_recipient_and_pii(
                announce, requester=user, policy=TeamPolicy({})
            ).status
            == "passed"
        )
        message = recipient_not_allowed_message(user.team)
        assert (
            f"자기 팀 채널(team-{user.team})" in message
            and "전사 공지(announce)" in message
        )
        assert "본인" in section and "같은 팀" in section
        for grade in (
            ANNOUNCE_APPROVER_MIN_GRADE - 1,
            ANNOUNCE_APPROVER_MIN_GRADE,
            ANNOUNCE_APPROVER_MIN_GRADE + 1,
        ):
            peer = UserInfo("approval-peer", user.team, grade)
            assert approver_decision(announce, requester=user, approver=peer) == (
                Decision.ALLOW
                if grade >= ANNOUNCE_APPROVER_MIN_GRADE
                else Decision.NOT_FOUND
            )
        assert (
            approver_decision(announce, requester=user, approver=user)
            == Decision.FORBIDDEN
        )
        assert (
            approver_decision(
                announce,
                requester=user,
                approver=UserInfo("other", "unknown", ANNOUNCE_APPROVER_MIN_GRADE),
            )
            == Decision.NOT_FOUND
        )
    assert allowed_channels("unknown") == ("announce",)
    assert "자기 팀 채널" not in recipient_not_allowed_message("unknown")
    assert "전사 공지(announce)" in recipient_not_allowed_message("unknown")


def make_graph(chat, tmp_path, *, profile=None):
    users = load_users()
    tools = FakeTools()
    tools.policy = ToolPolicy()

    class Jira:
        async def add_comment(self, *args, **kwargs):
            pytest.fail("Unexpected Jira effect")

        async def create_followup(self, *args, **kwargs):
            pytest.fail("Unexpected Jira effect")

    class Slack:
        async def send_message(self, user_id, **kwargs):
            return {"message": {"message_id": "message-1"}}

    return build_graph(
        chat,
        tools,
        InMemorySaver(),
        profile=profile or PROFILES["writes_team"],
        users=users,
        effects=EffectDeps(Jira(), Slack(), InMemoryOutboxStore()),
        audit=AuditLog(tmp_path),
        scope=ScopeIndex({}, {}, tuple(users.values())),
        policy=TeamPolicy({}),
        clock=lambda: datetime(2026, 10, 8, 9, tzinfo=timezone(timedelta(hours=9))),
    )


def test_templates_and_frozen_hash(tmp_path):
    graph = make_graph(FakeChat(), tmp_path)
    users = list(load_users().values())
    first = users[0]
    other = next(user for user in users if user.team != first.team)
    for user in (first, other):
        assert system_prompt_for(
            graph, user.label
        ) == graph.agent_profile.system_prompt + "\n" + requester_section(user)
    assert requester_section(first) != requester_section(other)
    org = PROFILES["writes_org"]
    team = PROFILES["writes_team"]
    assert team.system_prompt == org.system_prompt.replace(
        "수신처는 사용자가 요청한 곳만 사용합니다.", SEND_RULES
    )
    assert (
        team.tools == org.tools and team.requester_section and not org.requester_section
    )

    def digest(profile):
        return prompt_hash(
            system_prompt=profile.system_prompt,
            tools=profile.tools,
            model="gpt-6-luna",
            params={},
        )

    assert (
        digest(org)
        == "0a82447c0360f47db5f433d2d9e8b56e70a70c8c95f37479b86d8d6af95cdda7"
    )
    assert digest(team) != digest(org)
    graph.agent_profile = org
    assert system_prompt_for(graph, "unknown") == org.system_prompt


@async_test
async def test_actual_graph_prompt_and_unknown_user(tmp_path):
    chat = FakeChat(reply("안내"), reply("안내"))
    graph = make_graph(chat, tmp_path)
    users = list(load_users().values())
    selected = [users[0], next(user for user in users if user.team != users[0].team)]
    for i, user in enumerate(selected):
        state = await run_task(
            graph, task_id=f"task-{i}", user_id=user.label, request="안내"
        )
        assert chat.calls[i]["messages"][0] == {
            "role": "system",
            "content": system_prompt_for(graph, user.label),
        }
        assert state["effect_at"] is None
    with pytest.raises(ValueError, match="^unknown_user$"):
        await run_task(graph, task_id="unknown", user_id="unknown", request="안내")
    assert len(chat.calls) == 2


@async_test
@pytest.mark.parametrize("decision", ["approve", "reject"])
async def test_effect_timestamp_and_next_turn(tmp_path, decision):
    user = next(iter(load_users().values()))
    chat = FakeChat(
        reply(
            calls=[
                call(
                    "slack_send_message",
                    json.dumps(
                        {"channel": allowed_channels(user.team)[0], "text": "합성 안내"}
                    ),
                )
            ]
        ),
        reply("다음 안내"),
    )
    graph = make_graph(chat, tmp_path)
    task_id = uuid4().hex
    state = await run_task(
        graph, task_id=task_id, user_id=user.label, request="보내 주세요"
    )
    assert state["effect_at"] is None
    assert state["status"] == "awaiting_approval"
    state = await graph.ainvoke(
        Command(
            resume={
                "approver": user.label,
                "decision": decision,
                "draft_sha256": state["draft_sha256"],
            }
        ),
        {"configurable": {"thread_id": task_id}},
        context=AgentContext(user.label),
    )
    assert state["status"] == ("completed" if decision == "approve" else "rejected")
    if decision == "approve":
        assert datetime.fromisoformat(state["effect_at"]) == datetime(
            2026, 10, 8, tzinfo=UTC
        )
        assert datetime.fromisoformat(state["effect_at"]).utcoffset() == timedelta(0)
        assert "effect_at" not in state["effect"]
    else:
        assert state["effect_at"] is None
    next_state = await run_task(
        graph,
        task_id=uuid4().hex,
        conversation_id=task_id,
        user_id=user.label,
        request="다음 안내",
        previous=state,
    )
    assert next_state["effect_at"] is None
    readonly = make_graph(
        FakeChat(reply("조회 안내")), tmp_path / "readonly", profile=READ_ONLY
    )
    assert (
        await run_task(readonly, task_id="read", user_id=user.label, request="조회")
    )["effect_at"] is None

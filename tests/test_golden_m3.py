"""M3 golden contracts and replay determinism, using synthetic offline streams."""

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import call, reply

from govops.agent.graph import WITH_WRITES, build_graph, run_task
from govops.api.tasks import ApprovalError
from govops.golden import (
    GOLDEN_TASKS,
    compare,
    digest_for,
    golden_expectations,
    load_golden_specs,
    manifest_tasks,
    record_status,
    replay_task,
)
from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.seed import load_tickets
from govops.mocks.service import create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.replay.a_through import RecordingToolExecutor
from govops.replay.llm import RecordingChatClient, RerecordRequired, _hash, _request
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolOutcome, ToolRuntime
from govops.tools.slack import SlackClient

ROOT = Path(__file__).resolve().parents[1]


def test_fourteen_expectations_and_read_compatibility():
    specs = load_golden_specs()
    assert len(GOLDEN_TASKS) == len(specs) == 14
    expected = golden_expectations(specs.values())
    committed = json.loads((ROOT / "data/replay/golden/expectations.json").read_text())
    assert {key: expected[key] for key in committed} == committed
    for key, spec in specs.items():
        assert spec.split == "dev"
        if spec.executable_in == "m3":
            row = expected[key]
            assert row["effects"] == [
                effect.model_dump() for effect in spec.expected.effects
            ]
            assert row["effect_count"] == sum(e.count for e in spec.expected.effects)
            assert row["approval_plan"] == [
                step.model_dump() for step in spec.approval_script
            ]
            assert row["outcome"] == spec.expected.outcome
            assert row["status"] == (
                "completed"
                if spec.expected.outcome == "approved_executed"
                else "rejected"
            )
            assert row["forbidden_tools"] == sorted(spec.forbidden.forbidden_tools)
    assert "edit_append" in expected["T6-03"]["approval_plan"][0]
    assert expected["T3-07"]["effects"][0]["structure"] == {
        "parent_ticket_id": "SRE-1004",
        "project_key": "SRE",
    }


def test_manifest_profile_default_and_validation(tmp_path):
    llm, a, golden = [tmp_path / key for key in ("llm", "a", "golden")]
    for directory in (llm, a, golden):
        directory.mkdir()
    key = "T1-01"
    row = {"status": "pass", "golden_eligible": True, "observation_digest": {}}
    for directory in (llm, a):
        (directory / f"{key}.jsonl").write_text("")
    path = golden / "status.json"
    path.write_text(json.dumps({key: row}))
    assert (
        manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a)[key]["profile"]
        == "read_only"
    )
    row["profile"] = "writes"
    path.write_text(json.dumps({key: row}))
    assert (
        manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a)[key]["profile"]
        == "writes"
    )
    row["profile"] = "unknown"
    path.write_text(json.dumps({key: row}))
    with pytest.raises(ValueError, match="invalid_profile"):
        manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a)


# Actions are fixture inputs, independent of the expected effects.
ACTIONS = {
    "T2-01": ("slack_send_message", {"channel": "team-sre", "text": "합성 요약"}),
    "T3-01": ("jira_add_comment", {"ticket_id": "PAY-1234", "body": "합성 요약"}),
    "T3-07": (
        "jira_create_followup_ticket",
        {
            "parent_ticket_id": "SRE-1004",
            "title": "합성 후속",
            "description": "합성 요약",
        },
    ),
    "T4-01": ("slack_send_message", {"channel": "team-finance", "text": "합성 집계"}),
    "T6-01": ("slack_send_message", {"channel": "team-security", "text": "합성 요약"}),
    "T6-03": ("slack_send_message", {"channel": "team-platform", "text": "합성 요약"}),
    "T5-11": ("slack_send_message", {"channel": "team-data-ai", "text": "합성 요약"}),
}


@pytest.mark.parametrize("key", list(ACTIONS))
def test_write_replay_and_request_determinism(tmp_path, monkeypatch, key):
    asyncio.run(exercise(tmp_path, monkeypatch, key))


async def exercise(tmp_path, monkeypatch, key, *, all_hands=False):
    from govops.golden import _write_graph

    spec = load_golden_specs()[key]
    if all_hands:
        from govops.eval.spec import ApprovalStep

        spec = spec.model_copy(
            update={
                "approval_script": [
                    ApprovalStep(
                        actor="requester", decision="approve", expect_http=403
                    ),
                    ApprovalStep(
                        actor="same_team_g4", decision="approve", expect_http=None
                    ),
                ]
            }
        )
        monkeypatch.setattr("govops.golden.load_golden_specs", lambda: {key: spec})
    credentials = tmp_path / "mock.json"
    generate_tokens(
        users=ROOT / "data/seed/users.json", plaintext=credentials, hash_dir=tmp_path
    )
    monkeypatch.setenv("MOCK_TOKENS_FILE", str(credentials))
    jira, slack = JiraState(load_tickets(ROOT / "data/seed/tickets.json")), SlackState()
    requests = []
    graphs = []

    class SyntheticA:
        async def call_tool(self, *args):
            pytest.fail("replayed A must not call a server")

    class ReadA:
        async def call(self, name, arguments, *, user_id):
            if name == "jira_get_ticket":
                return await runtime.call(name, arguments, user_id=user_id)
            return ToolOutcome(
                {"documents": [{"doc_id": "syn-golden-fixture"}]},
                False,
                ("syn-golden-fixture",),
            )

    class Chat:
        index = 0

        async def complete(self, **kwargs):
            self.index += 1
            if self.index == 1:
                return reply(calls=[call("search_docs", '{"query":"합성"}')])
            if self.index == 2 and key.startswith("T3"):
                args = ACTIONS[key][1]
                return reply(
                    calls=[
                        call(
                            "jira_get_ticket",
                            json.dumps(
                                {
                                    "ticket_id": args.get(
                                        "ticket_id", args.get("parent_ticket_id")
                                    )
                                }
                            ),
                        )
                    ]
                )
            if self.index == 2:
                return reply(calls=[call("lookup_id", '{"id":"syn-golden-fixture"}')])
            name, args = ACTIONS[key]
            if all_hands:
                args = {"channel": "announce", "text": "합성 공지"}
            return reply(calls=[call(name, json.dumps(args, ensure_ascii=False))])

    def factory(chat, tools, saver, **kwargs):
        class Spy:
            async def complete(self, **kw):
                requests.append(json.loads(json.dumps(kw["messages"])))
                return await chat.complete(**kw)

        graph = build_graph(Spy(), tools, saver, **kwargs)
        graphs.append(graph)
        return graph

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_rest_app(
                    "jira",
                    state=jira,
                    token_store=TokenHashStore.load(
                        tmp_path / "mock_jira_token_hashes.json"
                    ),
                )
            )
        ) as jh,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_rest_app(
                    "slack",
                    state=slack,
                    token_store=TokenHashStore.load(
                        tmp_path / "mock_slack_token_hashes.json"
                    ),
                )
            )
        ) as sh,
    ):
        runtime = ToolRuntime(
            SyntheticA(),
            JiraClient("http://127.0.0.1", http_client=jh),
            SlackClient("http://127.0.0.1", http_client=sh),
        )
        chat = RecordingChatClient(
            Chat(),
            task_id=key,
            directory=tmp_path / "llm",
            system_prompt=WITH_WRITES.system_prompt,
            tools=WITH_WRITES.tools,
            model="gpt-6-luna",
            params={},
        )
        tools = RecordingToolExecutor(ReadA(), task_id=key, directory=tmp_path / "a")
        tools.policy = runtime.policy
        graph, service = _write_graph(chat, tools, runtime, InMemorySaver(), factory)
        task_id = "1" * 32
        state = await run_task(
            graph, task_id=task_id, user_id=spec.user, request=spec.request
        )
        for step in spec.approval_script:
            actor = (
                spec.user
                if step.actor == "requester"
                else min(
                    u.label
                    for u in service.users.values()
                    if u.team == service.users[spec.user].team
                    and u.grade == 4
                    and u.label != spec.user
                )
            )
            edited = (
                {"text": state["draft"]["args"]["text"] + "\n" + step.edit_append}
                if step.decision == "edit_approve"
                else None
            )
            try:
                _, _ = await service.approve(
                    actor,
                    task_id,
                    decision=step.decision,
                    draft_sha256=state["draft_sha256"],
                    edited=edited,
                )
            except ApprovalError as exc:
                assert getattr(exc, "status_code", None) == step.expect_http
            from govops.agent.graph import get_task

            state = await get_task(graph, task_id)
        chat.finish()
        summary = tools.finish()
        initial_messages = requests[:]
        requests.clear()
        observations = []
        variable_values = [task_id, state.get("expires_at"), state.get("created_at")]
        for run_id in ("2" * 32, "3" * 32):
            jira.reset()
            slack.reset()
            # Shift generated mock IDs without introducing effects into this run.
            if run_id.startswith("3"):
                next(jira.counter)
                next(slack.counter)
                if key == "T3-07":
                    jira.tickets["SRE-9998"] = dict(
                        jira.tickets["SRE-1004"], ticket_id="SRE-9998"
                    )
            obs = await replay_task(
                key,
                llm_dir=tmp_path / "llm",
                a_dir=tmp_path / "a",
                mock_executor=runtime,
                graph_factory=factory,
                profile="writes",
                run_id=run_id,
                mock_state=lambda: {
                    "jira": jira.report(bodies=True),
                    "slack": slack.report(bodies=True),
                },
            )
            observations.append(obs)
            snapshot = await graphs[-1].aget_state(
                {"configurable": {"thread_id": run_id}}
            )
            values = snapshot.values
            variable_values.extend(
                [
                    run_id,
                    values.get("expires_at"),
                    values.get("created_at"),
                    (values.get("effect") or {}).get("mock_id"),
                ]
            )
            variable_values.extend(row["approval_id"] for row in values["approvals"])
        assert len(requests) == 2 * len(initial_messages)
        first, second = (
            requests[: len(initial_messages)],
            requests[len(initial_messages) :],
        )
        keys = lambda rows: [
            _hash(_request(messages, WITH_WRITES.tools, 1024)) for messages in rows
        ]
        assert keys(initial_messages) == keys(first) == keys(second)
        encoded = json.dumps(requests, ensure_ascii=False)
        for value in variable_values:
            if value:
                assert value not in encoded
        assert not re.search(
            r'"(?:task_id|approval_id|expires_at|timestamp|mock_id)"|message-\d+|comment-\d+',
            encoded,
        )
        # Ticket dates are fixed source facts, unlike execution/approval clocks.
        # Reject every other ISO timestamp, including newly generated mock dates.
        seed_times = {
            ticket[field]
            for ticket in load_tickets(ROOT / "data/seed/tickets.json")
            for field in ("created_at", "resolved_at")
            if ticket.get(field)
        }
        message_times = set(
            re.findall(
                r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
                encoded,
            )
        )
        assert message_times <= seed_times
        if observations[0].effects:
            ids = [
                (
                    await graph.aget_state({"configurable": {"thread_id": run_id}})
                ).values["effect"]["mock_id"]
                for graph, run_id in zip(graphs[-2:], ("2" * 32, "3" * 32), strict=True)
            ]
            assert ids[0] != ids[1]
        if all_hands:
            assert observations[0].status == "completed"
            assert observations[0].effects[0].target == "announce"
            assert [a.http_status for a in observations[0].approvals] == [403, 202]
            assert observations[0].approvals[1].actor != spec.user
        left, right = [digest_for(spec, obs) for obs in observations]
        assert left == right
        assert "effects" in left and "approval_decisions" in left
        assert all(
            set(effect) == {"kind", "target", "count"} for effect in left["effects"]
        )
        assert (
            record_status(spec, observations[0], summary, profile="writes")["profile"]
            == "writes"
        )
        expected = golden_expectations([spec], require_all=False)[key]
        if not all_hands:
            assert left["effect_count"] == expected["effect_count"]
            assert left["status"] == expected["status"]
        assert left["approval_decisions"] == [
            {"actor": record.actor, "decision": record.decision}
            for record in observations[0].approvals
        ]
        assert (
            compare(
                expected, {"status": "recorded_fail", "observation_digest": left}, right
            )
            == []
        )
        for field, value in [
            ("effect_count", 999),
            ("tool_order", []),
            ("approval_decisions", [{"actor": "different", "decision": "reject"}]),
        ]:
            assert f"{field}_mismatch" in compare(
                {},
                {"status": "recorded_fail", "observation_digest": left},
                right | {field: value},
            )
        if key == "T6-03":
            assert observations[0].effect_bodies == [
                "합성 요약\n" + spec.approval_script[0].edit_append
            ]
        changed = observations[0].model_copy(
            update={
                "approvals": [
                    record.model_copy(update={"approval_id": "different"})
                    for record in observations[0].approvals
                ],
                "effects": [
                    effect.model_copy(
                        update={"approval_id": "different", "body_sha256": "different"}
                    )
                    for effect in observations[0].effects
                ],
            }
        )
        assert digest_for(spec, changed) == left
        path = tmp_path / "llm" / f"{key}.jsonl"
        rows = path.read_text().splitlines()
        header = json.loads(rows[0]) | {"prompt_hash": "changed"}
        path.write_text(json.dumps(header) + "\n" + "\n".join(rows[1:]) + "\n")
        with pytest.raises(RerecordRequired):
            await replay_task(
                key,
                llm_dir=tmp_path / "llm",
                a_dir=tmp_path / "a",
                mock_executor=runtime,
                graph_factory=factory,
                profile="writes",
                mock_state=dict,
            )


def test_all_hands_approver_replay(tmp_path, monkeypatch):
    asyncio.run(exercise(tmp_path, monkeypatch, "T2-01", all_hands=True))

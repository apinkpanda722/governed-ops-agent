"""Offline synthetic records exercise the real golden graph harness."""

import asyncio
import json
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import call, reply

from govops.agent.graph import build_graph, run_task
from govops.agent.prompts import SYSTEM_PROMPT
from govops.golden import (
    GOLDEN_TASKS,
    compare,
    digest,
    golden_expectations,
    load_golden_specs,
    record_status,
    replay_task,
)
from govops.replay.a_through import RecordingToolExecutor
from govops.replay.llm import RecordingChatClient, RerecordRequired
from govops.tools.registry import ToolOutcome, agent_tool_definitions


class SyntheticA:
    async def call(self, name, arguments, *, user_id):
        return ToolOutcome(
            {"documents": [{"doc_id": "syn-incident-golden"}]},
            False,
            ("syn-incident-golden",),
        )


class Chat:
    def __init__(self):
        self.index = 0

    async def complete(self, **kwargs):
        self.index += 1
        if self.index == 1:
            return reply(calls=[call("search_docs", json.dumps({"query": "합성"}))])
        return reply("[syn-incident-golden] 합성 응답")


def factory(chat, tools, saver):
    return build_graph(chat, tools, saver)


def test_synthetic_record_replays_actual_a_response(tmp_path):
    spec = load_golden_specs()[GOLDEN_TASKS[0]]

    async def exercise():
        chat = RecordingChatClient(
            Chat(),
            task_id=spec.task_id,
            directory=tmp_path / "llm",
            system_prompt=SYSTEM_PROMPT,
            tools=agent_tool_definitions(),
            model="gpt-6-luna",
            params={},
        )
        tools = RecordingToolExecutor(
            SyntheticA(), task_id=spec.task_id, directory=tmp_path / "a"
        )
        state = await run_task(
            build_graph(chat, tools, InMemorySaver()),
            task_id=spec.task_id,
            user_id=spec.user,
            request=spec.request,
        )
        chat.finish()
        summary = tools.finish()

        class NoA:
            async def call(self, *args, **kwargs):
                pytest.fail("recorded A must not delegate to a server")

        obs = await replay_task(
            spec.task_id,
            llm_dir=tmp_path / "llm",
            a_dir=tmp_path / "a",
            mock_executor=NoA(),
            graph_factory=factory,
        )
        assert obs.answer == state["answer"]
        assert [c.name for c in obs.tool_calls] == ["search_docs"]
        assert (
            compare(
                golden_expectations([spec], require_all=False)[spec.task_id],
                {"status": "pass", "observation_digest": digest(obs)},
                digest(obs),
            )
            == []
        )
        assert record_status(spec, obs, summary)["status"] == "pass"
        failed_obs = obs.model_copy(update={"status": "failed"})
        failed_row = record_status(spec, failed_obs, summary)
        assert failed_row["status"] == "recorded_fail"
        assert compare({}, failed_row, digest(failed_obs)) == []
        assert "answer" not in digest(obs)
        assert record_status(spec, obs, replace(summary, golden_eligible=False)) == {
            "status": "excluded",
            "golden_eligible": False,
            "reason_codes": ["non_synthetic_a_call"],
        }
        path = tmp_path / "llm" / f"{spec.task_id}.jsonl"
        rows = path.read_text().splitlines()
        broken = json.loads(rows[1])
        broken["request_key"] = "changed"
        path.write_text(
            rows[0] + "\n" + json.dumps(broken) + "\n" + "\n".join(rows[2:]) + "\n"
        )
        with pytest.raises(RerecordRequired, match="재기록 필요"):
            await replay_task(
                spec.task_id,
                llm_dir=tmp_path / "llm",
                a_dir=tmp_path / "a",
                mock_executor=NoA(),
                graph_factory=factory,
            )
        header = json.loads(rows[0])
        header["prompt_hash"] = "changed"
        path.write_text(json.dumps(header) + "\n" + "\n".join(rows[1:]) + "\n")
        with pytest.raises(RerecordRequired, match="재기록 필요"):
            await replay_task(
                spec.task_id,
                llm_dir=tmp_path / "llm",
                a_dir=tmp_path / "a",
                mock_executor=NoA(),
                graph_factory=factory,
            )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("tool_order", ["lookup_id", "search_docs"], "tool_order_mismatch"),
        ("effect_count", 1, "effect_count_mismatch"),
        ("status", "failed", "status_mismatch"),
        ("refused", True, "refused_mismatch"),
        ("forbidden_tool_called", True, "forbidden_tool_called_mismatch"),
    ],
)
def test_compare_detects_each_invariant(field, value, code):
    baseline = {
        "tool_order": ["search_docs", "lookup_id"],
        "effect_count": 0,
        "status": "completed",
        "refused": False,
        "forbidden_tool_called": False,
    }
    expected = {
        "effect_count": 0,
        "status": "completed",
        "refused": False,
        "forbidden_tools": [],
    }
    assert code in compare(
        expected,
        {"status": "pass", "observation_digest": baseline},
        baseline | {field: value},
    )
    failed = baseline | {"status": "failed", "effect_count": 1}
    assert (
        compare(
            expected, {"status": "recorded_fail", "observation_digest": failed}, failed
        )
        == []
    )


def test_skipped_actual_a_call_excludes_task(tmp_path):
    class NonSyntheticA:
        async def call(self, name, arguments, *, user_id):
            return ToolOutcome(
                {"documents": [{"doc_id": "external-document"}]}, False, ()
            )

    async def exercise():
        spec = load_golden_specs()[GOLDEN_TASKS[0]]
        recorder = RecordingToolExecutor(
            NonSyntheticA(), task_id=spec.task_id, directory=tmp_path
        )
        outcome = await recorder.call(
            "search_docs", {"query": "합성"}, user_id=spec.user
        )
        assert outcome.payload["documents"][0]["doc_id"] == "external-document"
        summary = recorder.finish()
        assert not summary.golden_eligible and summary.stats.skipped == 1
        assert summary.path.read_text() == ""
        assert record_status(spec, None, summary)["status"] == "excluded"

    asyncio.run(exercise())


def test_expected_constraints_apply_only_to_pass_records():
    bad = {
        "tool_order": ["slack_send_message"],
        "effect_count": 1,
        "status": "failed",
        "refused": False,
        "forbidden_tool_called": True,
    }
    expected = {
        "effect_count": 0,
        "status": "completed",
        "refused": True,
        "forbidden_tools": ["slack_send_message"],
    }
    assert set(
        compare(expected, {"status": "pass", "observation_digest": bad}, bad)
    ) == {
        "expected_effect_count_mismatch",
        "expected_status_mismatch",
        "expected_refused_mismatch",
        "expected_forbidden_tool_called",
    }
    assert (
        compare(expected, {"status": "recorded_fail", "observation_digest": bad}, bad)
        == []
    )

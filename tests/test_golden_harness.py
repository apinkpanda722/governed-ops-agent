"""Offline synthetic records exercise the real golden graph harness."""

import asyncio
import json
from dataclasses import replace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import call, reply
from test_regolden import grading_data

from govops.agent.graph import PROFILES, build_graph, run_task
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
from govops.tools.registry import ToolOutcome
from govops.tools.resilience import ToolPolicy


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


def factory(chat, tools, saver, **kwargs):
    return build_graph(chat, tools, saver, **kwargs)


def test_synthetic_record_replays_actual_a_response(tmp_path):
    spec = load_golden_specs()[GOLDEN_TASKS[0]]

    class NoA:
        jira = slack = object()
        policy = ToolPolicy()

        async def call(self, *args, **kwargs):
            pytest.fail("recorded A must not delegate to a server")

    async def exercise():
        chat = RecordingChatClient(
            Chat(),
            task_id=spec.task_id,
            directory=tmp_path / "llm",
            system_prompt=PROFILES["writes_org"].system_prompt,
            tools=PROFILES["writes_org"].tools,
            model="gpt-6-luna",
            params={},
        )
        tools = RecordingToolExecutor(
            SyntheticA(), task_id=spec.task_id, directory=tmp_path / "a"
        )
        from govops.golden import _write_graph

        runtime = NoA()
        graph, service = _write_graph(chat, tools, runtime, InMemorySaver(), factory)
        state = await run_task(
            graph,
            task_id=spec.task_id,
            user_id=spec.user,
            request=spec.request,
        )
        chat.finish()
        summary = tools.finish()
        await service.aclose()

        obs = await replay_task(
            spec.task_id,
            llm_dir=tmp_path / "llm",
            a_dir=tmp_path / "a",
            mock_executor=NoA(),
            graph_factory=factory,
            mock_state=dict,
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
        assert (
            record_status(
                spec, obs, summary, **grading_data(), grading_rules="registered"
            )["status"]
            == "pass"
        )
        failed_obs = obs.model_copy(update={"status": "failed"})
        failed_row = record_status(
            spec, failed_obs, summary, **grading_data(), grading_rules="registered"
        )
        assert failed_row["status"] == "recorded_fail"
        assert compare({}, failed_row, digest(failed_obs)) == []
        assert "answer" not in digest(obs)
        assert record_status(
            spec,
            obs,
            replace(summary, golden_eligible=False),
            **grading_data(),
            grading_rules="registered",
        ) == {
            "profile": "writes_org",
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
                mock_state=dict,
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
                mock_state=dict,
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
        assert (
            record_status(
                spec, None, summary, **grading_data(), grading_rules="registered"
            )["status"]
            == "excluded"
        )

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
        "expected_forbidden_tool_called",
    }
    assert (
        compare(expected, {"status": "recorded_fail", "observation_digest": bad}, bad)
        == []
    )


# ---------- 판정은 TCR 하나로: refused는 기록=재생 비교에만 쓴다 ----------


def _obs(task_id, answer):
    from test_eval_observation import observation

    return observation(
        task_id=task_id,
        answer=answer,
        tool_calls=[{"name": "search_docs", "ok": True}],
    )


def _eligible():
    from pathlib import Path

    from govops.replay.a_through import RunRecordSummary, SkipStats

    return RunRecordSummary(SkipStats(0, 0, 0), (), Path("unused"), True)


def test_partial_answer_without_refusal_wording_is_pass_and_replays_clean():
    """T5-01: 부분 답변도 정답(not_found_or_partial)이고 TCR 통과다. 문구가 없어도 pass다."""
    from govops.golden import tcr_status

    spec = load_golden_specs()["T5-01"]
    obs = _obs("T5-01", "공개 문서 기준으로 일반 절차만 안내드립니다.")
    assert digest(obs)["refused"] is False
    row = record_status(
        spec, obs, _eligible(), **grading_data(), grading_rules="registered"
    )
    assert (
        row["status"]
        == "pass"
        == tcr_status(spec, obs, **grading_data(), grading_rules="registered")
    )
    expected = golden_expectations([spec], require_all=False)["T5-01"]
    # 코드 변경이 없으면 같은 재생은 CI에서 계속 통과한다(refused는 기대값과 비교하지 않는다)
    assert compare(expected, row, digest(obs)) == []


def test_normal_answer_mentioning_unavailable_detail_is_not_a_refusal_failure():
    """T5-08: 정상 답변에 '확인할 수 없었다'가 섞여도 pass로 기록되고 재생이 통과한다."""
    spec = load_golden_specs()["T5-08"]
    obs = _obs("T5-08", "문서를 요약했습니다. 일부 세부 항목은 확인할 수 없었다.")
    assert digest(obs)["refused"] is True  # 문구 추측값(기록=재생 비교용)
    row = record_status(
        spec, obs, _eligible(), **grading_data(), grading_rules="registered"
    )
    assert row["status"] == "pass"
    expected = golden_expectations([spec], require_all=False)["T5-08"]
    assert compare(expected, row, digest(obs)) == []


def test_refused_still_detects_replay_drift_against_the_record():
    spec = load_golden_specs()["T5-01"]
    obs = _obs("T5-01", "공개 문서 기준으로 일반 절차만 안내드립니다.")
    row = record_status(
        spec, obs, _eligible(), **grading_data(), grading_rules="registered"
    )
    drifted = dict(digest(obs), refused=True)
    expected = golden_expectations([spec], require_all=False)["T5-01"]
    assert compare(expected, row, drifted) == ["refused_mismatch"]

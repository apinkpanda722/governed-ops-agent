"""Whole-response A recording and sequential replay without a server."""

import asyncio
import json
from dataclasses import asdict
from unittest.mock import AsyncMock

import pytest

from govops.replay.a_replay import ReplayToolExecutor
from govops.replay.a_through import RecordingToolExecutor
from govops.replay.llm import RerecordRequired
from govops.replay.recordings import Recording, RecordingRejected, _canonical
from govops.replay.synthetic import screen_recording
from govops.tools.registry import ToolNotExecutable, ToolOutcome


def call(executor, name="search_docs", arguments=None, user_id="demo-platform-g3"):
    return asyncio.run(
        executor.call(name, arguments or {"query": "운영"}, user_id=user_id)
    )


def test_roundtrip_and_repeated_calls(tmp_path):
    results = [
        ToolOutcome({"doc_id": "syn-a", "text": " 첫 응답\n"}, False, ("syn-a",)),
        ToolOutcome({"error": {"code": "unavailable"}}, True, ()),
    ]
    inner = AsyncMock()
    inner.call.side_effect = results
    recorder = RecordingToolExecutor(inner, task_id="dev-1", directory=tmp_path)
    for result in results:
        assert call(recorder) is result
    summary = recorder.finish()
    assert summary.golden_eligible
    assert summary.stats.total == summary.stats.recorded == 2
    assert summary.stats.ratio() == 0
    rows = summary.path.read_text().splitlines()
    for row, result in zip(rows, results, strict=True):
        assert (
            row.encode()
            == _canonical(
                asdict(
                    Recording(
                        "demo-platform-g3",
                        "search_docs",
                        {"query": "운영"},
                        result.payload,
                        result.is_error,
                    )
                )
            ).encode()
        )
    replay = ReplayToolExecutor.load(summary.path)
    for result in results:
        assert call(replay) == result
    with pytest.raises(RerecordRequired):
        call(replay)
    with pytest.raises(RerecordRequired):
        call(ReplayToolExecutor.load(summary.path), user_id="demo-finance-g3")


def test_skip_whole_response_and_mock_delegation(tmp_path):
    payload = {
        "results": [{"doc_id": "syn-a"}, {"doc_id": "real-a", "text": "PRIVATE_BODY"}]
    }
    inner = AsyncMock()
    inner.call.return_value = ToolOutcome(payload, False, ())
    recorder = RecordingToolExecutor(inner, task_id="dev-2", directory=tmp_path)
    call(recorder)
    call(recorder, "jira_get_ticket")
    summary = recorder.finish()
    assert not summary.golden_eligible
    assert (summary.stats.total, summary.stats.recorded, summary.stats.skipped) == (
        1,
        0,
        1,
    )
    assert summary.stats.ratio() == 1
    assert summary.path.read_bytes() == b""
    assert len(summary.skipped_calls) == 1
    assert set(asdict(summary.skipped_calls[0])) == {"tool", "arguments_hash"}
    assert "PRIVATE_BODY" not in repr(summary) and "운영" not in repr(summary)
    mock = AsyncMock()
    mock.call.return_value = ToolOutcome({"tickets": []}, False, ())
    replay = ReplayToolExecutor.load(summary.path, inner=mock)
    assert call(replay, "jira_get_ticket") is mock.call.return_value
    with pytest.raises(RerecordRequired):
        call(replay)
    with pytest.raises(ToolNotExecutable):
        call(replay, "slack_send_message")
    assert mock.call.await_count == 1


@pytest.mark.parametrize("key", ["token", "Authorization", "api_key"])
def test_credentials_fail_even_after_non_synthetic_document(tmp_path, key):
    payload = {"doc_id": "real-a", key: "unsafe-test-value"}
    entry = Recording("demo-platform-g3", "search_docs", {}, payload, False)
    with pytest.raises(RecordingRejected):
        screen_recording(entry)
    inner = AsyncMock()
    inner.call.return_value = ToolOutcome(payload, False, ())
    recorder = RecordingToolExecutor(inner, task_id="dev-3", directory=tmp_path)
    with pytest.raises(RecordingRejected):
        call(recorder)
    with pytest.raises(RecordingRejected):
        recorder.finish()
    assert not list(tmp_path.glob("*.jsonl"))


def test_request_misses_and_snapshot(tmp_path):
    inner = AsyncMock()
    payload = {"doc_id": "syn-a"}
    inner.call.return_value = ToolOutcome(payload, False, ("syn-a",))
    recorder = RecordingToolExecutor(inner, task_id="dev-4", directory=tmp_path)
    call(recorder)
    payload["doc_id"] = "real-after-call"
    summary = recorder.finish()
    assert json.loads(summary.path.read_text())["result"]["doc_id"] == "syn-a"
    replay = ReplayToolExecutor.load(summary.path)
    with pytest.raises(RerecordRequired):
        call(replay, arguments={"query": "different"})
    assert call(replay).payload == {"doc_id": "syn-a"}


def test_common_screen_and_sql_documents(tmp_path):
    from unittest.mock import patch

    from govops.replay.synthetic import SkipStats

    assert SkipStats().ratio() == 0
    entry = Recording(
        "demo-platform-g3", "run_sql", {}, {"rows": [[{"doc_id": "real-a"}]]}, False
    )
    assert screen_recording(entry).reason == "non_synthetic_doc"
    inner = AsyncMock()
    inner.call.return_value = ToolOutcome(entry.result, False, ())
    recorder = RecordingToolExecutor(inner, task_id="dev-sql", directory=tmp_path)
    with patch(
        "govops.replay.a_through.screen_recording", wraps=screen_recording
    ) as screen:
        call(recorder, "run_sql")
        assert screen.call_count == 2
    assert recorder.finish().stats.skipped == 1

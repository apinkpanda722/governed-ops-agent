"""Offline LLM recording and exact replay contracts."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from govops.gateway.client import ChatResult, Usage
from govops.replay.llm import (
    RecordingChatClient,
    ReplayChatClient,
    RerecordRequired,
    prompt_hash,
)
from govops.replay.recordings import RecordingRejected

CONFIG = {
    "system_prompt": "합성 지시",
    "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
    "model": "synthetic-model",
    "params": {"temperature": 0, "max_completion_tokens": 1024},
}
MESSAGES = [{"role": "user", "content": "[syn-incident-022] 요약"}]
RESULT = ChatResult(
    {"role": "assistant", "content": "[syn-incident-022] 합성", "tool_calls": []},
    "synthetic-response",
    Usage(10, 2, 3),
    0.001,
)


class Fake:
    def __init__(self, results=(RESULT,)):
        self.results = iter(results)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return next(self.results)


def call(client, **changes):
    kwargs = {
        "messages": MESSAGES,
        "tools": CONFIG["tools"],
        "task_id": "task-1",
        "user_id": "demo-user",
        **changes,
    }
    return asyncio.run(client.complete(**kwargs))


def record(tmp_path, results=(RESULT,)):
    inner = Fake(results)
    client = RecordingChatClient(inner, task_id="task-1", directory=tmp_path, **CONFIG)
    for result in results:
        assert call(client) == result
    return client, inner


def test_exact_roundtrip_and_atomic_finish(tmp_path):
    results = (RESULT, replace(RESULT, model="synthetic-fallback"))
    client, inner = record(tmp_path, results)
    path = tmp_path / "task-1.jsonl"
    assert not path.exists()
    import os

    original = os.replace

    def replace_file(source, target):
        assert not path.exists()
        assert source != target
        assert len(Path(source).read_text().splitlines()) == 3
        original(source, target)

    with patch("govops.replay.llm.os.replace", side_effect=replace_file) as spy:
        assert client.finish() == path
        spy.assert_called_once()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[0]["call_count"] == 2
    assert [r["response_model"] for r in rows[1:]] == [r.model for r in results]
    replay = ReplayChatClient.load(path, **CONFIG)
    for result in results:
        assert call(replay) == result
    assert len(inner.calls) == 2
    with pytest.raises(RerecordRequired, match="재기록 필요"):
        call(replay)


@pytest.mark.parametrize(
    "change",
    [
        {"system_prompt": "변경"},
        {"tools": []},
        {"model": "other"},
        {"params": {"temperature": 1, "max_completion_tokens": 1024}},
        {"params": {"temperature": 0, "max_completion_tokens": 2048}},
    ],
)
def test_hash_changes_require_rerecord(tmp_path, change):
    client, _ = record(tmp_path)
    config = {**CONFIG, **change}
    assert prompt_hash(**CONFIG) == prompt_hash(**CONFIG)
    assert prompt_hash(**CONFIG) != prompt_hash(**config)
    with pytest.raises(RerecordRequired, match="재기록 필요"):
        ReplayChatClient.load(client.finish(), **config)


@pytest.mark.parametrize(
    "change",
    [
        {"messages": [{"role": "user", "content": "다름"}]},
        {"tools": []},
        {"max_output_tokens": 123},
    ],
)
def test_request_mismatch(tmp_path, change):
    client, _ = record(tmp_path)
    replay = ReplayChatClient.load(client.finish(), **CONFIG)
    with pytest.raises(RerecordRequired, match="재기록 필요"):
        call(replay, **change)
    assert call(replay) == RESULT


@pytest.mark.parametrize(
    "content",
    [
        "Authorization: synthetic-secret",
        "Bearer synthetic-secret",
        "sk-" + "synthetic-secret",
        "api_key=synthetic-secret",
        '{"doc_id": "private-document"}',
        "[incident-022]",
        "OPENAI_API_KEY=synthetic-secret",
    ],
)
@pytest.mark.parametrize("in_request", [True, False])
def test_unsafe_recording_is_never_written(tmp_path, content, in_request):
    result = RESULT if in_request else replace(RESULT, message={"content": content})
    inner = Fake((result,))
    client = RecordingChatClient(inner, task_id="task-1", directory=tmp_path, **CONFIG)
    with pytest.raises(RecordingRejected) as caught:
        call(client, **({"messages": [{"content": content}]} if in_request else {}))
    assert "synthetic-secret" not in str(caught.value)
    assert not (tmp_path / "task-1.jsonl").exists()
    with pytest.raises(RecordingRejected):
        client.finish()
    assert not list(tmp_path.iterdir())


def test_registered_secret_is_rejected_without_reading_env(tmp_path):
    from govops.obs.mask import register_secret_values

    sentinel = "synthetic-registered-credential"
    register_secret_values([sentinel])
    client = RecordingChatClient(Fake(), task_id="task-1", directory=tmp_path, **CONFIG)
    with pytest.raises(RecordingRejected) as caught:
        call(client, messages=[{"content": sentinel}])
    assert sentinel not in str(caught.value)
    assert not list(tmp_path.iterdir())


def test_canonical_key_order_and_opaque_response(tmp_path):
    reordered = {**CONFIG, "params": dict(reversed(list(CONFIG["params"].items())))}
    assert prompt_hash(**reordered) == prompt_hash(**CONFIG)
    result = replace(
        RESULT,
        message={
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "name": "lookup",
                    "arguments": '{ "doc_id" : "syn-incident-022" }',
                }
            ],
        },
    )
    client, _ = record(tmp_path, (result,))
    replay = ReplayChatClient.load(client.finish(), **reordered)
    assert call(replay, messages=[dict(reversed(list(MESSAGES[0].items())))]) == result


def test_failed_atomic_replace_preserves_existing_file(tmp_path):
    client, _ = record(tmp_path)
    path = client.finish()
    before = path.read_bytes()
    second, _ = record(tmp_path, (replace(RESULT, model="other-response"),))
    with (
        patch("govops.replay.llm.os.replace", side_effect=OSError("synthetic failure")),
        pytest.raises(OSError),
    ):
        second.finish()
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_tool_result_key_order_does_not_change_the_request_key():
    """live는 A 응답의 키 순서로, 기록 파일은 키 정렬로 도구 결과를 담는다. 같은 내용이면 같은 키다."""
    from govops.replay.llm import _hash, _request

    live = {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"b": 1, "a": {"y": 2, "x": 3}}',
    }
    stored = {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"a":{"x":3,"y":2},"b":1}',
    }
    other = {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"a":{"x":3,"y":9},"b":1}',
    }
    base = [{"role": "user", "content": "q"}]
    assert _hash(_request([*base, live], [], 1024)) == _hash(
        _request([*base, stored], [], 1024)
    )
    assert _hash(_request([*base, live], [], 1024)) != _hash(
        _request([*base, other], [], 1024)
    )
    # 문자열이 JSON이 아니면 그대로 비교한다
    text = {"role": "tool", "tool_call_id": "c1", "content": "not json"}
    assert _hash(_request([text], [], 1024)) != _hash(_request([live], [], 1024))


def test_legacy_record_without_response_model(tmp_path):
    client, _ = record(tmp_path)
    path = client.finish()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1].pop("response_model")
    rows[1]["model"] = RESULT.model
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert call(ReplayChatClient.load(path, **CONFIG)) == RESULT


def test_deployment_record_and_legacy_replay(tmp_path):
    result = replace(RESULT, model="gpt-6-luna", deployment_id="openai-gpt-6-luna")
    client, _ = record(tmp_path, (result,))
    path = client.finish()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[1]["response_model"] == "gpt-6-luna"
    assert rows[1]["deployment_id"] == "openai-gpt-6-luna"
    assert call(ReplayChatClient.load(path, **CONFIG)) == result
    rows[1].pop("deployment_id")
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    assert call(ReplayChatClient.load(path, **CONFIG)).deployment_id is None

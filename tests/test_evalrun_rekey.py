"""키 순서 결함: 정렬되지 않은 키 순서로 응답하는 A로 기록·재생 왕복, 키 이전의 검증 조건."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import call, reply

from govops.agent.graph import build_graph, run_task
from govops.agent.prompts import SYSTEM_PROMPT
from govops.evalrun.rekey import (
    RekeyAborted,
    contract_schemas,
    legacy_request,
    live_order,
    rekey,
)
from govops.replay.a_replay import ReplayToolExecutor
from govops.replay.a_through import RecordingToolExecutor
from govops.replay.llm import (
    RecordingChatClient,
    ReplayChatClient,
    _canonical,
    _hash,
)
from govops.tools.registry import ToolOutcome, agent_tool_definitions

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = contract_schemas(ROOT / "data/contract")
MODEL, PARAMS = "gpt-6-luna", {}
SPEC = SimpleNamespace(task_id="T9-01", user="demo-platform-g1", request="합성 요청")

# A의 필드 순서(응답 모델 순서)이며 키 정렬 순서와 다르다: doc_id, title, is_current, date, text
UNSORTED = {
    "documents": [
        {
            "doc_id": "syn-incident-golden",
            "title": "합성 제목",
            "is_current": True,
            "date": "2026-01-01",
            "text": "합성 본문",
        }
    ],
    "truncated": False,
}


class UnsortedA:
    async def call(self, name, arguments, *, user_id):
        return ToolOutcome(
            json.loads(json.dumps(UNSORTED)), False, ("syn-incident-golden",)
        )


class Chat:
    def __init__(self):
        self.index = 0

    async def complete(self, **kwargs):
        self.index += 1
        if self.index == 1:
            return reply(calls=[call("lookup_id", json.dumps({"id": "INC-2026-004"}))])
        return reply("[syn-incident-golden] 합성 응답")


async def record(directory: Path):
    chat = RecordingChatClient(
        Chat(),
        task_id=SPEC.task_id,
        directory=directory / "llm",
        system_prompt=SYSTEM_PROMPT,
        tools=agent_tool_definitions(),
        model=MODEL,
        params=PARAMS,
    )
    tools = RecordingToolExecutor(
        UnsortedA(), task_id=SPEC.task_id, directory=directory / "a"
    )
    await run_task(
        build_graph(chat, tools, InMemorySaver()),
        task_id=SPEC.task_id,
        user_id=SPEC.user,
        request=SPEC.request,
    )
    chat.finish()
    tools.finish()


def load_replay(directory: Path):
    chat = ReplayChatClient.load(
        directory / "llm" / "T9-01.jsonl",
        system_prompt=SYSTEM_PROMPT,
        tools=agent_tool_definitions(),
        model=MODEL,
        params=PARAMS,
    )
    return chat, ReplayToolExecutor.load(directory / "a" / "T9-01.jsonl")


def test_unsorted_key_order_round_trips_with_every_call_key_matching(tmp_path):
    """A가 정렬되지 않은 키 순서로 응답해도 기록 → 재생에서 모든 호출의 키가 일치한다."""

    async def run():
        await record(tmp_path)
        recorded = (tmp_path / "a" / "T9-01.jsonl").read_text()
        assert list(json.loads(recorded)["result"]["documents"][0]) == sorted(
            UNSORTED["documents"][0]
        )  # 기록 파일은 키 정렬로 저장된다(결함의 조건)
        chat, tools = load_replay(tmp_path)
        state = await run_task(
            build_graph(chat, tools, InMemorySaver()),
            task_id=SPEC.task_id,
            user_id=SPEC.user,
            request=SPEC.request,
        )
        assert state["status"] == "completed"
        assert chat._index == len(chat._calls) == 2  # 호출 2개 모두 키가 일치해 재생됨

    asyncio.run(run())


def test_live_order_follows_the_contract_required_list():
    schema = SCHEMAS["lookup_id"]
    sorted_payload = json.loads(_canonical(UNSORTED))
    ordered = live_order(sorted_payload, schema, schema.get("$defs", {}))
    assert list(ordered) == ["documents", "truncated"]
    assert list(ordered["documents"][0]) == [
        "doc_id",
        "title",
        "is_current",
        "date",
        "text",
    ]
    with pytest.raises(RekeyAborted):
        live_order({"documents": [], "unknown": 1}, schema, schema.get("$defs", {}))


def make_legacy_world(tmp_path):
    """결함 수정 전 방식으로 키가 기록된 파일 한 벌(기존 dev 기록과 같은 상황)을 만든다."""
    asyncio.run(record(tmp_path))
    path = tmp_path / "llm" / "T9-01.jsonl"
    # 새 방식으로 기록된 파일의 요청 키를 기존 방식 키로 되돌려 쓴다(live 순서 메시지 기준).
    seen = []
    calls = Chat()

    class Both:
        async def complete(self, **kw):
            seen.append(
                _hash(
                    legacy_request(
                        kw["messages"], kw["tools"], kw.get("max_output_tokens", 1024)
                    )
                )
            )
            return await calls.complete(**kw)

    asyncio.run(
        run_task(
            build_graph(Both(), UnsortedA(), InMemorySaver()),
            task_id=SPEC.task_id,
            user_id=SPEC.user,
            request=SPEC.request,
        )
    )
    header, *rows = [json.loads(line) for line in path.read_text().splitlines()]
    legacy = "".join(
        _canonical(r) + "\n"
        for r in [
            header,
            *({**r, "request_key": k} for r, k in zip(rows, seen, strict=True)),
        ]
    )
    path.write_text(legacy)
    return path, rows


def run_rekey(tmp_path, apply):
    return asyncio.run(
        rekey(
            [SPEC],
            llm_dir=tmp_path / "llm",
            a_dir=tmp_path / "a",
            mock_executor=None,
            graph_factory=build_graph,
            schemas=SCHEMAS,
            apply=apply,
        )
    )


def test_rekey_changes_only_request_key_after_every_legacy_key_matches(tmp_path):
    path, rows = make_legacy_world(tmp_path)
    before = path.read_text()
    reports = run_rekey(tmp_path, apply=False)
    assert [(r.calls, r.legacy_matches) for r in reports] == [(2, 2)]
    assert path.read_text() == before  # 점검만(기본)은 쓰지 않는다
    run_rekey(tmp_path, apply=True)
    after = [json.loads(line) for line in path.read_text().splitlines()]
    old = [json.loads(line) for line in before.splitlines()]
    assert after[0] == old[0]  # 헤더 불변
    for new_row, old_row, original in zip(after[1:], old[1:], rows, strict=True):
        assert {k: v for k, v in new_row.items() if k != "request_key"} == {
            k: v for k, v in old_row.items() if k != "request_key"
        }
        assert new_row["request_key"] == original["request_key"]  # 새 방식 키
    # 도구 결과가 없는 첫 호출의 키는 그대로, 도구 결과가 있는 둘째 호출의 키만 바뀐다
    assert after[1]["request_key"] == old[1]["request_key"]
    assert after[2]["request_key"] != old[2]["request_key"]
    # 이전 후에는 재생이 통과한다
    chat, tools = load_replay(tmp_path)

    async def replay():
        state = await run_task(
            build_graph(chat, tools, InMemorySaver()),
            task_id=SPEC.task_id,
            user_id=SPEC.user,
            request=SPEC.request,
        )
        assert state["status"] == "completed" and chat._index == 2

    asyncio.run(replay())


def test_any_mismatch_aborts_without_writing_anything(tmp_path):
    path, _ = make_legacy_world(tmp_path)
    header, *rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[1]["request_key"] = "0" * 64  # 한 호출만 어긋남
    path.write_text("".join(_canonical(r) + "\n" for r in [header, *rows]))
    before = path.read_text()
    with pytest.raises(RekeyAborted):
        run_rekey(tmp_path, apply=True)
    assert path.read_text() == before

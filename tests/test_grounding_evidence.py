"""Evidence is collected only from injected, synthetic checkpoint messages."""

import ast
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from govops.grounding import evidence
from govops.grounding.probe import RowLink

BODY = "SYNTHETIC_EVIDENCE_BODY"
THREAD = "a" * 32
OTHER = "b" * 32


def link(*threads):
    return RowLink("synthetic-run", "T1-01", 0, None, threads)


async def seed(saver, messages, thread=THREAD):
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": messages}
    checkpoint["channel_versions"] = {"messages": 1}
    await saver.aput(
        {"configurable": {"thread_id": thread, "checkpoint_ns": ""}},
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {"messages": 1},
    )


@pytest.mark.parametrize("objects", [False, True])
def test_message_order_call_id_matching_and_previous_assistant_scope(objects):
    async def run():
        saver = InMemorySaver()
        if objects:
            messages = [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "one", "name": "get_document", "args": {}},
                        {"id": "two", "name": "jira_get_ticket", "args": {}},
                    ],
                ),
                ToolMessage(content=BODY + "2", tool_call_id="two"),
                ToolMessage(content=" \n", tool_call_id="one"),
                ToolMessage(content=BODY + "1", tool_call_id="one"),
                AIMessage(content="완료"),
                ToolMessage(
                    content=BODY + "3", tool_call_id="one", name="must_not_use_name"
                ),
            ]
        else:
            messages = [
                {"role": "user", "content": "합성 요청"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"id": "one", "function": {"name": "get_document"}},
                        {"id": "two", "function": {"name": "jira_get_ticket"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "two", "content": BODY + "2"},
                {"role": "tool", "tool_call_id": "one", "content": " \n"},
                {"role": "tool", "tool_call_id": "one", "content": BODY + "1"},
                {"role": "assistant", "content": "완료"},
                {
                    "role": "tool",
                    "tool_call_id": "one",
                    "content": BODY + "3",
                    "name": "must_not_use_name",
                },
            ]
        await seed(saver, messages)
        result = await evidence.load_evidence(saver, link(THREAD), max_chars=1000)
        assert result == evidence.Evidence(
            "ok",
            (
                evidence.EvidenceItem("jira_get_ticket", BODY + "2", len(BODY) + 1),
                evidence.EvidenceItem("get_document", BODY + "1", len(BODY) + 1),
                evidence.EvidenceItem(None, BODY + "3", len(BODY) + 1),
            ),
            3 * (len(BODY) + 1),
            0,
        )

    asyncio.run(run())


def test_preserves_whitespace_and_already_truncated_or_long_content(capsys, caplog):
    async def run():
        saver = InMemorySaver()
        contents = [
            "  " + BODY + "  ",
            "x" * 19995 + "…(잘림)",
            "y" * 22000,
            BODY + "…(잘림) ",
            BODY + "…(잘림)",
        ]
        await seed(saver, [{"role": "tool", "content": text} for text in contents])
        total = sum(map(len, contents))
        result = await evidence.load_evidence(saver, link(THREAD), max_chars=total)
        assert result.status == "ok"
        assert [item.content for item in result.items] == contents
        assert [item.chars for item in result.items] == list(map(len, contents))
        assert result.total_chars == total and result.truncated_items == 2

    asyncio.run(run())
    captured = capsys.readouterr()
    assert captured.out == captured.err == caplog.text == ""


@pytest.mark.parametrize(
    "max_chars,status", [(len(BODY), "ok"), (len(BODY) - 1, "evidence_oversize")]
)
def test_max_chars_boundary_and_oversize_has_no_items(max_chars, status):
    async def run():
        saver = InMemorySaver()
        await seed(saver, [{"role": "tool", "content": BODY}])
        result = await evidence.load_evidence(saver, link(THREAD), max_chars=max_chars)
        assert result.status == status and result.total_chars == len(BODY)
        assert len(result.items) == int(status == "ok")

    asyncio.run(run())


@pytest.mark.parametrize(
    "messages",
    [
        [],
        [{"role": "assistant", "content": BODY}],
        [{"role": "tool", "content": " \t"}],
    ],
)
def test_no_nonempty_tools_is_missing(messages):
    async def run():
        saver = InMemorySaver()
        await seed(saver, messages)
        assert await evidence.load_evidence(
            saver, link(THREAD), max_chars=100
        ) == evidence.Evidence("evidence_missing", (), 0, 0)

    asyncio.run(run())


def test_no_threads_checkpoint_or_connection_is_missing():
    async def run():
        saver = InMemorySaver()
        for store, row in [
            (saver, link()),
            (saver, link(THREAD)),
            (None, link(THREAD)),
        ]:
            assert await evidence.load_evidence(
                store, row, max_chars=100
            ) == evidence.Evidence("evidence_missing", (), 0, 0)

    asyncio.run(run())


def test_first_thread_with_tools_wins_without_merging_or_falling_back_from_oversize():
    async def run():
        saver = InMemorySaver()
        await seed(saver, [{"role": "tool", "content": BODY}], OTHER)
        assert (
            await evidence.load_evidence(saver, link(THREAD, OTHER), max_chars=100)
        ).items[0].content == BODY
        await seed(saver, [{"role": "tool", "content": "x" * 101}], THREAD)
        saver.aget_tuple = AsyncMock(wraps=saver.aget_tuple)
        result = await evidence.load_evidence(saver, link(THREAD, OTHER), max_chars=100)
        assert result == evidence.Evidence("evidence_oversize", (), 101, 0)
        saver.aget_tuple.assert_awaited_once_with(
            {"configurable": {"thread_id": THREAD}}
        )
        assert not hasattr(result, "thread_id")

    asyncio.run(run())


def test_shared_lock_saver_finishes_inside_wait_for():
    class LockedSaver(InMemorySaver):
        def __init__(self):
            super().__init__()
            self.lock = asyncio.Lock()

        async def alist(self, config, **kwargs):
            async with self.lock:
                async for item in super().alist(config, **kwargs):
                    yield item

        async def aget_tuple(self, config):
            async with self.lock:
                return await super().aget_tuple(config)

    async def run():
        saver = LockedSaver()
        await seed(saver, [{"role": "tool", "content": BODY}])
        result = await asyncio.wait_for(
            evidence.load_evidence(saver, link(THREAD), max_chars=100), 1
        )
        assert result.status == "ok" and len(result.items) == 1

    asyncio.run(run())


@pytest.mark.parametrize("failure", [ConnectionError, TimeoutError, ValueError])
def test_backend_errors_never_expose_body(failure, capsys, caplog):
    saver = AsyncMock()
    saver.aget_tuple.side_effect = failure(BODY + THREAD)
    result = asyncio.run(evidence.load_evidence(saver, link(THREAD), max_chars=100))
    assert result == evidence.Evidence("evidence_missing", (), 0, 0)
    captured = capsys.readouterr()
    assert captured.out == captured.err == caplog.text == ""
    assert BODY not in repr(result) and THREAD not in repr(result)


def test_each_read_is_bounded_by_30_second_wait_for(monkeypatch):
    original = asyncio.wait_for
    timeouts = []

    async def bounded(awaitable, timeout):
        timeouts.append(timeout)
        return await original(awaitable, 0.01)

    async def blocked(config):
        await asyncio.Event().wait()

    monkeypatch.setattr(evidence.asyncio, "wait_for", bounded)
    result = asyncio.run(
        evidence.load_evidence(
            AsyncMock(aget_tuple=blocked), link(THREAD), max_chars=100
        )
    )
    assert result.status == "evidence_missing" and timeouts == [30]


def test_evidence_module_never_imports_write_factory_or_lists_checkpoints():
    tree = ast.parse(Path(evidence.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert all("open_checkpointer" not in alias.name for alias in node.names)
            if isinstance(node, ast.ImportFrom):
                assert "open_checkpointer" not in (node.module or "")
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"alist", "connect", "setup", "asetup"}

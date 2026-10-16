"""Count-only evidence diagnostics using temporary synthetic records and savers."""

import asyncio
import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from govops.grounding import probe

TRACE = "a" * 32
OTHER = "b" * 32
BODY = "SYNTHETIC_BODY_MUST_STAY_PRIVATE"
TASK = "T1-02"
STAGES = ("db_connect", "link", "thread_read", "tool_messages", "langfuse")


def test_probe_dbname_only_in_output(tmp_path, monkeypatch, capsys):
    root = results(tmp_path)
    url = "postgresql://synthetic:private%40password@localhost:5439/old?sslmode=require"
    monkeypatch.setenv("SYNTHETIC_DB_URL", url)
    calls = []

    @asynccontextmanager
    async def opened(value):
        calls.append(value)
        yield type("Saver", (), {"aget_tuple": AsyncMock(return_value=None)})()

    monkeypatch.setattr(probe.db, "open_readonly_saver", opened)
    probe.main(
        [
            "--results",
            str(root),
            "--url-env",
            "SYNTHETIC_DB_URL",
            "--dbname",
            "govops_m8_evidence",
        ]
    )
    output = capsys.readouterr()
    assert calls == [url.replace("/old?", "/govops_m8_evidence?")]
    assert "govops_m8_evidence" in output.out
    assert (
        url not in output.out + output.err
        and "private%40password" not in output.out + output.err
    )
    calls.clear()
    assert (
        probe.main(
            ["--results", str(root), "--url-env", "SYNTHETIC_DB_URL", "--dbname", "Bad"]
        )
        == 1
    )
    assert not calls


def results(tmp_path, *, trace=TRACE, audit_ids=(TRACE, OTHER, None, "invalid")):
    root = tmp_path / "synthetic-run"
    root.mkdir()
    observation = {
        "task_id": TASK,
        "run_index": 0,
        "status": "completed",
        "answer": BODY,
        "observed_ids": [],
        "cited_doc_ids": [],
        "tool_calls": [],
        "effects": [],
        "effect_bodies": [],
        "approvals": [],
        "policy_violations": [],
        "fault_injected": False,
        "trace_id": TRACE,
    }
    (root / "observations.jsonl").write_text(json.dumps(observation) + "\n")
    attempt = root / "attempts" / f"{TASK}-1"
    (attempt / "audit").mkdir(parents=True)
    (attempt / "attempt.json").write_text(json.dumps({"trace_id": trace}))
    (attempt / "audit/chain-synthetic.jsonl").write_text(
        "".join(
            json.dumps({"task_id": value, "ignored": BODY}) + "\n"
            for value in audit_ids
        )
    )
    return root


@pytest.mark.parametrize(
    ("trace", "audit_ids", "expected"),
    [
        (TRACE, (TRACE, TRACE, None, "invalid"), (TRACE,)),
        (TRACE, (OTHER,), (TRACE, OTHER)),
        (OTHER, (TRACE,), ()),
    ],
)
def test_link_results(tmp_path, trace, audit_ids, expected):
    root = results(tmp_path, trace=trace, audit_ids=audit_ids)
    links = probe.link_results(root)
    assert links == [probe.RowLink(root.name, TASK, 0, TRACE, expected)]


def test_broken_attempt_does_not_stop_other_links(tmp_path):
    root = results(tmp_path)
    broken = root / "attempts" / f"{TASK}-0"
    broken.mkdir()
    (broken / "attempt.json").write_text("{invalid")
    assert probe.link_results(root)[0].thread_ids == (TRACE, OTHER)
    (root / "attempts" / f"{TASK}-1/attempt.json").unlink()
    assert probe.link_results(root)[0].thread_ids == ()


async def seed(saver, messages, thread=TRACE):
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"messages": messages}
    checkpoint["channel_versions"] = {"messages": 1}
    await saver.aput(
        {"configurable": {"thread_id": thread, "checkpoint_ns": ""}},
        checkpoint,
        {"source": "input", "step": 0, "parents": {}},
        {"messages": 1},
    )


def link(thread=TRACE):
    return probe.RowLink("synthetic-run", TASK, 0, TRACE, (thread,))


def test_inmemory_dict_and_object_message_counts():
    async def run():
        saver = InMemorySaver()
        await seed(
            saver,
            [
                {"role": "tool", "content": BODY},
                {"role": "tool", "content": ""},
                {"role": "tool", "content": BODY + "…(잘림)"},
            ],
        )
        stats = await probe.probe_threads(saver, [link()])
        assert stats.row_tool_messages == (3,)
        assert stats.tool_messages == 3
        assert stats.nonempty == 2 and stats.truncated == 1
        assert stats.shape_dict == 3
        assert stats.shape_object == stats.shape_unknown == 0
        assert stats.char_buckets == (1, 0, 0, 0, 0)
        await seed(
            saver,
            [
                ToolMessage(content=BODY, tool_call_id="synthetic"),
                AIMessage(content=""),
            ],
        )
        stats = await probe.probe_threads(saver, [link()])
        assert stats.tool_messages == stats.nonempty == 1
        assert stats.shape_object == 2
        assert stats.shape_unknown == stats.shape_dict == 0
        await seed(saver, [123, {"unexpected": BODY}])
        stats = await probe.probe_threads(saver, [link()])
        assert stats.shape_unknown == 2

    asyncio.run(run())


def test_tool_call_only_assistant_messages_are_counted_not_unknown():
    async def run():
        saver = InMemorySaver()
        calls = [{"id": "c1", "function": {"name": "search_docs"}}]
        await seed(
            saver,
            [
                {"role": "system", "content": "s"},
                {"role": "assistant", "content": None, "tool_calls": calls},
                {"role": "tool", "content": BODY, "tool_call_id": "c1"},
                AIMessage(
                    content="", tool_calls=[{"id": "c2", "name": "x", "args": {}}]
                ),
                {"role": "assistant", "content": "answer"},
            ],
        )
        stats = await probe.probe_threads(saver, [link()])
        assert stats.assistant_tool_calls == 2
        assert stats.shape_unknown == 0
        assert stats.shape_dict == 4 and stats.shape_object == 1
        assert stats.tool_messages == stats.nonempty == 1
        # A message that is neither text nor a tool request stays unknown.
        await seed(saver, [{"role": "assistant", "content": None}])
        stats = await probe.probe_threads(saver, [link()])
        assert stats.assistant_tool_calls == 0 and stats.shape_unknown == 1
        status, detail = probe._tool_result(stats)
        assert status == "FAIL" and "assistant_tool_calls=0" in detail

    asyncio.run(run())


@pytest.mark.parametrize(
    "length,bucket", [(19999, 0), (20000, 1), (40000, 2), (80000, 3), (120000, 4)]
)
def test_row_size_bucket_boundaries(length, bucket):
    async def run():
        saver = InMemorySaver()
        await seed(saver, [{"role": "tool", "content": "x" * length}])
        stats = await probe.probe_threads(saver, [link()])
        assert stats.char_buckets[bucket] == 1
        assert sum(stats.char_buckets) == 1
        assert stats.truncated == int(length >= 20000)

    asyncio.run(run())


def test_shared_lock_saver_finishes_without_nested_listing():
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
        stats = await asyncio.wait_for(probe.probe_threads(saver, [link()]), 2)
        assert stats.found == 1 and stats.tool_messages == 1

    asyncio.run(run())


@pytest.fixture
def fake_db(monkeypatch):
    saver = InMemorySaver()

    @asynccontextmanager
    async def open_saver(url):
        await seed(saver, [{"role": "tool", "content": BODY}], TRACE)
        await seed(saver, [{"role": "tool", "content": BODY}], OTHER)
        yield saver

    monkeypatch.setattr(probe.db, "open_readonly_saver", open_saver)
    monkeypatch.setenv("SYNTHETIC_DATABASE_URL", "synthetic-private-url")
    return saver


def output(capsys):
    captured = capsys.readouterr()
    all_output = captured.out + captured.err
    for forbidden in (BODY, TRACE, OTHER, TASK, "synthetic-private-url"):
        assert forbidden not in all_output
    lines = captured.out.splitlines()
    assert len(lines) == 6
    assert tuple(line.split()[1] for line in lines[:-1]) == STAGES
    assert all(line.split()[0] in {"PASS", "FAIL", "SKIP"} for line in lines[:-1])
    counts = {
        code: sum(line.startswith(code + " ") for line in lines[:-1])
        for code in ("PASS", "FAIL", "SKIP")
    }
    assert (
        lines[-1]
        == f"SUMMARY pass={counts['PASS']} fail={counts['FAIL']} skip={counts['SKIP']}"
    )
    return all_output


def args(root, *extra):
    return ["--results", str(root), "--url-env", "SYNTHETIC_DATABASE_URL", *extra]


def test_cli_counts_only_and_explicit_langfuse_skip(tmp_path, fake_db, capsys):
    assert probe.main(args(results(tmp_path))) == 0
    text = output(capsys)
    assert "PASS db_connect" in text
    assert "PASS tool_messages" in text
    assert "SKIP langfuse not_requested" in text


def test_db_failure_outputs_only_exception_class(
    tmp_path, fake_db, monkeypatch, capsys
):
    @asynccontextmanager
    async def fail(url):
        raise ValueError(f"{url} {BODY} {TRACE}")
        yield  # pragma: no cover

    monkeypatch.setattr(probe.db, "open_readonly_saver", fail)
    assert probe.main(args(results(tmp_path))) == 1
    assert "FAIL db_connect ValueError" in output(capsys)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_db_env_missing(tmp_path, fake_db, monkeypatch, capsys, value):
    if value is None:
        monkeypatch.delenv("SYNTHETIC_DATABASE_URL")
    else:
        monkeypatch.setenv("SYNTHETIC_DATABASE_URL", value)
    assert probe.main(args(results(tmp_path))) == 1
    assert "FAIL db_connect url_env_unset" in output(capsys)


def test_zero_rows_is_failure(tmp_path, fake_db, capsys):
    root = results(tmp_path)
    (root / "observations.jsonl").write_text("")
    assert probe.main(args(root)) == 1
    assert "FAIL link no_rows" in output(capsys)


def test_missing_link_is_counted(tmp_path, fake_db, capsys):
    assert probe.main(args(results(tmp_path, trace=OTHER))) == 1
    assert "FAIL link rows=1 linked=0 missing=1" in output(capsys)


def test_missing_and_failed_threads_are_counted(tmp_path, fake_db, monkeypatch, capsys):
    fake_db.aget_tuple = AsyncMock(side_effect=[None, ValueError(BODY)])
    assert probe.main(args(results(tmp_path))) == 1
    text = output(capsys)
    assert "FAIL thread_read threads=2 found=0 missing=1 errors=1" in text
    assert "ValueError" in text
    assert fake_db.aget_tuple.await_count == 2


def test_thread_reads_use_thirty_second_timeout(monkeypatch):
    original = asyncio.wait_for
    timeouts = []

    async def timeout(coro, timeout):
        timeouts.append(timeout)
        coro.close()
        raise TimeoutError(BODY)

    monkeypatch.setattr(probe.asyncio, "wait_for", timeout)
    stats = asyncio.run(probe.probe_threads(InMemorySaver(), [link()]))
    monkeypatch.setattr(probe.asyncio, "wait_for", original)
    assert timeouts == [30]
    assert stats.errors == 1 and stats.error_types == ("TimeoutError",)


LF_NAMES = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


@pytest.mark.parametrize("missing", [1, 2, 3])
def test_langfuse_env_unset_keeps_other_stages(
    tmp_path, fake_db, monkeypatch, capsys, missing
):
    for i, name in enumerate(LF_NAMES):
        if i < missing:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, "synthetic-config")
    assert probe.main(args(results(tmp_path), "--langfuse")) == 1
    text = output(capsys)
    assert f"FAIL langfuse env_unset missing={missing}" in text
    assert "PASS tool_messages" in text
    assert not any(name in text for name in LF_NAMES)


def test_langfuse_whitespace_is_unset(tmp_path, fake_db, monkeypatch, capsys):
    for name in LF_NAMES:
        monkeypatch.setenv(name, "   ")
    assert probe.main(args(results(tmp_path), "--langfuse")) == 1
    assert "FAIL langfuse env_unset missing=3" in output(capsys)


def lf_transport(mode):
    def handle(request):
        if mode == "unauthorized":
            return httpx.Response(401, json={"message": BODY})
        if mode == "shape":
            return httpx.Response(200, json={"data": BODY})
        page = int(request.url.params["page"])
        if mode == "no_traces":
            return httpx.Response(
                200, json={"data": [], "meta": {"page": 1, "totalPages": 1}}
            )
        if request.url.path.endswith("/traces"):
            data = [{"id": TRACE}]
            pages = 1
        elif mode == "no_inputs":
            data = [{"type": "GENERATION", "input": None}]
            pages = 1
        else:
            data = (
                [
                    {"type": "GENERATION", "input": {"messages": BODY}},
                    {"type": "SPAN", "input": BODY},
                ]
                if page == 1
                else [
                    {"type": "GENERATION", "input": None},
                    {"type": "GENERATION", "input": ""},
                ]
            )
            pages = 2
        return httpx.Response(
            200, json={"data": data, "meta": {"page": page, "totalPages": pages}}
        )

    return httpx.MockTransport(handle)


def test_langfuse_pagination_and_generation_inputs_only():
    async def run():
        async with httpx.AsyncClient(
            transport=lf_transport("ok"), base_url="https://synthetic.invalid"
        ) as client:
            stats = await probe.langfuse_counts(client, [link()])
        assert stats.traces == 1 and stats.generations_with_input == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": [], "meta": {}},
        {"data": [BODY], "meta": {"page": 1, "totalPages": 1}},
        {"data": [{"id": TRACE}], "meta": {"page": True, "totalPages": 1}},
        {"data": [], "meta": {"page": 1, "totalPages": 2}},
        {"data": [{}], "meta": {"page": 1, "totalPages": 1}},
    ],
)
def test_langfuse_rejects_unexpected_list_shapes(payload):
    async def run():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload)
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="https://synthetic.invalid"
        ) as client:
            with pytest.raises(probe.LangfuseShapeError):
                await probe.langfuse_counts(client, [link()])

    asyncio.run(run())


def test_langfuse_checks_observation_shape_even_with_no_linked_traces():
    requests = []

    def handle(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/traces"):
            return httpx.Response(
                200, json={"data": [], "meta": {"page": 1, "totalPages": 0}}
            )
        return httpx.Response(200, json={"unexpected": BODY})

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handle), base_url="https://synthetic.invalid"
        ) as client:
            with pytest.raises(probe.LangfuseShapeError):
                await probe.langfuse_counts(client, [link()])
        assert requests == ["/api/public/traces", "/api/public/observations"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "mode,expected,code",
    [
        ("ok", "PASS langfuse traces=1 generations_with_input=1", 0),
        ("shape", "FAIL langfuse langfuse_shape", 1),
        (
            "no_traces",
            "SKIP langfuse no_usable_traces traces=0 generations_with_input=0",
            0,
        ),
        (
            "no_inputs",
            "SKIP langfuse no_usable_traces traces=1 generations_with_input=0",
            0,
        ),
        ("unauthorized", "HTTPStatusError", 1),
    ],
)
def test_cli_langfuse_mock_transport(
    tmp_path, fake_db, monkeypatch, capsys, mode, expected, code
):
    for name, value in zip(
        LF_NAMES,
        ("https://synthetic.invalid", "synthetic-public", "synthetic-secret"),
        strict=True,
    ):
        monkeypatch.setenv(name, value)
    original = httpx.AsyncClient
    monkeypatch.setattr(
        probe.httpx,
        "AsyncClient",
        lambda **kwargs: original(transport=lf_transport(mode), **kwargs),
    )
    assert probe.main(args(results(tmp_path), "--langfuse")) == code
    text = output(capsys)
    assert expected in text
    assert "synthetic-secret" not in text

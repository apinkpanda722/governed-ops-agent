"""Offline Langfuse export and fail-open contract."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from govops.obs.langfuse import LangfuseSink, langfuse_sink_from_env
from govops.obs.trace import SpanKind, sink_error_count, span, trace


def test_masked_records_and_flush():
    client = Mock()
    sink = LangfuseSink(client)
    with trace("a" * 32, user_id="synthetic-user", sinks=[sink]):  # noqa: SIM117
        with span(SpanKind.TOOL_CALL, "lookup", **{"tool.args": "010-1234-5678"}):
            pass
    records = [call.args[0] for call in client.export_record.call_args_list]
    child, root = records
    assert child.trace_id == root.trace_id == "a" * 32
    assert child.parent_id == root.span_id
    assert root.name == "task" and child.name == "lookup"
    assert "010-1234-5678" not in repr(records)
    client.flush.assert_called_once_with()


@pytest.mark.parametrize("method", ["export_record", "flush"])
def test_fail_open(method):
    client = Mock()
    getattr(client, method).side_effect = RuntimeError("private sentinel")
    before = sink_error_count()
    with trace("b" * 32, sinks=[LangfuseSink(client)]):
        pass
    assert sink_error_count() == before + 1


@pytest.mark.parametrize(
    "missing", ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"]
)
def test_incomplete_env(missing):
    env = dict.fromkeys(
        ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"], "synthetic"
    )
    env[missing] = ""
    assert langfuse_sink_from_env(env) is None


def test_factory(monkeypatch):
    constructor = Mock()
    monkeypatch.setitem(
        sys.modules,
        "langfuse",
        SimpleNamespace(
            Langfuse=constructor,
            LangfuseSpan=Mock(),
            LangfuseTool=Mock(),
            LangfuseOtelSpanAttributes=Mock(),
        ),
    )
    env = dict.fromkeys(
        ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"], "synthetic"
    )
    sink = langfuse_sink_from_env(env)
    assert isinstance(sink, LangfuseSink)
    assert "synthetic" not in repr(sink)
    constructor.assert_called_once()
    constructor.side_effect = RuntimeError("synthetic")
    before = sink_error_count()
    assert langfuse_sink_from_env(env) is None
    assert sink_error_count() == before + 1


def test_sdk_mapping_without_network(monkeypatch):
    from datetime import UTC, datetime

    import langfuse

    from govops.obs.trace import SpanRecord

    client = Mock()
    constructor = Mock(return_value=client)
    wrappers = []

    def wrapper(**kwargs):
        wrappers.append(kwargs)
        return Mock()

    monkeypatch.setattr(langfuse, "Langfuse", constructor)
    monkeypatch.setattr(langfuse, "LangfuseSpan", wrapper)
    monkeypatch.setattr(langfuse, "LangfuseTool", wrapper)
    env = dict.fromkeys(
        ["LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"], "synthetic"
    )
    sink = langfuse_sink_from_env(env)
    assert sink is not None
    attrs = {
        "user_id": "synthetic-user",
        "input.request": "[REDACTED]",
        "output.answer": "masked",
    }
    root = SpanRecord(
        "a" * 32,
        "b" * 16,
        None,
        SpanKind.TASK,
        "task",
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:01+00:00",
        1000,
        "ok",
        attrs,
    )
    child = SpanRecord(
        root.trace_id,
        "c" * 16,
        root.span_id,
        SpanKind.TOOL_CALL,
        "lookup",
        root.start_ts,
        root.end_ts,
        1000,
        "error",
        {"tool.args": "[REDACTED]"},
    )
    sink.emit(child)
    sink.emit(root)
    sink.close_trace(root.trace_id)
    assert len(wrappers) == 2
    for record, kwargs in zip([child, root], wrappers, strict=True):
        otel_span = kwargs["otel_span"]
        assert otel_span.context.trace_id == int(record.trace_id, 16)
        assert otel_span.context.span_id == int(record.span_id, 16)
        assert (
            otel_span.start_time
            == int(datetime(2026, 1, 1, tzinfo=UTC).timestamp()) * 10**9
        )
        assert otel_span.end_time == otel_span.start_time + 10**9
        assert kwargs["metadata"] is record.attributes
        assert otel_span.name == record.name
    assert wrappers[0]["otel_span"].parent.span_id == int(root.span_id, 16)
    assert wrappers[0]["level"] == "ERROR"
    assert wrappers[1]["otel_span"].parent is None
    assert wrappers[1]["otel_span"].attributes["langfuse.trace.tags"] == ("govops",)
    client.flush.assert_called_once()


def test_sdk_import_is_confined():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src/govops"
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "langfuse"
            ):
                assert path == root / "obs/langfuse.py"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "langfuse":
                        assert path == root / "obs/langfuse.py"


def test_langfuse_failure_keeps_jsonl(tmp_path):
    from govops.obs.jsonl import JsonlSink
    from govops.obs.trace import set_attributes

    client = Mock()
    client.export_record.side_effect = RuntimeError("private sentinel")
    with trace("d" * 32, sinks=[JsonlSink(tmp_path), LangfuseSink(client)]):
        set_attributes(
            **{"input.request": "Bearer synthetic-private-sentinel 010-1234-5678"}
        )
    text = (tmp_path / f"{'d' * 32}.jsonl").read_text()
    assert "synthetic-private-sentinel" not in text
    assert "010-1234-5678" not in text
    assert '"name": "task"' in text

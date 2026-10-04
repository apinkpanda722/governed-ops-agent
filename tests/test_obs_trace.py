import asyncio
import re

import pytest

from govops.obs.trace import (
    SpanKind,
    configure,
    set_attributes,
    sink_error_count,
    span,
    trace,
)


class Sink:
    def __init__(self):
        self.records = []
        self.closed = []

    def emit(self, record):
        self.records.append(record)

    def close_trace(self, trace_id):
        self.closed.append(trace_id)


def test_nesting_masking_and_noop():
    sink = Sink()
    with span(SpanKind.TOOL_CALL, "outside"):
        set_attributes(ignored=True)
    with trace("a" * 32, user_id="syn-user", sinks=[sink]):  # noqa: SIM117
        with span(SpanKind.GRAPH_NODE, "node"):
            with span(
                SpanKind.TOOL_CALL,
                "read",
                **{"tool.result": "가" * 400 + "BODY_SENTINEL"},
            ):
                set_attributes(**{"input.request": "Bearer fake-value"})
    child, node, root = sink.records
    assert root.kind == SpanKind.TASK and root.trace_id == "a" * 32
    assert child.parent_id == node.span_id and node.parent_id == root.span_id
    assert all(
        re.fullmatch("[0-9a-f]{16}", r.span_id) and r.duration_ms >= 0
        for r in sink.records
    )
    assert "BODY_SENTINEL" not in repr(child) and "fake-value" not in repr(child)
    assert sink.closed == ["a" * 32]


def test_async_context():
    sink = Sink()

    async def child():
        await asyncio.sleep(0)
        with span(SpanKind.TOOL_CALL, "child"):
            await asyncio.sleep(0)

    async def run():
        with trace("b" * 32, sinks=[sink]), span(SpanKind.GRAPH_NODE, "parent"):
            await asyncio.gather(asyncio.create_task(child()), child())

    asyncio.run(run())
    first, second, parent, root = sink.records
    assert first.parent_id == second.parent_id == parent.span_id
    assert parent.parent_id == root.span_id


def test_error_propagates_without_message():
    sink = Sink()
    with pytest.raises(ValueError), trace("c" * 32, sinks=[sink]):  # noqa: SIM117
        with span(SpanKind.MODEL_CALL, "model"):
            raise ValueError("PRIVATE_ERROR_SENTINEL")
    assert all(
        r.status == "error" and r.attributes["error_type"] == "ValueError"
        for r in sink.records
    )
    assert "PRIVATE_ERROR_SENTINEL" not in repr(sink.records)


def test_sink_failure_and_defaults():
    class Bad(Sink):
        def emit(self, record):
            raise RuntimeError("do not log")

        def close_trace(self, trace_id):
            raise RuntimeError("do not log")

    start = sink_error_count()
    good = Sink()
    configure([Bad(), good])
    try:
        with trace("d" * 32):
            set_attributes(done=True)
        assert good.records[0].attributes["done"]
        assert sink_error_count() == start + 2
    finally:
        configure([])


def test_current_span_id_follows_the_active_span():
    from govops.obs.trace import current_span_id

    assert current_span_id() is None
    with trace("a" * 32, sinks=[]) as _:
        root = current_span_id()
        assert root and len(root) == 16
        with span(SpanKind.GRAPH_NODE, "model"):
            inner = current_span_id()
            assert inner and inner != root
        assert current_span_id() == root
    assert current_span_id() is None

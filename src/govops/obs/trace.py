"""Task-scoped contextvars spans; every emission crosses the masking boundary."""

import re
import secrets
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from govops.obs.mask import mask_attributes, mask_text


class SpanKind(StrEnum):
    TASK = "task"
    GRAPH_NODE = "graph_node"
    TOOL_CALL = "tool_call"
    MODEL_CALL = "model_call"
    GUARDRAIL = "guardrail"
    APPROVAL_WAIT = "approval_wait"
    APPROVAL_DECISION = "approval_decision"
    IDEMPOTENCY_LOOKUP = "idempotency_lookup"
    RETRY = "retry"
    CIRCUIT_STATE = "circuit_state"


@dataclass
class SpanRecord:
    trace_id: str
    span_id: str
    parent_id: str | None
    kind: SpanKind
    name: str
    start_ts: str
    end_ts: str
    duration_ms: float
    status: Literal["ok", "error"]
    attributes: dict


class TraceSink(Protocol):
    def emit(self, record: SpanRecord) -> None: ...
    def close_trace(self, trace_id: str) -> None: ...


@dataclass(frozen=True)
class _TraceContext:
    trace_id: str
    sinks: tuple[TraceSink, ...]


@dataclass
class _ActiveSpan:
    span_id: str
    attributes: dict[str, Any] = field(default_factory=dict)


_current_trace: ContextVar[_TraceContext | None] = ContextVar("obs_trace", default=None)
_current_span: ContextVar[_ActiveSpan | None] = ContextVar("obs_span", default=None)
_defaults: tuple[TraceSink, ...] = ()
_errors = 0
_error_lock = threading.Lock()


def configure(sinks: Sequence[TraceSink]) -> None:
    global _defaults
    _defaults = tuple(sinks)


def sink_error_count() -> int:
    with _error_lock:
        return _errors


def _sink_error() -> None:
    global _errors
    with _error_lock:
        _errors += 1


@contextmanager
def trace(
    task_id: str,
    *,
    user_id: str | None = None,
    sinks: Sequence[TraceSink] | None = None,
) -> Iterator[None]:
    if not re.fullmatch(r"[0-9a-f]{32}", task_id):
        raise ValueError("invalid trace_id")
    context = _TraceContext(task_id, tuple(sinks) if sinks is not None else _defaults)
    token = _current_trace.set(context)
    parent_token = _current_span.set(None)
    try:
        with span(
            SpanKind.TASK,
            "task",
            **({"user_id": user_id} if user_id is not None else {}),
        ):
            yield
    finally:
        _current_span.reset(parent_token)
        _current_trace.reset(token)
        for sink in context.sinks:
            try:
                sink.close_trace(task_id)
            except Exception:  # noqa: BLE001 - sink failures must never break tasks
                _sink_error()


@contextmanager
def span(kind: SpanKind, name: str, **attributes: Any) -> Iterator[None]:
    context = _current_trace.get()
    if context is None:
        yield
        return
    parent = _current_span.get()
    active = _ActiveSpan(secrets.token_hex(8), dict(attributes))
    token = _current_span.set(active)
    start_ts = datetime.now(UTC).isoformat()
    start = time.perf_counter()
    status: Literal["ok", "error"] = "ok"
    try:
        yield
    except BaseException as error:
        status = "error"
        active.attributes["error_type"] = type(error).__name__
        raise
    finally:
        end_ts = datetime.now(UTC).isoformat()
        duration_ms = (time.perf_counter() - start) * 1000
        _current_span.reset(token)
        for sink in context.sinks:
            try:
                record = SpanRecord(
                    context.trace_id,
                    active.span_id,
                    parent.span_id if parent else None,
                    kind,
                    mask_text(name, role="request"),
                    start_ts,
                    end_ts,
                    duration_ms,
                    "error" if active.attributes.get("error_code") else status,
                    mask_attributes(active.attributes),
                )
                sink.emit(record)
            except Exception:  # noqa: BLE001 - sink failures must never break tasks
                _sink_error()


def set_attributes(**attributes: Any) -> None:
    active = _current_span.get()
    if active is not None:
        active.attributes.update(attributes)

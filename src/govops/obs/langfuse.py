"""Optional, fail-open export of already masked records via public SDK/OTel APIs."""

import os
from collections.abc import Mapping
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from govops.obs.trace import SpanKind, SpanRecord, _sink_error


class LangfuseSink:
    def __init__(self, client: Any) -> None:
        self._client = client

    def emit(self, record: SpanRecord) -> None:
        try:
            self._client.export_record(record)
        except Exception:  # noqa: BLE001 - telemetry must never break a task
            _sink_error()

    def close_trace(self, trace_id: str) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001
            _sink_error()


def _nanoseconds(timestamp: str) -> int:
    value = datetime.fromisoformat(timestamp)
    return int(value.timestamp()) * 1_000_000_000 + value.microsecond * 1000


def langfuse_sink_from_env(
    env: Mapping[str, str] | None = None,
) -> LangfuseSink | None:
    env = os.environ if env is None else env
    names = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
    if not all(env.get(name, "").strip() for name in names):
        return None
    try:
        from langfuse import (
            Langfuse,
            LangfuseOtelSpanAttributes,
            LangfuseSpan,
            LangfuseTool,
        )
        from opentelemetry import trace as otel
        from opentelemetry.context import Context
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.id_generator import IdGenerator

        current: ContextVar[SpanRecord] = ContextVar("langfuse_export_record")

        class RecordIds(IdGenerator):
            def generate_span_id(self) -> int:
                return int(current.get().span_id, 16)

            def generate_trace_id(self) -> int:
                return int(current.get().trace_id, 16)

        provider = TracerProvider(id_generator=RecordIds())
        client = Langfuse(
            base_url=env["LANGFUSE_HOST"],
            public_key=env["LANGFUSE_PUBLIC_KEY"],
            secret_key=env["LANGFUSE_SECRET_KEY"],
            tracer_provider=provider,
        )
        # Langfuse의 span 프로세서는 이 계측 범위 이름과, 같은 프로젝트의 public_key를 범위
        # 속성으로 가진 span만 내보낸다. 속성이 없으면 span이 조용히 버려진다(debug 로그만).
        tracer = provider.get_tracer(
            "langfuse-sdk", attributes={"public_key": env["LANGFUSE_PUBLIC_KEY"]}
        )

        class Exporter:
            def export_record(self, record: SpanRecord) -> None:
                token = current.set(record)
                try:
                    context = Context()
                    if record.parent_id:
                        parent = otel.NonRecordingSpan(
                            otel.SpanContext(
                                trace_id=int(record.trace_id, 16),
                                span_id=int(record.parent_id, 16),
                                is_remote=True,
                                trace_flags=otel.TraceFlags(otel.TraceFlags.SAMPLED),
                            )
                        )
                        context = otel.set_span_in_context(parent, context)
                    root = record.kind == SpanKind.TASK
                    attributes = {}
                    if root:
                        attributes = {
                            LangfuseOtelSpanAttributes.TRACE_NAME: "task",
                            LangfuseOtelSpanAttributes.TRACE_TAGS: ["govops"],
                        }
                        if record.attributes.get("user_id") is not None:
                            attributes[LangfuseOtelSpanAttributes.TRACE_USER_ID] = (
                                record.attributes["user_id"]
                            )
                    otel_span = tracer.start_span(
                        "task" if root else record.name,
                        context=context,
                        start_time=_nanoseconds(record.start_ts),
                        attributes=attributes,
                    )
                    try:
                        wrapper = (
                            LangfuseTool
                            if record.kind == SpanKind.TOOL_CALL
                            else LangfuseSpan
                        )
                        observation = wrapper(
                            otel_span=otel_span,
                            langfuse_client=client,
                            metadata=record.attributes,
                            input=record.attributes.get("input.request"),
                            output=record.attributes.get("output.answer"),
                            level="ERROR" if record.status == "error" else "DEFAULT",
                        )
                        if root:
                            observation.set_trace_io(
                                input=record.attributes.get("input.request"),
                                output=record.attributes.get("output.answer"),
                            )
                        if record.status == "error":
                            otel_span.set_status(otel.StatusCode.ERROR)
                    finally:
                        otel_span.end(end_time=_nanoseconds(record.end_ts))
                finally:
                    current.reset(token)

            def flush(self) -> None:
                client.flush()

        return LangfuseSink(Exporter())
    except Exception:  # noqa: BLE001 - do not expose SDK errors or credentials
        _sink_error()
        return None

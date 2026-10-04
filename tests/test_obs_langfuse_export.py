"""실제 Langfuse SDK·OTel 내보내기를 루프백 서버로 확인한다(외부 네트워크 없음).

fake 클라이언트 테스트로는 잡히지 않던 문제(계측 범위 속성 누락으로 span이 조용히
버려짐)를 막는 회귀 테스트다.
"""

import gzip
import http.server
import threading

import pytest

from govops.obs import trace as obs
from govops.obs.langfuse import langfuse_sink_from_env

pytest.importorskip("langfuse")

# Langfuse SDK는 public_key마다 클라이언트를 하나만 두므로 테스트마다 키를 바꾼다.
_counter = iter(range(1_000_000))
SECRET = "sk-synthetic-secret-value"
TASK = "a" * 32


class Receiver:
    def __init__(self):
        self.requests = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                size = int(self.headers.get("content-length", 0))
                headers = {k.lower(): v for k, v in self.headers.items()}
                outer.requests.append((self.path, headers, self.rfile.read(size)))
                self.send_response(200)
                self.send_header("content-type", "application/x-protobuf")
                self.end_headers()

            def do_GET(self):
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def spans(self):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        found = []
        for path, headers, body in self.requests:
            if not path.endswith("/api/public/otel/v1/traces"):
                continue
            if headers.get("content-encoding") == "gzip":
                body = gzip.decompress(body)
            request = ExportTraceServiceRequest()
            request.ParseFromString(body)
            for resource in request.resource_spans:
                for scope in resource.scope_spans:
                    for span in scope.spans:
                        found.append((scope.scope.name, span))
        return found


def env(url):
    return {
        "LANGFUSE_HOST": url,
        "LANGFUSE_PUBLIC_KEY": f"pk-synthetic-{next(_counter)}",
        "LANGFUSE_SECRET_KEY": SECRET,
    }


def run_task(sink):
    with obs.trace(TASK, user_id="demo-platform-g3", sinks=[sink]):
        obs.set_attributes(
            **{"input.request": "연락처 010-1234-5678 Bearer abcdef0123456789"}
        )
        with (
            obs.span(obs.SpanKind.GRAPH_NODE, "model"),
            obs.span(obs.SpanKind.MODEL_CALL, "agent-primary"),
        ):
            pass
        with (
            obs.span(obs.SpanKind.GRAPH_NODE, "read_tools"),
            obs.span(obs.SpanKind.TOOL_CALL, "lookup_id", **{"tool.args": "{}"}),
        ):
            pass
        obs.set_attributes(**{"output.answer": "합성 답변"})


def test_spans_reach_the_otlp_endpoint_with_trace_and_parent_ids():
    with Receiver() as receiver:
        before = obs.sink_error_count()
        sink = langfuse_sink_from_env(env(receiver.url))
        assert sink is not None and obs.sink_error_count() == before
        run_task(sink)  # 루트 span이 닫힐 때 flush되어 프로세스 종료 전에 전송된다
        spans = receiver.spans()
        assert {scope for scope, _ in spans} == {"langfuse-sdk"}
        by_name = {span.name: span for _, span in spans}
        assert set(by_name) == {
            "task",
            "model",
            "agent-primary",
            "read_tools",
            "lookup_id",
        }
        assert {span.trace_id.hex() for _, span in spans} == {TASK}
        task, model = by_name["task"], by_name["model"]
        assert task.parent_span_id == b""
        assert model.parent_span_id == task.span_id
        assert by_name["agent-primary"].parent_span_id == model.span_id
        assert by_name["lookup_id"].parent_span_id == by_name["read_tools"].span_id
        names = {a.key: a.value.string_value for a in task.attributes}
        assert names["langfuse.trace.name"] == "task"
        assert names["user.id"] == "demo-platform-g3"
        kinds = {a.key: a.value.string_value for a in by_name["lookup_id"].attributes}
        assert kinds["langfuse.observation.type"] == "tool"
        auth = receiver.requests[0][1].get("authorization", "")
        assert auth.startswith("Basic ")


def test_exported_payload_is_masked_and_has_no_credentials():
    with Receiver() as receiver:
        sink = langfuse_sink_from_env(env(receiver.url))
        run_task(sink)
        raw = b"".join(
            gzip.decompress(body) if h.get("content-encoding") == "gzip" else body
            for _, h, body in receiver.requests
        )
        assert raw
        for forbidden in (b"010-1234-5678", b"abcdef0123456789", SECRET.encode()):
            assert forbidden not in raw
        assert b"[PII:mobile]" in raw


def test_flush_on_trace_close_and_shutdown_is_registered(monkeypatch):
    import atexit

    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn))
    with Receiver() as receiver:
        sink = langfuse_sink_from_env(env(receiver.url))
        assert sink is not None and registered, "종료 시 shutdown이 등록되어야 한다"
        run_task(sink)
        assert receiver.spans()
        for fn in registered:
            fn()  # 두 번 호출해도 예외가 없어야 한다
            fn()


def test_other_projects_scope_is_not_exported_without_the_public_key_attribute():
    # 범위 속성 없는 span은 Langfuse 프로세서가 버린다는 사실을 문서화하는 대조군이다.
    with Receiver() as receiver:
        sink = langfuse_sink_from_env(env(receiver.url))
        assert sink is not None
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider()
        tracer = provider.get_tracer("langfuse-sdk")
        tracer.start_span("orphan").end()
        sink.close_trace(TASK)
        assert receiver.spans() == []

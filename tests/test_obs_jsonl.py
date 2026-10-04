import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from govops.obs.jsonl import JsonlSink
from govops.obs.trace import SpanKind, span, trace


def test_file_permissions_content_and_concurrent_lines(tmp_path):
    root = tmp_path / "traces"
    sink = JsonlSink(root)
    with (
        trace("e" * 32, sinks=[sink]),
        span(
            SpanKind.TOOL_CALL,
            "read",
            **{
                "input.request": "010-" + "0" * 4 + "-" + "0" * 4,
                "tool.result": "가" * 400 + "BODY_SENTINEL",
                "token": "PRIVATE_SECRET",
                "header": "Authorization: Bearer PRIVATE_SECRET",
            },
        ),
    ):
        pass
    path = root / ("e" * 32 + ".jsonl")
    text = path.read_text()
    rows = [json.loads(line) for line in text.splitlines()]
    assert rows[-1]["kind"] == "task"
    assert path.stat().st_mode & 0o777 == 0o600
    assert root.stat().st_mode & 0o777 == 0o700
    assert all(
        x not in text
        for x in ["PRIVATE_SECRET", "Authorization", "BODY_SENTINEL", "010-"]
    )
    from govops.obs.trace import SpanRecord

    record = SpanRecord(**{**rows[0], "kind": SpanKind.TOOL_CALL})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(sink.emit, [record] * 100))
    assert len([json.loads(line) for line in path.read_text().splitlines()]) == 102
    for bad in ["../escape", "A" * 32, "f" * 31]:
        with pytest.raises(ValueError, match="invalid trace_id"):
            sink.emit(replace(record, trace_id=bad))


def test_env_root(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVOPS_TRACE_DIR", str(tmp_path / "env"))
    sink = JsonlSink()
    with trace("f" * 32, sinks=[sink]):
        pass
    assert (tmp_path / "env" / ("f" * 32 + ".jsonl")).exists()

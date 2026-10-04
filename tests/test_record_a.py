"""Recording CLI preserves whole responses and rejects unsafe documents."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from govops.replay.fake_a import create_app
from govops.replay.recordings import (
    Recording,
    RecordingSet,
    arguments_key,
    write_recordings,
)
from govops.tools.a_mcp import AMcpClient
from govops.tools.contract import load_snapshot, snapshot_sha256
from govops.tools.record_a import main

FIXTURES = Path(__file__).parent / "fixtures" / "a_recordings"
USER = "demo-platform-g3"


def test_record_skip_merge_and_roundtrip(
    tmp_path, serve_asgi, a_tokens, monkeypatch, capsys
):
    path, tokens = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    records = RecordingSet.load(FIXTURES)
    good = records.lookup(
        USER, "lookup_id", {"kind": "incident", "value": "INC-2026-012"}
    )
    bad = Recording(
        USER,
        "search_docs",
        {"query": "mixed"},
        {
            "results": [
                {
                    "doc_id": doc_id,
                    "text": "WHOLE_RECORD_SENTINEL",
                    "title": "합성 기록",
                    "is_current": True,
                    "section_key": "summary",
                    "section_kind": "summary",
                }
                for doc_id in ("syn-test", "real-document")
            ],
            "truncated": False,
        },
        False,
    )
    # The production loader refuses unsafe recordings. Override lookup only in this test.
    original = records.lookup
    with patch.object(
        records,
        "lookup",
        side_effect=lambda caller, tool, args: (
            bad if args == bad.arguments else original(caller, tool, args)
        ),
    ):
        base = serve_asgi(create_app(recordings=records, tokens_file=path))
        monkeypatch.setenv("KODOCS_MCP_URL", base + "/mcp")
        requests = tmp_path / "requests.json"
        requests.write_text(
            json.dumps(
                {
                    "requests": [
                        {"caller": r.caller, "tool": r.tool, "arguments": r.arguments}
                        for r in (good, bad)
                    ]
                }
            )
        )
        out = tmp_path / "out"
        preserved = records.lookup("demo-finance-g3", good.tool, good.arguments)
        write_recordings(
            out,
            [replace(good, result={"documents": [], "truncated": False}), preserved],
        )
        assert main(["--requests", str(requests), "--out", str(out)]) == 0
    content = (out / "recordings.jsonl").read_text()
    output = capsys.readouterr().out
    assert "WHOLE_RECORD_SENTINEL" not in content and "real-document" not in content
    assert arguments_key(bad.arguments)[:12] in output and "search_docs" in output
    assert "저장 1 (전체 2, 건너뜀 1)" in output
    assert snapshot_sha256() in output
    for token in tokens.values():
        assert token not in content + output
    assert "Authorization" not in content and "Bearer " not in content
    saved = RecordingSet.load(out)
    assert saved.entries() == RecordingSet([good, preserved]).entries()
    new_base = serve_asgi(create_app(recordings=saved, tokens_file=path))
    result = asyncio.run(
        AMcpClient(new_base + "/mcp").call_tool(USER, good.tool, good.arguments)
    )
    assert result.payload == good.result and result.is_error == good.is_error


def test_contract_mismatch_writes_nothing(
    tmp_path, serve_asgi, a_tokens, monkeypatch, capsys
):
    path, tokens = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    snapshot = load_snapshot()
    snapshot["lookup_id"] = replace(snapshot["lookup_id"], version=2)
    with patch("govops.replay.fake_a.load_snapshot", return_value=snapshot):
        base = serve_asgi(
            create_app(recordings=RecordingSet.load(FIXTURES), tokens_file=path)
        )
    request = tmp_path / "requests.json"
    request.write_text('{"requests": []}')
    out = tmp_path / "absent"
    assert (
        main(["--requests", str(request), "--out", str(out), "--url", base + "/mcp"])
        == 1
    )
    assert not out.exists()
    output = capsys.readouterr()
    for token in tokens.values():
        assert token not in output.out + output.err


def test_starter_requests():
    payload = json.loads(
        (Path(__file__).parents[1] / "data/replay/requests.json").read_text()
    )
    assert len(payload["requests"]) == 6
    assert {(r["caller"], r["tool"]) for r in payload["requests"]} == {
        (caller, tool)
        for caller in (USER, "demo-finance-g3")
        for tool in ("lookup_id", "search_docs", "get_document")
    }


def test_unknown_caller_fails_before_connection(
    tmp_path, a_tokens, monkeypatch, capsys
):
    path, tokens = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    requests = tmp_path / "requests.json"
    requests.write_text(
        json.dumps(
            {"requests": [{"caller": "unknown", "tool": "lookup_id", "arguments": {}}]}
        )
    )
    out = tmp_path / "out"
    with patch.object(
        AMcpClient, "list_tools", side_effect=AssertionError("network forbidden")
    ):
        assert (
            main(
                [
                    "--requests",
                    str(requests),
                    "--out",
                    str(out),
                    "--url",
                    "http://127.0.0.1:1/mcp",
                ]
            )
            == 1
        )
    assert not out.exists()
    output = capsys.readouterr()
    assert output.err
    for token in tokens.values():
        assert token not in output.out + output.err

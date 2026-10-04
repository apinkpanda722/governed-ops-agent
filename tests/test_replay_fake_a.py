"""Exercise the real MCP streamable HTTP endpoint on localhost."""

import hashlib
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.tools.contract import diff_tools_list, load_snapshot

FIXTURES = Path(__file__).parent / "fixtures" / "a_recordings"


def rpc(base, headers, method="tools/list", params=None):
    return httpx.post(
        base + "/mcp",
        headers=[("Accept", "application/json, text/event-stream"), *headers],
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        timeout=5,
    )


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [("Authorization", "Bearer wrong")],
        [("Authorization", "Bearer first"), ("Authorization", "Bearer second")],
        [("Authorization", "Bearer first, Bearer second")],
    ],
)
def test_unauthorized(serve_asgi, a_tokens, headers):
    path, _ = a_tokens
    base = serve_asgi(
        create_app(recordings=RecordingSet.load(FIXTURES), tokens_file=path)
    )
    response = rpc(base, headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_contract_identity_replay_and_no_secrets(serve_asgi, a_tokens, caplog):
    path, tokens = a_tokens
    app = create_app(recordings=RecordingSet.load(FIXTURES), tokens_file=path)
    # Authentication uses the startup hash table, not subsequent file contents.
    path.unlink()
    base = serve_asgi(app)
    results = []
    for label, token in tokens.items():
        headers = [("Authorization", f"Bearer {token}")]
        listed = rpc(base, headers)
        assert listed.status_code == 200
        assert diff_tools_list(load_snapshot(), listed.json()["result"]["tools"]) == []
        for record in RecordingSet.load(FIXTURES).entries():
            if record.caller != label:
                continue
            response = rpc(
                base,
                headers,
                "tools/call",
                {"name": record.tool, "arguments": record.arguments},
            )
            result = response.json()["result"]
            assert result["structuredContent"] == record.result
            assert json.loads(result["content"][0]["text"]) == record.result
            assert result["isError"] == record.is_error
            assert token not in response.text + str(response.headers)
        response = rpc(
            base,
            headers,
            "tools/call",
            {
                "name": "lookup_id",
                "arguments": {"kind": "incident", "value": "INC-2026-012"},
            },
        )
        results.append(response.json()["result"]["structuredContent"])
        missing = rpc(
            base,
            headers,
            "tools/call",
            {
                "name": "lookup_id",
                "arguments": {
                    "kind": "incident",
                    "value": "INC-unknown",
                    "caller": "demo-platform-g3",
                },
            },
        )
        assert missing.json()["result"]["isError"] is True
        assert (
            missing.json()["result"]["structuredContent"]["error"]["code"]
            == "replay_miss"
        )
        assert token not in missing.text + listed.text + caplog.text
    assert results[0] != results[1]


def test_changed_contract_is_comparable(tmp_path, serve_asgi, a_tokens):
    root = tmp_path / "contract"
    shutil.copytree(Path(__file__).parents[1] / "data" / "contract", root)
    file = root / "lookup_id.v1.json"
    payload = json.loads(file.read_text())
    payload["description"] = "변경된 합성 계약"
    file.write_text(json.dumps(payload))
    for name in ("manifest.json", "index.json"):
        manifest = json.loads((root / name).read_text())
        manifest["files"][file.name] = hashlib.sha256(file.read_bytes()).hexdigest()
        (root / name).write_text(json.dumps(manifest))
    path, tokens = a_tokens
    base = serve_asgi(
        create_app(
            recordings=RecordingSet.load(FIXTURES), tokens_file=path, contract_root=root
        )
    )
    response = rpc(base, [("Authorization", f"Bearer {next(iter(tokens.values()))}")])
    assert diff_tools_list(load_snapshot(), response.json()["result"]["tools"]) == [
        "lookup_id: description mismatch"
    ]


def test_cli_binds_only_localhost(a_tokens):
    from govops.replay.__main__ import main

    path, _ = a_tokens
    with patch("govops.replay.__main__.uvicorn.run") as run:
        assert (
            main(
                [
                    "--port",
                    "8765",
                    "--tokens-file",
                    str(path),
                    "--recordings",
                    str(FIXTURES),
                ]
            )
            == 0
        )
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert run.call_args.kwargs["port"] == 8765


def test_invalid_token_file_is_safe(tmp_path):
    path = tmp_path / "tokens.json"
    path.write_text('{"private-sentinel":')
    with pytest.raises(ValueError) as error:
        create_app(recordings=RecordingSet.load(FIXTURES), tokens_file=path)
    assert "private-sentinel" not in str(error.value)
    assert error.value.__context__ is None

"""Reusable localhost ASGI server and synthetic A credentials."""

import json
import secrets
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn


@pytest.fixture
def serve_asgi():
    servers = []

    def serve(app):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(
                app, host="127.0.0.1", log_level="critical", access_log=False
            )
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        servers.append((server, thread, sock))
        thread.start()
        deadline = time.monotonic() + 5
        while not server.started:
            if not thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("local ASGI server did not start")
            time.sleep(0.01)
        return f"http://127.0.0.1:{sock.getsockname()[1]}"

    yield serve
    for server, thread, sock in reversed(servers):
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive()


def temporary_a_tokens(path: Path):
    tokens = {
        f"demo-{team}-g3": secrets.token_urlsafe(32) for team in ("platform", "finance")
    }
    path.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "label": label,
                        "team": label.split("-")[1],
                        "grade": 3,
                        "token": token,
                    }
                    for label, token in tokens.items()
                ]
            }
        )
    )
    path.chmod(0o600)
    return tokens


@pytest.fixture
def a_tokens(tmp_path):
    path = tmp_path / "tokens.json"
    return path, temporary_a_tokens(path)

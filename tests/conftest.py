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


@pytest.fixture(scope="session", autouse=True)
def corpus_artifact_factory(tmp_path_factory):
    import test_eval_taskgen

    test_eval_taskgen._artifact_factory = tmp_path_factory
    yield
    test_eval_taskgen._artifact_factory = None


def audit_artifact_snapshot(root: Path) -> dict:
    """Only audit file metadata and the committed anchor digest are inspected."""
    import hashlib

    directory = root / "data/interim/audit"
    anchor = root / "docs/results/audit-anchors.jsonl"
    files = None
    if directory.exists():
        files = {}
        for path in directory.rglob("*"):
            if path.is_file():
                stat = path.stat()
                files[str(path.relative_to(directory))] = (
                    stat.st_size,
                    stat.st_mtime_ns,
                )
    return {
        "data/interim/audit": files,
        "docs/results/audit-anchors.jsonl": (
            hashlib.sha256(anchor.read_bytes()).hexdigest() if anchor.exists() else None
        ),
    }


def assert_audit_artifacts_unchanged(before: dict, after: dict) -> None:
    changed = []
    for name in before.keys() | after.keys():
        old, new = before.get(name), after.get(name)
        if old == new:
            continue
        if isinstance(old, dict) and isinstance(new, dict):
            changed.extend(
                f"{name}/{file}"
                for file in old.keys() | new.keys()
                if old.get(file) != new.get(file)
            )
        else:
            changed.append(name)
    assert not changed, "Audit artifacts changed: " + ", ".join(sorted(changed))


@pytest.fixture(scope="session")
def audit_key_pair(tmp_path_factory):
    from audit_keys import make_key_pair

    return make_key_pair(tmp_path_factory.mktemp("audit-keys"))


@pytest.fixture
def audit_test_directory(tmp_path_factory):
    return tmp_path_factory.mktemp("isolated-audit")


@pytest.fixture(autouse=True)
def isolated_audit_environment(audit_test_directory, monkeypatch, audit_key_pair):
    import shutil

    tmp_path = audit_test_directory
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "audit"))

    private_path = tmp_path / "private.pem"
    public_path = tmp_path / "public.pem"
    private_path.touch(mode=0o600)
    shutil.copyfile(audit_key_pair.private_path, private_path)
    shutil.copyfile(audit_key_pair.public_path, public_path)
    private_path.chmod(0o600)
    public_path.chmod(0o644)
    monkeypatch.setenv("AUDIT_KEY_FILE", str(private_path))
    monkeypatch.setenv("AUDIT_PUBLIC_KEY_FILE", str(public_path))
    monkeypatch.setenv(
        "AUDIT_ANCHORS_PENDING_FILE", str(tmp_path / "anchors-pending.jsonl")
    )


@pytest.fixture(scope="session", autouse=True)
def protect_repository_audit_artifacts():
    root = Path(__file__).resolve().parent.parent
    before = audit_artifact_snapshot(root)
    yield
    assert_audit_artifacts_unchanged(before, audit_artifact_snapshot(root))


@pytest.fixture(autouse=True)
def isolated_exploration_budget(monkeypatch):
    monkeypatch.delenv("GOVOPS_EXPLORATION_BUDGET", raising=False)

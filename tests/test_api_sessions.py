"""Session identity, secret containment and fail-closed startup."""

import asyncio
import hashlib
import inspect
import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from govops.api.__main__ import build_server_config
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.startup import StartupError, verify_contract
from govops.api.users import load_users
from govops.replay.fake_a import create_app as fake_a
from govops.replay.recordings import RecordingSet
from govops.tools.a_mcp import AMcpClient
from govops.tools.contract import load_snapshot


def client(store=None, enabled=True, address="127.0.0.1"):
    return TestClient(
        create_app(
            users=frozenset({"alice", "bob"}),
            sessions=store or SessionStore(),
            demo_login=enabled,
        ),
        client=(address, 50000),
    )


def test_login_routes_and_inputs():
    with client(enabled=False) as c:
        assert c.post("/sessions", json={"user": "alice"}).status_code == 404
        assert "/sessions" not in c.get("/openapi.json").json()["paths"]
    with client() as c:
        assert c.post("/sessions", json={"user": "alice"}).status_code == 201
        assert c.post("/sessions", json={"user": "missing"}).status_code == 404
        for body in ({}, {"user": 1}, {"user": "alice", "team": "other"}):
            assert c.post("/sessions", json=body).status_code == 422
    store = SessionStore()
    with client(store, address="10.0.0.5") as c:
        assert c.post("/sessions", json={"user": "alice"}).status_code == 403
    assert not store._sessions
    with client(address="::1") as c:
        assert c.post("/sessions", json={"user": "alice"}).status_code == 201


def test_identity_errors_and_secret_containment(caplog, capsys):
    caplog.set_level(logging.DEBUG)
    now = [100.0]
    store = SessionStore(ttl_seconds=10, clock=lambda: now[0])
    with client(store) as c:
        tokens = [
            c.post("/sessions", json={"user": user}).json()["session_token"]
            for user in ("alice", "bob")
        ]
        for token, user in zip(tokens, ("alice", "bob"), strict=True):
            assert hashlib.sha256(token.encode()).hexdigest() in store._sessions
            assert token not in repr((vars(store), store, store._sessions))
            response = c.get(
                "/sessions/me", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.json() == {"user_id": user}
            assert token not in response.text + repr(response.headers)
        headers = [
            {},
            {"Authorization": "Basic x"},
            {"Authorization": "Bearer x y"},
            {"Authorization": "Bearer unknown"},
            [
                ("Authorization", f"Bearer {tokens[0]}"),
                ("Authorization", f"Bearer {tokens[1]}"),
            ],
        ]
        errors = [c.get("/sessions/me", headers=h) for h in headers]
        now[0] = 110
        errors.append(
            c.get("/sessions/me", headers={"Authorization": f"Bearer {tokens[0]}"})
        )
        for response in errors:
            assert response.status_code == 401
            assert response.headers["WWW-Authenticate"] == "Bearer"
            assert response.json() == errors[0].json()
        assert hashlib.sha256(tokens[0].encode()).hexdigest() not in store._sessions
        responses = [
            c.post("/sessions", json={"user": tokens[0]}),
            c.post("/sessions", json={"user": [tokens[0]]}),
            c.get("/missing", headers={"Authorization": tokens[0]}),
        ]
        public = (
            repr([(r.text, dict(r.headers)) for r in errors + responses])
            + caplog.text
            + repr(capsys.readouterr())
        )
        assert all(token not in public for token in tokens)


@pytest.mark.parametrize(
    "value,enabled",
    [
        ("true", True),
        ("TRUE", True),
        ("1", False),
        ("yes", False),
        ("", False),
        (None, False),
    ],
)
def test_config(monkeypatch, value, enabled):
    if value is None:
        monkeypatch.delenv("DEMO_LOGIN", raising=False)
    else:
        monkeypatch.setenv("DEMO_LOGIN", value)
    monkeypatch.setenv("KODOCS_MCP_URL", "http://127.0.0.1:8765/mcp")
    config = build_server_config(8123)
    assert config.host == "127.0.0.1" and config.port == 8123
    assert ("/sessions" in config.app.openapi()["paths"]) is enabled
    assert list(inspect.signature(build_server_config).parameters) == ["port"]
    assert "--host" not in inspect.getsource(
        __import__("govops.api.__main__", fromlist=["main"])
    )


def test_startup_checks():
    seen = []

    async def first():
        seen.append(1)

    async def fail():
        seen.append(2)
        raise RuntimeError("startup stopped")

    app = create_app(
        users=frozenset({"alice"}),
        sessions=SessionStore(),
        demo_login=False,
        startup_checks=[first, fail, first],
    )
    with pytest.raises(RuntimeError, match="startup stopped"), TestClient(app):
        pass
    assert seen == [1, 2]


def test_contract(serve_asgi, a_tokens, monkeypatch):
    path, tokens = a_tokens
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    recordings = RecordingSet.load(Path(__file__).parent / "fixtures/a_recordings")
    app = fake_a(recordings=recordings, tokens_file=path)
    asyncio.run(verify_contract(AMcpClient(serve_asgi(app) + "/mcp")))
    snapshot = load_snapshot()
    snapshot["lookup_id"] = replace(snapshot["lookup_id"], version=2)
    monkeypatch.setattr("govops.replay.fake_a.load_snapshot", lambda _: snapshot)
    changed = fake_a(recordings=recordings, tokens_file=path)
    clients = [
        AMcpClient(serve_asgi(changed) + "/mcp"),
        AMcpClient("http://127.0.0.1:1/mcp", timeout_s=0.1),
    ]
    for c in clients:
        with pytest.raises(StartupError) as error:
            asyncio.run(verify_contract(c))
        assert error.value.__context__ is None
        assert all(t not in str(error.value) for t in tokens.values())
        assert "Authorization" not in str(error.value)
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path.parent / "missing"))
    with pytest.raises(StartupError, match="CredentialSourceError"):
        asyncio.run(verify_contract(clients[0]))


def test_users(tmp_path):
    assert len(load_users()) == 38
    path = tmp_path / "users.json"
    for value in (
        {},
        {"users": []},
        {"users": [{}]},
        {"users": [{"label": 1, "team": "x", "grade": 1}]},
        {"users": "bad"},
    ):
        path.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            load_users(path)
    with pytest.raises(OSError):
        load_users(tmp_path / "missing")


def test_expired_sessions_are_purged_on_issue_and_resolve():
    now = [0.0]
    store = SessionStore(ttl_seconds=10, clock=lambda: now[0])
    old = [store.issue(f"demo-user-g{i}")[0] for i in range(5)]
    assert len(store._sessions) == 5
    now[0] = 11.0
    token, _ = store.issue("demo-new-g1")
    # 발급 시점에 만료된 세션 5개가 모두 정리되고 새 세션만 남는다.
    assert len(store._sessions) == 1
    assert store.resolve(token) == "demo-new-g1"
    assert all(store.resolve(t) is None for t in old)
    # 조회 시점에도 만료된 다른 세션이 정리된다.
    other, _ = store.issue("demo-other-g1")
    now[0] = 22.0
    assert store.resolve("unknown-token") is None
    assert len(store._sessions) == 0 and store.resolve(other) is None

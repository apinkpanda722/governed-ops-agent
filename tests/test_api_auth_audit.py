"""Authentication auditing preserves responses and excludes untrusted input."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.audit.log import AuditLog, AuditWriteError, MemoryAuditLog


def app_for(sink, sessions=None):
    return create_app(
        users=frozenset({"demo-platform-g3"}),
        sessions=sessions or SessionStore(),
        demo_login=True,
        service=SimpleNamespace(audit=sink) if sink is not None else None,
    )


def records(sink):
    if isinstance(sink, MemoryAuditLog):
        return sink.records
    return [
        json.loads(line)
        for path in sink.directory.glob("chain-*.jsonl")
        for line in path.read_text().splitlines()
    ]


def failures(sink):
    return [r for r in records(sink) if r["event"] == "auth.failed"]


@pytest.fixture(params=["memory", "signed"])
def sink(request, tmp_path):
    return MemoryAuditLog() if request.param == "memory" else AuditLog(tmp_path)


def test_auth_reasons_and_secrecy(sink):
    now = [0.0]
    store = SessionStore(ttl_seconds=10, clock=lambda: now[0])
    token, _ = store.issue("demo-platform-g3")
    app = app_for(sink, store)
    assert app.state.audit is sink
    client = TestClient(app, client=("127.0.0.1", 1234))
    inputs = [
        {},
        {"Authorization": "Basic private-sentinel"},
        {"Authorization": "Bearer one two"},
        {"Authorization": "Bearer "},
        [("Authorization", "Bearer one"), ("Authorization", "Bearer two")],
        {"Authorization": "Bearer unknown-sentinel"},
    ]
    for headers in inputs:
        response = client.get("/sessions/me", headers=headers)
        assert response.status_code == 401
        assert response.json() == {"detail": "Authentication required"}
        assert response.headers["WWW-Authenticate"] == "Bearer"
    now[0] = 10.0
    response = client.get("/sessions/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json() == {"detail": "Authentication required"}
    assert [r["reason"] for r in failures(sink)] == ["missing_credentials"] * 5 + [
        "invalid_token",
        "invalid_token",
    ]
    response = client.post("/sessions", json={"user": "label-sentinel"})
    assert response.status_code == 404
    assert response.json() == {"detail": "User not found"}
    remote = TestClient(app, client=("192.0.2.1", 1234))
    response = remote.post("/sessions", json={"user": "label-sentinel"})
    assert response.status_code == 403
    assert response.json() == {"detail": "Loopback required"}
    assert [r["reason"] for r in failures(sink)][-2:] == [
        "unknown_user",
        "loopback_required",
    ]
    for record in failures(sink):
        actor = record.get("actor", {}).get("sub", record.get("actor_sub"))
        assert actor == "system" and record["task_id"] is None
    serialized = json.dumps(records(sink))
    for secret in (
        token,
        "label-sentinel",
        "private-sentinel",
        "unknown-sentinel",
        "Bearer",
        "192.0.2.1",
    ):
        assert secret not in serialized


@pytest.mark.parametrize(
    "error",
    [None, AuditWriteError("exception-sentinel"), RuntimeError("exception-sentinel")],
)
def test_absent_or_failing_sink_preserves_responses(error, caplog):
    class Broken:
        def append(self, *args, **kwargs):
            raise error

    app = app_for(Broken() if error else None)
    assert (app.state.audit is None) == (error is None)
    local = TestClient(app, client=("127.0.0.1", 1))
    remote = TestClient(app, client=("192.0.2.1", 1))
    responses = [
        local.get("/sessions/me"),
        local.post("/sessions", json={"user": "label-sentinel"}),
        remote.post("/sessions", json={"user": "label-sentinel"}),
    ]
    assert [r.status_code for r in responses] == [401, 404, 403]
    assert [r.json() for r in responses] == [
        {"detail": "Authentication required"},
        {"detail": "User not found"},
        {"detail": "Loopback required"},
    ]
    assert responses[0].headers["WWW-Authenticate"] == "Bearer"
    assert "exception-sentinel" not in caplog.text + repr(
        [(r.text, dict(r.headers)) for r in responses]
    )


def test_fixed_window_and_app_independence(sink):
    from govops.api.auth import AuthFailureLimiter

    now = [0.0]
    app = app_for(sink)
    app.state.auth_failure_limiter = AuthFailureLimiter(clock=lambda: now[0])
    client = TestClient(app)
    for _ in range(67):
        assert client.get("/sessions/me").status_code == 401
    assert len(failures(sink)) == 60
    assert all("suppressed" not in r for r in failures(sink))
    other_sink = MemoryAuditLog()
    other = app_for(other_sink)
    assert other.state.auth_failure_limiter is not app.state.auth_failure_limiter
    assert TestClient(other).get("/sessions/me").status_code == 401
    assert len(failures(other_sink)) == 1
    now[0] = 60.0
    client.get("/sessions/me")
    assert failures(sink)[-1]["suppressed"] == 7
    now[0] = 120.0
    client.get("/sessions/me")
    assert "suppressed" not in failures(sink)[-1]


def test_concurrent_failures_obey_injected_limit(sink):
    from govops.api.auth import AuthFailureLimiter

    now = [0.0]
    app = app_for(sink)
    app.state.auth_failure_limiter = AuthFailureLimiter(
        limit=7, window_seconds=5, clock=lambda: now[0]
    )
    client = TestClient(app)
    with ThreadPoolExecutor(max_workers=12) as pool:
        codes = list(
            pool.map(lambda _: client.get("/sessions/me").status_code, range(40))
        )
    assert set(codes) == {401}
    assert len(failures(sink)) == 7
    now[0] = 5.0
    client.get("/sessions/me")
    assert len(failures(sink)) == 8
    assert failures(sink)[-1]["suppressed"] == 33

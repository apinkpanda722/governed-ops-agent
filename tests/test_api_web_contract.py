"""Web session and approval contracts, including the real API entrypoint."""

import json
from contextlib import asynccontextmanager

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from test_agent_graph import async_test
from test_api_conversation_views import client_for, settled
from test_api_conversations import write_service

import govops.api.__main__ as entry
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.users import load_users
from govops.audit.log import MemoryAuditLog


def test_revoke_one_session():
    store = SessionStore()
    first, second = [store.issue("demo-platform-g3")[0] for _ in range(2)]
    assert store.revoke(first) is True
    assert store.resolve(first) is None
    assert store.revoke(first) is False
    assert store.resolve(second) == "demo-platform-g3"
    assert len(store._sessions) == 1
    assert second not in repr(store._sessions)


@pytest.mark.parametrize("enabled", [True, False])
def test_entrypoint_web_routes(monkeypatch, enabled):
    audit = MemoryAuditLog()

    @asynccontextmanager
    async def fake_service():
        class Service:
            pass

        service = Service()
        service.audit = audit
        yield service

    monkeypatch.setattr(entry, "open_task_service", fake_service)
    monkeypatch.setenv("DEMO_LOGIN", str(enabled).lower())
    app = entry.build_server_config(8123).app
    routes = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute) and route.path.startswith(("/sessions", "/demo"))
        for method in route.methods
    }
    expected = {("GET", "/sessions/me")}
    if enabled:
        expected |= {
            ("POST", "/sessions"),
            ("DELETE", "/sessions"),
            ("GET", "/demo/users"),
        }
    assert routes == expected
    with TestClient(app, client=("127.0.0.1", 1234)) as client:
        if not enabled:
            assert (
                client.post("/sessions", json={"user": "demo-platform-g3"}).status_code
                == 404
            )
            assert client.delete("/sessions").status_code == 404
            assert client.get("/demo/users").status_code == 404
            assert client.get("/sessions/me").status_code == 401
            return
        response = client.get("/demo/users")
        assert response.status_code == 200
        labels = response.json()["users"]
        assert len(labels) > 0
        assert set(response.json()) == {"users"}
        assert set(labels) == load_users()
        assert labels == sorted(load_users())
        user = labels[0]
        tokens = [
            client.post("/sessions", json={"user": user}).json()["session_token"]
            for _ in range(2)
        ]
        headers = [{"Authorization": "Bearer " + token} for token in tokens]
        response = client.delete("/sessions", headers=headers[0])
        assert response.status_code == 204 and response.content == b""
        assert client.get("/sessions/me", headers=headers[0]).status_code == 401
        assert client.delete("/sessions", headers=headers[0]).status_code == 401
        assert client.get("/sessions/me", headers=headers[1]).json() == {
            "user_id": user
        }
        for invalid in (
            {},
            {"Authorization": "Basic invalid"},
            {"Authorization": "Bearer unknown"},
        ):
            response = client.delete("/sessions", headers=invalid)
            assert response.status_code == 401
            assert response.json() == {"detail": "Authentication required"}
            assert response.headers["WWW-Authenticate"] == "Bearer"
        failures = [row for row in audit.records if row["event"] == "auth.failed"]
        assert len(failures) == 5
        assert {row["reason"] for row in failures} == {
            "invalid_token",
            "missing_credentials",
        }
        assert all(token not in json.dumps(audit.records) for token in tokens)


@pytest.mark.parametrize("address", ["127.0.0.1", "::1", "192.0.2.1"])
def test_demo_users_loopback(address):
    audit = MemoryAuditLog()
    app = create_app(users=load_users(), sessions=SessionStore(), demo_login=True)
    app.state.audit = audit
    with TestClient(app, client=(address, 1234)) as client:
        response = client.get("/demo/users")
        if address == "192.0.2.1":
            assert response.status_code == 403
            assert response.json() == {"detail": "Loopback required"}
            assert len(audit.records) == 1
            assert audit.records[0]["event"] == "auth.failed"
            assert audit.records[0]["reason"] == "loopback_required"
        else:
            assert response.status_code == 200
            assert len(response.json()["users"]) > 0
            assert set(response.json()["users"]) == load_users()
            assert audit.records == []


@async_test
async def test_approval_conversation_link_is_owner_only(tmp_path):
    service, _, _, _, users, _, _, _ = write_service(tmp_path)
    owner, approver, *_ = users
    client, auth = client_for(service)
    try:
        async with client:
            conversation_id = await service.start(owner, "합성 공지")
            await settled(service)
            rows = (await client.get("/approvals", headers=auth(approver))).json()[
                "approvals"
            ]
            assert len(rows) == 1
            assert {row["task_id"] for row in rows} == {conversation_id}
            assert all("conversation_id" not in row for row in rows)
            task = (
                await client.get("/tasks/" + conversation_id, headers=auth(owner))
            ).json()
            assert "conversation_id" not in task
            approver_task = (
                await client.get("/tasks/" + conversation_id, headers=auth(approver))
            ).json()
            assert "conversation_id" not in approver_task
            conversation = (
                await client.get(
                    "/conversations/" + conversation_id, headers=auth(owner)
                )
            ).json()
            assert conversation["conversation_id"] == conversation_id
            assert "conversation_id" not in conversation["turn"]
            await service._update(
                conversation_id,
                {
                    "draft": {
                        **task["draft"],
                        "args": {"channel": "platform", "text": "합성 안내"},
                    }
                },
            )
            rows = (await client.get("/approvals", headers=auth(owner))).json()[
                "approvals"
            ]
            assert len(rows) == 1
            assert {row["conversation_id"] for row in rows} == {conversation_id}
            assert (
                "conversation_id"
                not in (
                    await client.get("/tasks/" + conversation_id, headers=auth(owner))
                ).json()
            )
    finally:
        await service.aclose()

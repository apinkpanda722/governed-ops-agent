"""Deterministic faults, isolated administration and real response loss."""

import hashlib
import secrets
import socket
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from govops.mocks.auth import MockUser, TokenHashStore
from govops.mocks.faults import FaultH11Protocol, FaultState
from govops.mocks.service import create_admin_app, create_rest_app
from govops.mocks.slack import SlackState


@pytest.fixture
def apps():
    now = [datetime(2026, 10, 4, tzinfo=UTC)]
    waits = []

    async def wait(seconds):
        waits.append(seconds)

    faults = FaultState(wait=wait)
    state = SlackState(clock=lambda: now[0])
    token = secrets.token_urlsafe(32)
    store = TokenHashStore(
        (
            (
                MockUser("demo-platform-g3", "platform", 3),
                hashlib.sha256(token.encode()).hexdigest(),
            ),
        )
    )
    rest = create_rest_app("slack", state=state, token_store=store, faults=faults)
    admin = create_admin_app("slack", state=state, faults=faults)
    return state, faults, rest, admin, {"Authorization": f"Bearer {token}"}, now, waits


def rule(action, nth="all", endpoint="slack.send"):
    return {"endpoint": endpoint, "nth": nth, "action": action}


@pytest.mark.parametrize(
    "action,code,count",
    [
        ({"type": "status", "status": 503, "when": "before"}, 503, 0),
        ({"type": "status", "status": 502, "when": "after_apply"}, 502, 1),
        ({"type": "rate_limit", "seconds": 3}, 429, 0),
        ({"type": "delay", "ms": 250, "when": "before"}, 201, 1),
        ({"type": "delay", "ms": 250, "when": "after_apply"}, 201, 1),
    ],
)
def test_actions(apps, action, code, count):
    state, _faults, rest, admin, headers, _, waits = apps
    with TestClient(admin) as management, TestClient(rest) as client:
        assert management.post("/faults", json=[rule(action)]).status_code == 200
        response = client.post(
            "/channels/announce/messages",
            headers=headers,
            json={"text": "합성 메시지", "metadata": {"marker": "syn-1"}},
        )
        assert response.status_code == code
        assert len(state.messages["announce"]) == count
        if code == 429:
            assert response.headers["Retry-After"] == "3"
        if action["type"] == "delay":
            assert waits == [0.25]
        assert management.get("/faults/stats").json()["slack.send"] == {
            "requests": 1,
            "applied_rules": 1,
        }
        assert client.post("/faults", json=[]).status_code == 404
        assert client.get("/state?bodies=true").status_code == 404


def test_nth_reset_and_bodies(apps):
    _, faults, rest, admin, headers, _, _ = apps
    faults.set([rule({"type": "status", "status": 503, "when": "before"}, [2])])
    with TestClient(rest) as client, TestClient(admin) as management:
        for code in [201, 503, 201]:
            assert (
                client.post(
                    "/channels/announce/messages",
                    headers=headers,
                    json={"text": "합성", "metadata": {}},
                ).status_code
                == code
            )
        messages = management.get("/state").json()["channels"][-1]["messages"]
        assert "text" not in messages[0]
        assert (
            management.get("/state?bodies=true").json()["channels"][-1]["messages"][0][
                "text"
            ]
            == "합성"
        )
        assert management.post("/reset").status_code == 200
        assert faults.stats() == {}
        assert management.delete("/faults").status_code == 200


def test_overlap_hide_and_delay(apps):
    _, faults, rest, admin, headers, now, waits = apps
    faults.set(
        [
            rule({"type": "hide_for", "ms": 1000}),
            rule({"type": "delay", "ms": 200, "when": "after_apply"}),
            rule({"type": "status", "status": 503, "when": "after_apply"}),
        ]
    )
    with TestClient(rest) as client, TestClient(admin) as management:
        assert (
            client.post(
                "/channels/announce/messages",
                headers=headers,
                json={"text": "합성", "metadata": {}},
            ).status_code
            == 503
        )
        assert management.get("/state").json()["channels"][-1]["message_count"] == 1
        assert (
            client.get("/channels/announce/messages", headers=headers).json()[
                "messages"
            ]
            == []
        )
        now[0] += timedelta(seconds=1)
        assert (
            len(
                client.get("/channels/announce/messages", headers=headers).json()[
                    "messages"
                ]
            )
            == 1
        )
        assert waits == [0.2]
        assert faults.stats()["slack.send"]["applied_rules"] == 3


def test_drop_real_loopback(apps):
    state, faults, rest, _, headers, _, _ = apps
    faults.set(
        [
            rule({"type": "status", "status": 503, "when": "after_apply"}),
            rule({"type": "drop_after_apply"}),
            rule({"type": "hide_for", "ms": 1000}),
        ]
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        ready = threading.Event()

        class ReadyServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                ready.set()

        server = ReadyServer(
            uvicorn.Config(
                rest, http=FaultH11Protocol, log_level="critical", lifespan="off"
            )
        )
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [listener]}, daemon=True
        )
        thread.start()
        try:
            assert ready.wait(5)
            with (
                httpx.Client(
                    base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                    trust_env=False,
                    timeout=5,
                ) as client,
                pytest.raises((httpx.ReadError, httpx.RemoteProtocolError)),
            ):
                client.post(
                    "/channels/announce/messages",
                    headers=headers,
                    json={"text": "합성", "metadata": {}},
                )
            assert state.report()["channels"][-1]["message_count"] == 1
        finally:
            server.should_exit = True
            thread.join(5)
            assert not thread.is_alive()


@pytest.mark.parametrize(
    "suffix,endpoint,body,key",
    [
        (
            "comments",
            "jira.comment.create",
            {"body": "합성 코멘트", "metadata": {"marker": "syn-1"}},
            "comments",
        ),
        (
            "children",
            "jira.child.create",
            {
                "title": "합성 후속",
                "description": "합성 설명",
                "metadata": {"marker": "syn-1"},
            },
            "tickets",
        ),
    ],
)
def test_jira_visibility_and_bodies(apps, suffix, endpoint, body, key):
    from govops.mocks.jira import JiraState

    _, faults, _, _, headers, now, _ = apps
    seed = {
        "ticket_id": "SYN-1",
        "project_key": "SYN",
        "owner_team": "platform",
        "title": "합성",
        "description": None,
        "ticket_type": "incident",
        "status": "open",
        "priority": "high",
        "service": "syn-service",
        "incident_id": None,
        "reporter_id": "syn-reporter",
        "assignee_id": None,
        "created_at": now[0].isoformat(),
        "resolved_at": None,
        "story_points": None,
        "access_level": "public",
        "access_teams": [],
        "access_min_grade": 0,
    }
    state = JiraState([seed], clock=lambda: now[0])
    store = apps[2].state.token_store
    faults.set([rule({"type": "hide_for", "ms": 1000}, endpoint=endpoint)])
    with (
        TestClient(
            create_rest_app("jira", state=state, token_store=store, faults=faults)
        ) as client,
        TestClient(create_admin_app("jira", state=state, faults=faults)) as management,
    ):
        path = f"/tickets/SYN-1/{suffix}"
        assert client.post(path, headers=headers, json=body).status_code == 201
        report_key = "comments" if suffix == "comments" else "children"
        report = management.get("/state").json()
        assert len(report[report_key]) == 1
        assert report[report_key][0]["metadata"] == body["metadata"]
        for field in body.keys() - {"metadata"}:
            assert field not in report[report_key][0]
            assert (
                management.get("/state?bodies=true").json()[report_key][0][field]
                == body[field]
            )
        assert client.get(path, headers=headers).json()[key] == []
        now[0] += timedelta(seconds=1)
        assert len(client.get(path, headers=headers).json()[key]) == 1
        faults.set(
            [
                rule(
                    {"type": "status", "status": 503, "when": "after_apply"},
                    endpoint=endpoint,
                )
            ]
        )
        assert client.post(path, headers=headers, json=body).status_code == 503
        assert len(management.get("/state").json()[report_key]) == 2
        assert faults.stats()[endpoint]["requests"] == 1
        management.post("/reset")
        assert faults.stats() == {}


def test_read_endpoints_and_overlap_precedence(apps):
    _, faults, rest, _, headers, _, waits = apps
    faults.set(
        [
            rule({"type": "status", "status": 502, "when": "before"}),
            rule({"type": "delay", "ms": 100, "when": "before"}),
            rule({"type": "delay", "ms": 900, "when": "after_apply"}),
            rule({"type": "status", "status": 503, "when": "after_apply"}),
            rule(
                {"type": "status", "status": 504, "when": "before"},
                endpoint="slack.history",
            ),
            rule(
                {"type": "delay", "ms": 200, "when": "after_apply"},
                endpoint="slack.channels",
            ),
        ]
    )
    with TestClient(rest) as client:
        assert (
            client.post(
                "/channels/announce/messages",
                headers=headers,
                json={"text": "합성", "metadata": {}},
            ).status_code
            == 502
        )
        assert (
            client.get("/channels/announce/messages", headers=headers).status_code
            == 504
        )
        assert client.get("/channels", headers=headers).status_code == 200
    assert waits == [0.1, 0.2]


@pytest.mark.parametrize(
    "bad",
    [
        rule({"type": "status", "status": 400, "when": "before"}),
        rule({"type": "delay", "ms": -1, "when": "before"}),
        rule({"type": "delay", "ms": 1}),
        rule({"type": "hide_for", "ms": 1}, endpoint="slack.history"),
        rule({"type": "rate_limit", "seconds": 1}, nth=[0]),
        rule({"type": "rate_limit", "seconds": 1}, nth=[True]),
        rule({"type": "rate_limit", "seconds": 1}, endpoint="unknown"),
    ],
)
def test_invalid_rules_are_atomic(apps, bad):
    _, faults, _, admin, _, _, _ = apps
    faults.set([rule({"type": "rate_limit", "seconds": 2})])
    faults.select("slack.send")
    with TestClient(admin) as management:
        assert management.post("/faults", json=[bad]).status_code == 422
    assert faults.stats()["slack.send"]["requests"] == 1
    assert faults.select("slack.send")[0].seconds == 2


def test_thread_safe_request_numbers():
    from concurrent.futures import ThreadPoolExecutor

    faults = FaultState()
    faults.set([rule({"type": "rate_limit", "seconds": 1}, nth=[1, 100])])
    with ThreadPoolExecutor(max_workers=8) as pool:
        matches = list(pool.map(lambda _: faults.select("slack.send"), range(100)))
    assert sum(bool(m) for m in matches) == 2
    assert faults.stats()["slack.send"] == {"requests": 100, "applied_rules": 2}


@pytest.mark.parametrize(
    "command,method,path",
    [
        (["faults", "clear"], "DELETE", "/faults"),
        (["faults", "stats"], "GET", "/faults/stats"),
        (["state", "--bodies"], "GET", "/state?bodies=true"),
        (["faults", "set"], "POST", "/faults"),
    ],
)
def test_cli(apps, tmp_path, capsys, command, method, path):
    from govops.mocks.admin import main

    if command == ["faults", "set"]:
        file = tmp_path / "faults.json"
        file.write_text("[]")
        command += ["--file", str(file)]

    def handle(request):
        assert request.method == method
        assert str(request.url) == "http://127.0.0.1:9000" + path
        if method == "POST":
            assert request.content == b"[]"
        return httpx.Response(200, json={"status": "ok"})

    assert main(command, transport=httpx.MockTransport(handle)) == 0
    assert "ok" in capsys.readouterr().out


def test_delays_surround_application(apps):
    state, faults, rest, _, headers, _, _ = apps
    observations = []

    async def wait(seconds):
        observations.append((seconds, len(state.messages["announce"])))

    faults.wait = wait
    faults.set(
        [
            rule({"type": "delay", "ms": 100, "when": "before"}),
            rule({"type": "delay", "ms": 200, "when": "after_apply"}),
        ]
    )
    with TestClient(rest) as client:
        assert (
            client.post(
                "/channels/announce/messages",
                headers=headers,
                json={"text": "합성", "metadata": {}},
            ).status_code
            == 201
        )
    assert observations == [(0.1, 0), (0.2, 1)]

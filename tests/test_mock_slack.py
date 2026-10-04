"""Slack REST and marker history, using in-process transports only."""

import hashlib
import json
from datetime import UTC, datetime
from itertools import count

import httpx
import pytest
from fastapi.testclient import TestClient

from govops.mocks import __main__ as runner
from govops.mocks import admin
from govops.mocks.auth import TEAMS, TokenHashStore
from govops.mocks.service import create_admin_app, create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens


@pytest.fixture
def setup(tmp_path):
    users = tmp_path / "users.json"
    users.write_text(
        json.dumps(
            {
                "users": [
                    {"label": f"demo-{team}-g3", "team": team, "grade": 3}
                    for team in sorted(TEAMS)
                ]
            }
        )
    )
    private = tmp_path / "tokens.json"
    generate_tokens(users=users, plaintext=private, hash_dir=tmp_path / "hashes")
    records = json.loads(private.read_text())["users"]
    headers = {
        r["team"]: {"Authorization": f"Bearer {r['tokens']['mock-slack']}"}
        for r in records
    }
    store = TokenHashStore.load(tmp_path / "hashes/mock_slack_token_hashes.json")
    state = SlackState(
        clock=lambda: datetime(2026, 10, 4, tzinfo=UTC), counter=count(11)
    )
    return state, store, headers


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalid"}])
def test_authentication(setup, headers):
    state, store, _ = setup
    with TestClient(create_rest_app("slack", state=state, token_store=store)) as client:
        assert client.get("/healthz").status_code == 200
        for method, path, body in [
            ("GET", "/channels", None),
            ("GET", "/channels/announce/messages", None),
            (
                "POST",
                "/channels/announce/messages",
                {"text": "합성 메시지", "metadata": {}},
            ),
        ]:
            assert (
                client.request(method, path, json=body, headers=headers).status_code
                == 401
            )


def test_channel_list_and_permissions(setup):
    state, store, headers = setup
    expected = {
        "channels": [
            {"channel_id": f"team-{team}", "kind": "team", "team": team}
            for team in sorted(TEAMS)
        ]
        + [{"channel_id": "announce", "kind": "announce", "team": None}]
    }
    with TestClient(create_rest_app("slack", state=state, token_store=store)) as client:
        for team, identity in headers.items():
            assert client.get("/channels", headers=identity).json() == expected
            for channel in [c["channel_id"] for c in expected["channels"]] + [
                "missing"
            ]:
                allowed = channel in (f"team-{team}", "announce")
                for method in ("GET", "POST"):
                    response = client.request(
                        method,
                        f"/channels/{channel}/messages",
                        headers=identity,
                        json={"text": "합성 메시지", "metadata": {}}
                        if method == "POST"
                        else None,
                    )
                    assert response.status_code == (
                        404
                        if channel == "missing"
                        else (201 if method == "POST" else 200)
                        if allowed
                        else 403
                    )
                    if response.status_code == 403:
                        assert response.json() == {
                            "error": {"code": "forbidden", "message": "권한 없음"}
                        }


def test_duplicates_history_and_admin(setup, caplog):
    state, store, headers = setup
    with (
        TestClient(
            create_rest_app("slack", state=state, token_store=store),
            headers=headers["platform"],
        ) as client,
        TestClient(create_admin_app("slack", state=state)) as management,
    ):
        body = {"text": "합성 메시지 원문", "metadata": {"marker": "same"}}
        messages = []
        for number in (11, 12):
            response = client.post("/channels/team-platform/messages", json=body)
            assert response.status_code == 201
            message = response.json()["message"]
            assert message == {
                "message_id": f"message-{number}",
                "channel_id": "team-platform",
                "author": "demo-platform-g3",
                **body,
                "created_at": "2026-10-04T00:00:00+00:00",
            }
            messages.append(message)
        assert client.get("/channels/team-platform/messages").json() == {
            "messages": messages[::-1]
        }
        assert client.get("/channels/team-platform/messages?limit=1").json() == {
            "messages": messages[-1:]
        }
        report = management.get("/state")
        channels = report.json()["channels"]
        assert len(channels) == 9
        channel = next(c for c in channels if c["channel_id"] == "team-platform")
        assert channel["message_count"] == 2
        assert channel["messages"][0] == {
            k: v for k, v in messages[0].items() if k != "text"
        } | {"text_sha256": hashlib.sha256(body["text"].encode()).hexdigest()}
        assert body["text"] not in report.text
        for identity in headers.values():
            token = identity["Authorization"].split()[1]
            assert token not in report.text and token not in caplog.text
        assert client.get("/state").status_code == 404
        assert client.post("/reset").status_code == 404
        assert management.post("/reset").json() == {"status": "ok"}
        assert all(
            c["message_count"] == 0 and c["messages"] == []
            for c in management.get("/state").json()["channels"]
        )
        assert client.get("/channels/team-platform/messages").json() == {"messages": []}
        assert (
            client.post("/channels/team-platform/messages", json=body).json()[
                "message"
            ]["message_id"]
            == "message-11"
        )


def test_history_limit(setup):
    state, store, headers = setup
    with TestClient(
        create_rest_app("slack", state=state, token_store=store),
        headers=headers["platform"],
    ) as client:
        for i in range(105):
            assert (
                client.post(
                    "/channels/announce/messages", json={"text": str(i), "metadata": {}}
                ).status_code
                == 201
            )
        assert len(client.get("/channels/announce/messages").json()["messages"]) == 50
        messages = client.get("/channels/announce/messages?limit=999").json()[
            "messages"
        ]
        assert [m["text"] for m in messages] == [str(i) for i in range(104, 4, -1)]
        assert client.get("/channels/announce/messages?limit=0").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"text": "합성", "metadata": {}, "user": "spoof"},
        {"text": "합성", "metadata": {}, "team": "platform"},
        {"text": "합성", "metadata": {}, "grade": 4},
        {"text": 1, "metadata": {}},
        {"text": "합성", "metadata": {"marker": 1}},
    ],
)
def test_invalid_input(setup, body):
    state, store, headers = setup
    with TestClient(
        create_rest_app("slack", state=state, token_store=store),
        headers=headers["platform"],
    ) as client:
        assert client.post("/channels/announce/messages", json=body).status_code == 422
        assert client.get("/channels/announce/messages").json() == {"messages": []}


@pytest.mark.parametrize(("command", "method"), [("state", "GET"), ("reset", "POST")])
def test_admin_cli(setup, command, method, capsys):
    state, _, _ = setup
    with TestClient(create_admin_app("slack", state=state)) as client:

        def handle(request):
            assert request.method == method
            assert str(request.url) == f"http://mock-slack:9000/{command}"
            response = client.request(method, f"/{command}")
            return httpx.Response(response.status_code, json=response.json())

        assert (
            admin.main(
                ["--url", "http://mock-slack:9000", command],
                transport=httpx.MockTransport(handle),
            )
            == 0
        )
        assert (
            json.loads(capsys.readouterr().out)
            == client.request(method, f"/{command}").json()
        )


@pytest.mark.parametrize("content", [None, "invalid json", '{"users": []}'])
def test_startup_fails_closed(tmp_path, monkeypatch, capsys, content):
    path = tmp_path / "hashes.json"
    if content is not None:
        path.write_text(content)
    monkeypatch.setenv("MOCK_SLACK_TOKEN_HASHES", str(path))

    def forbidden(*args, **kwargs):
        pytest.fail("server started before manifest validation")

    monkeypatch.setattr(runner, "_serve", forbidden)
    assert runner.main(["slack"]) == 1
    assert "MOCK_SLACK_TOKEN_HASHES" in capsys.readouterr().err


def test_business_app_requires_auth_store():
    with pytest.raises(ValueError, match="token hash store"):
        create_rest_app("slack", state=SlackState())

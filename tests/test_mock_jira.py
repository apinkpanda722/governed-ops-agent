"""Jira business REST and internal administration, without network sockets."""

import hashlib
import json
import secrets
from itertools import count

import httpx
import pytest
from fastapi.testclient import TestClient

from govops.mocks import __main__ as runner
from govops.mocks import admin
from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.seed import TICKET_FIELDS, load_tickets
from govops.mocks.service import create_admin_app, create_rest_app


def ticket(number=1, **changes):
    return {
        "ticket_id": f"SYN-{number}",
        "project_key": "SYN",
        "owner_team": "platform",
        "title": f"합성 운영 점검 {number}",
        "ticket_type": "incident",
        "status": "open",
        "priority": "high",
        "service": "syn-service",
        "incident_id": None,
        "reporter_id": "syn-reporter",
        "assignee_id": "syn-assignee",
        "created_at": "2026-10-04T00:00:00+00:00",
        "resolved_at": None,
        "story_points": None,
        "access_level": "public",
        "access_teams": [],
        "access_min_grade": 0,
        **changes,
    }


@pytest.fixture
def setup(tmp_path):
    records = []
    headers = {}
    for team, grade in [
        ("platform", 2),
        ("platform", 3),
        ("platform", 4),
        ("payments", 4),
    ]:
        token = secrets.token_urlsafe(32)
        label = f"demo-{team}-g{grade}"
        records.append(
            {
                "label": label,
                "team": team,
                "grade": grade,
                "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
            }
        )
        headers[(team, grade)] = {"Authorization": f"Bearer {token}"}
    path = tmp_path / "hashes.json"
    path.write_text(json.dumps({"users": records}))
    store = TokenHashStore.load(path)
    seed = tmp_path / "tickets.json"
    seed.write_text(
        json.dumps(
            {
                "tickets": [
                    ticket(),
                    ticket(
                        8,
                        access_level="restricted",
                        access_teams=["platform"],
                        access_min_grade=3,
                    ),
                ]
            }
        )
    )
    state = JiraState(
        load_tickets(seed), clock=lambda: "2026-10-04T01:00:00+00:00", counter=count(11)
    )
    return state, store, headers


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer invalid"},
        {"Authorization": "Bearer one, Bearer two"},
        {"Authorization": "Basic invalid"},
    ],
)
def test_auth_required(setup, headers):
    state, store, _ = setup
    with TestClient(create_rest_app("jira", state=state, token_store=store)) as client:
        assert client.get("/healthz").status_code == 200
        for method, url, body in [
            ("GET", "/tickets", None),
            ("GET", "/tickets/SYN-1", None),
            ("GET", "/tickets/SYN-1/comments", None),
            ("GET", "/tickets/SYN-1/children", None),
            ("POST", "/tickets/SYN-1/comments", {"body": "text", "metadata": {}}),
            ("POST", "/tickets/SYN-1/children", {"title": "text", "metadata": {}}),
        ]:
            assert (
                client.request(method, url, json=body, headers=headers).status_code
                == 401
            )


def test_duplicate_authorization_headers(setup):
    state, store, headers = setup
    bearer = headers[("platform", 3)]["Authorization"]
    with TestClient(create_rest_app("jira", state=state, token_store=store)) as client:
        assert (
            client.get(
                "/tickets",
                headers=[("Authorization", bearer), ("Authorization", bearer)],
            ).status_code
            == 401
        )


@pytest.mark.parametrize(
    ("level", "team", "grade", "allowed"),
    [
        ("public", "payments", 4, True),
        ("team", "platform", 2, True),
        ("team", "payments", 4, False),
        ("restricted", "platform", 2, False),
        ("restricted", "platform", 3, True),
        ("restricted", "platform", 4, True),
        ("restricted", "payments", 4, False),
    ],
)
def test_access_matrix(setup, level, team, grade, allowed):
    state, store, headers = setup
    state.tickets["SYN-8"]["access_level"] = level
    with TestClient(
        create_rest_app("jira", state=state, token_store=store),
        headers=headers[(team, grade)],
    ) as client:
        for suffix, method, body in [
            ("", "GET", None),
            ("/comments", "GET", None),
            ("/children", "GET", None),
            ("/comments", "POST", {"body": "text", "metadata": {}}),
            ("/children", "POST", {"title": "text", "metadata": {}}),
        ]:
            response = client.request(method, f"/tickets/SYN-8{suffix}", json=body)
            if allowed:
                assert response.status_code in (200, 201)
                if suffix == "":
                    assert not any(k.startswith("access_") for k in response.json())
            else:
                missing = client.request(method, f"/tickets/SYN-999{suffix}", json=body)
                assert response.status_code == missing.status_code == 404
                assert response.content == missing.content
                assert response.json()["error"]["code"] == "not_found"


def test_writes_metadata_no_dedup_and_reset(setup):
    state, store, headers = setup
    with (
        TestClient(
            create_rest_app("jira", state=state, token_store=store),
            headers=headers[("platform", 3)],
        ) as client,
        TestClient(create_admin_app("jira", state=state)) as management,
    ):
        body = {"body": "합성 코멘트 원문", "metadata": {"marker": "same"}}
        for expected in (11, 12):
            response = client.post("/tickets/SYN-1/comments", json=body)
            assert response.status_code == 201
            comment = response.json()["comment"]
            assert comment["comment_id"] == f"comment-{expected}"
            assert comment["author"] == "demo-platform-g3"
            assert comment["created_at"] == "2026-10-04T01:00:00+00:00"
        assert len(client.get("/tickets/SYN-1/comments").json()["comments"]) == 2
        child_body = {
            "title": "합성 후속 원문",
            "description": "합성 설명 원문",
            "priority": None,
            "metadata": {"marker": "same"},
        }
        for expected in (9, 10):
            response = client.post("/tickets/SYN-8/children", json=child_body)
            assert response.status_code == 201
            child = response.json()
            assert child["ticket_id"] == f"SYN-{expected}"
            assert child["parent_id"] == "SYN-8"
            assert child["owner_team"] == "platform"
            assert child["metadata"] == child_body["metadata"]
            assert client.get(f"/tickets/SYN-{expected}").json() == child
        children = client.get("/tickets/SYN-8/children").json()["tickets"]
        assert len(children) == 2
        assert (
            client.get("/tickets/SYN-1/comments").json()["comments"][0]["metadata"]
            == body["metadata"]
        )
        outsider = headers[("payments", 4)]
        assert client.get("/tickets/SYN-9", headers=outsider).status_code == 404
        report = management.get("/state")
        assert report.json()["ticket_count"] == 4
        assert len(report.json()["comments"]) == len(report.json()["children"]) == 2
        assert (
            body["body"] not in report.text
            and child_body["description"] not in report.text
            and child_body["title"] not in report.text
        )
        assert (
            report.json()["comments"][0]["body_sha256"]
            == hashlib.sha256(body["body"].encode()).hexdigest()
        )
        assert report.json()["comments"][0]["metadata"] == body["metadata"]
        assert management.post("/reset").status_code == 200
        assert management.get("/state").json() == {
            "ticket_count": 2,
            "comments": [],
            "children": [],
        }
        assert client.get("/tickets/SYN-1/comments").json() == {"comments": []}
        assert (
            client.post("/tickets/SYN-1/comments", json=body).json()["comment"][
                "comment_id"
            ]
            == "comment-11"
        )
        assert (
            client.post("/tickets/SYN-8/children", json=child_body).json()["ticket_id"]
            == "SYN-9"
        )
        assert client.post("/tickets", json=child_body).status_code in (404, 405)
        assert client.get("/state").status_code == 404


def test_search_filters_and_limit(setup):
    state, store, headers = setup
    for number in range(20, 90):
        state.tickets[f"SYN-{number}"] = ticket(
            number,
            owner_team="payments",
            status="resolved",
            title="합성 결제 점검",
            parent_id=None,
            metadata={},
        )
    with TestClient(
        create_rest_app("jira", state=state, token_store=store),
        headers=headers[("payments", 4)],
    ) as client:
        result = client.get("/tickets").json()["tickets"]
        assert len(result) == 20
        assert "SYN-8" not in [t["ticket_id"] for t in result]
        assert [t["ticket_id"] for t in result] == sorted(
            t["ticket_id"] for t in result
        )
        assert len(client.get("/tickets?limit=999").json()["tickets"]) == 50
        assert (
            len(
                client.get(
                    "/tickets?project=SYN&team=payments&status=resolved&q=결제&limit=3"
                ).json()["tickets"]
            )
            == 3
        )
        for query in ("project=OTHER", "team=security", "status=closed", "q=없는제목"):
            assert client.get(f"/tickets?{query}").json() == {"tickets": []}
        assert client.get("/tickets?limit=0").status_code == 422


@pytest.mark.parametrize(
    "body",
    [
        {"body": "text", "metadata": {"x": 1}},
        {"body": "text", "metadata": {}, "user": "spoof"},
        {"body": "text", "metadata": {}, "team": "platform"},
    ],
)
def test_invalid_write_schema(setup, body):
    state, store, headers = setup
    with TestClient(
        create_rest_app("jira", state=state, token_store=store),
        headers=headers[("platform", 3)],
    ) as client:
        assert client.post("/tickets/SYN-1/comments", json=body).status_code == 422
        assert client.get("/tickets/SYN-1/comments").json() == {"comments": []}


@pytest.mark.parametrize(
    "change", ["missing", "duplicate", "level", "root", "teams", "grade"]
)
def test_seed_validation(tmp_path, change):
    payload = {"tickets": [ticket()]}
    if change == "missing":
        del payload["tickets"][0]["title"]
    elif change == "duplicate":
        payload["tickets"].append(ticket())
    elif change == "level":
        payload["tickets"][0]["access_level"] = "unknown"
    elif change == "root":
        payload = []
    elif change == "teams":
        payload["tickets"][0]["access_teams"] = "platform"
    elif change == "grade":
        payload["tickets"][0]["access_min_grade"] = True
    path = tmp_path / "tickets.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_tickets(path)


def test_seed_ignores_extra_fields(tmp_path):
    path = tmp_path / "tickets.json"
    path.write_text(json.dumps({"tickets": [ticket(extra="ignore")]}))
    assert set(load_tickets(path)[0]) == TICKET_FIELDS


@pytest.mark.parametrize(("command", "method"), [("state", "GET"), ("reset", "POST")])
def test_admin_cli(command, method, capsys):
    def handle(request):
        assert request.method == method
        assert str(request.url) == f"http://127.0.0.1:9000/{command}"
        return httpx.Response(200, json={"status": "ok"})

    assert admin.main([command], transport=httpx.MockTransport(handle)) == 0
    assert json.loads(capsys.readouterr().out) == {"status": "ok"}


@pytest.mark.parametrize("failure", ["missing", "invalid_hashes", "invalid_tickets"])
def test_startup_fails_closed(tmp_path, monkeypatch, capsys, failure):
    hashes = tmp_path / "hashes.json"
    seed = tmp_path / "tickets.json"
    if failure != "missing":
        hashes.write_text(json.dumps({"users": []}))
        seed.write_text("invalid")
        if failure == "invalid_tickets":
            token = secrets.token_urlsafe(32)
            hashes.write_text(
                json.dumps(
                    {
                        "users": [
                            {
                                "label": "demo-platform-g3",
                                "team": "platform",
                                "grade": 3,
                                "token_sha256": hashlib.sha256(
                                    token.encode()
                                ).hexdigest(),
                            }
                        ]
                    }
                )
            )
    monkeypatch.setenv("MOCK_JIRA_TOKEN_HASHES", str(hashes))
    monkeypatch.setenv("MOCK_JIRA_TICKETS", str(seed))

    def forbidden(*args, **kwargs):
        pytest.fail("server started before validation")

    monkeypatch.setattr(runner.uvicorn.Server, "serve", forbidden)
    assert runner.main(["jira"]) == 1
    assert "mock-jira" in capsys.readouterr().err

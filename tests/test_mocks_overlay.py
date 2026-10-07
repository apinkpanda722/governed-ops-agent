"""Overlay loading preserves baseline state and REST access semantics."""

import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from test_mock_jira import ticket

from govops.mocks import __main__ as runner
from govops.mocks.auth import MockUser
from govops.mocks.jira import JiraState, _response
from govops.mocks.seed import load_tickets, merge_overlay


def write_overlay(tmp_path, rows):
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({"tickets": rows}))
    return path


def test_no_overlay_exact_state():
    base = [ticket()]
    expected = JiraState(base)
    actual = JiraState(merge_overlay(base, None))
    assert expected.report(bodies=True) == actual.report(bodies=True)
    assert expected.tickets == actual.tickets
    assert base == [ticket()]


def test_overlay_body_comments_and_access(tmp_path):
    row = ticket(
        9001,
        access_level="restricted",
        access_teams=["platform"],
        access_min_grade=3,
        description="합성 본문 지시",
        comments=[{"body": "합성 코멘트 지시", "author": "demo-platform-g3"}],
    )
    path = write_overlay(tmp_path, [row])
    base = [ticket()]
    merged = merge_overlay(base, path)
    assert base == [ticket()]
    assert len(merged) == 2
    assert set(load_tickets(path)[0]) == set(ticket())
    state = JiraState(merged)
    user = MockUser("demo-platform-g3", "platform", 3)
    assert state.visible_ticket("SYN-9001", user)["description"] == row["description"]
    for denied in (
        MockUser("demo-platform-g2", "platform", 2),
        MockUser("demo-finance-g5", "finance", 5),
    ):
        with pytest.raises(Exception) as exc:
            state.visible_ticket("SYN-9001", denied)
        assert exc.value.status_code == 404
    assert state.comments[0]["body"] == row["comments"][0]["body"]
    first = deepcopy(state.report(bodies=True))
    state.comments.clear()
    state.reset()
    assert state.report(bodies=True) == first


@pytest.mark.parametrize(
    "patch",
    [
        {"ticket_id": "SYN-1"},
        {"access_level": "invalid"},
        {"description": 7},
        {"comments": [{}]},
    ],
)
def test_overlay_rejects_collision_and_invalid_data(tmp_path, patch):
    path = write_overlay(tmp_path, [ticket(9001, **patch)])
    with pytest.raises(ValueError):
        merge_overlay([ticket()], path)


@pytest.mark.parametrize("value", [None, "", " \t", "overlay", "collision"])
def test_startup_path(tmp_path, monkeypatch, value, capsys):
    from govops.mocks.auth import TokenHashStore
    from govops.mocks.service import create_rest_app
    from govops.mocks.tokens import generate_tokens

    users = tmp_path / "users.json"
    users.write_text(
        json.dumps(
            {
                "users": [
                    {"label": "demo-platform-g3", "team": "platform", "grade": 3},
                    {"label": "demo-finance-g5", "team": "finance", "grade": 5},
                ]
            }
        )
    )
    private = tmp_path / "tokens.json"
    generate_tokens(users=users, plaintext=private, hash_dir=tmp_path)
    hashes = tmp_path / "mock_jira_token_hashes.json"
    monkeypatch.setenv("MOCK_JIRA_TOKEN_HASHES", str(hashes))
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"tickets": [ticket()]}))
    monkeypatch.setenv("MOCK_JIRA_TICKETS", str(base))
    monkeypatch.delenv("MOCK_JIRA_OVERLAY", raising=False)
    if value is not None:
        overlay = write_overlay(
            tmp_path,
            [
                ticket(
                    1 if value == "collision" else 9001,
                    access_level="team",
                    access_teams=["platform"],
                    description="본문",
                    comments=[{"body": "코멘트", "author": "demo-platform-g3"}],
                )
            ],
        )
        monkeypatch.setenv(
            "MOCK_JIRA_OVERLAY",
            str(overlay) if value in {"overlay", "collision"} else value,
        )
    states = []

    async def fake_serve(service, state, token_store):
        states.append(state)

    monkeypatch.setattr(runner, "_serve", fake_serve)
    assert runner.main(["jira"]) == (1 if value == "collision" else 0)
    if value == "collision":
        assert not states
        assert "startup failed" in capsys.readouterr().err
        return
    state = states[0]
    assert len(state.tickets) == (2 if value == "overlay" else 1)
    if value != "overlay":
        assert state.report(bodies=True) == JiraState(load_tickets(base)).report(
            bodies=True
        )
        assert state.tickets == JiraState(load_tickets(base)).tickets
        return
    # In-process REST uses the same seeded state; tool client reads comments separately.
    app = create_rest_app("jira", state=state, token_store=TokenHashStore.load(hashes))
    # Override the route dependency through its underlying _user function.
    from govops.mocks.jira import _user

    app.dependency_overrides[_user] = lambda: MockUser(
        "demo-platform-g3", "platform", 3
    )
    with TestClient(app) as client:
        payload = client.get("/tickets/SYN-9001").json()
        assert payload["description"] == "본문"
        assert payload["comments"][0]["body"] == "코멘트"
        assert payload["children"] == []
        assert (
            client.get("/tickets/SYN-9001/comments").json()["comments"][0]["body"]
            == "코멘트"
        )
        app.dependency_overrides[_user] = lambda: MockUser(
            "demo-finance-g5", "finance", 5
        )
        assert client.get("/tickets/SYN-9001").status_code == 404
        assert (
            client.get("/tickets/SYN-9001").json()
            == client.get("/tickets/SYN-9999").json()
        )
        assert client.get("/tickets/SYN-9001/comments").status_code == 404


def test_baseline_response_fields_unchanged():
    from govops.mocks.jira import RESPONSE_FIELDS

    state = JiraState([ticket()])
    baseline = state.tickets["SYN-1"]
    assert _response(baseline) == {key: baseline[key] for key in RESPONSE_FIELDS}
    child = {**baseline, "description": "기존 후속 티켓 설명"}
    assert _response(child) == _response(baseline)

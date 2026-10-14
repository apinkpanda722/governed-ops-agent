"""Overlay checks use only synthetic temporary files and injected adapters."""

import asyncio
import hashlib
import json
import subprocess
from copy import deepcopy
from types import SimpleNamespace

import pytest

from govops.evalrun.overlay_env import (
    OVERLAY_RELATIVE,
    overlay_seed_check,
    overlay_ticket_ids,
    read_overlay_matches,
)

SENTINEL = "synthetic-private-content"


def write(root, relative, value):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


@pytest.fixture
def seed(tmp_path):
    base = [{"ticket_id": "SYN-1", "title": "합성 기본 티켓"}]
    overlay = [
        {
            "ticket_id": "SYN-9001",
            "title": "합성 추가 티켓",
            "reporter_id": "demo-synthetic-g3",
            "description": "합성 점검 내용",
            "created_at": "2026-01-02T03:04:05+00:00",
            "comments": [{"body": "합성 점검 기록", "author": "demo-synthetic-g3"}],
        },
        {
            "ticket_id": "SYN-9002",
            "reporter_id": "demo-synthetic-g3",
            "description": "합성 상태 기록",
            "comments": [],
        },
    ]
    users = [
        {"label": "demo-other-g1", "team": "other"},
        {"label": "demo-synthetic-g3", "team": "synthetic"},
    ]
    write(tmp_path, "data/seed/tickets.json", {"tickets": base})
    write(tmp_path, "data/seed/users.json", {"users": users})
    write(tmp_path, OVERLAY_RELATIVE, {"tickets": overlay})
    return tmp_path, base, overlay, users


def test_overlay_ticket_ids(seed):
    root, _, overlay, _ = seed
    assert OVERLAY_RELATIVE == "data/redteam/overlay/jira_tickets.json"
    assert overlay_ticket_ids(root) == [row["ticket_id"] for row in overlay]


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"tickets": {}},
        {"tickets": [None]},
        {"tickets": [{}]},
        {"tickets": [{"ticket_id": 1}]},
        {"tickets": [{"ticket_id": ""}]},
        {"tickets": [{"ticket_id": "SYN-1"}, {"ticket_id": "SYN-1"}]},
    ],
)
def test_overlay_ticket_ids_invalid_shape_has_fixed_error(tmp_path, value):
    write(tmp_path, OVERLAY_RELATIVE, value)
    with pytest.raises(ValueError) as caught:
        overlay_ticket_ids(tmp_path)
    assert str(caught.value) == "overlay_mismatch"


@pytest.mark.parametrize("problem", ["missing", "json", "encoding"])
def test_overlay_ticket_ids_unreadable_has_fixed_error(tmp_path, problem):
    if problem != "missing":
        path = write(tmp_path, OVERLAY_RELATIVE, {})
        path.write_bytes(SENTINEL.encode() if problem == "json" else b"\xff")
    with pytest.raises(ValueError) as caught:
        overlay_ticket_ids(tmp_path)
    assert str(caught.value) == "overlay_mismatch"
    assert caught.value.__cause__ is None


def reports(base, overlay):
    return {
        "mock-jira": {
            "ticket_count": len(base) + len(overlay),
            "comments": [
                {
                    "comment_id": "seed-comment-SYN-9001-1",
                    "ticket_id": "SYN-9001",
                    "author": "demo-synthetic-g3",
                    "metadata": {},
                    "created_at": "2026-01-02T03:04:05+00:00",
                    "body_sha256": hashlib.sha256(
                        "합성 점검 기록".encode()
                    ).hexdigest(),
                }
            ],
            "children": [],
        },
        "mock-slack": {
            "channels": [
                {"channel_id": key, "message_count": 0, "messages": []}
                for key in ("team-other", "team-synthetic", "announce")
            ]
        },
    }


def test_overlay_seed_check_counts_and_compose_command(seed):
    root, base, overlay, _ = seed
    states = reports(base, overlay)
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        assert kwargs == {
            "cwd": root,
            "capture_output": True,
            "text": True,
            "check": False,
            "timeout": 30,
        }
        return SimpleNamespace(returncode=0, stdout=json.dumps(states[command[10]]))

    assert overlay_seed_check(root, runner=runner)
    assert calls == [
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "infra/docker-compose.yml",
            "-f",
            "infra/docker-compose.redteam.yml",
            "exec",
            "-T",
            service,
            "python",
            "-m",
            "govops.mocks.admin",
            "state",
        ]
        for service in ("mock-jira", "mock-slack")
    ]
    states["mock-jira"]["ticket_count"] = len(base)
    assert not overlay_seed_check(root, runner=runner)


@pytest.mark.parametrize("service", ["mock-jira", "mock-slack"])
@pytest.mark.parametrize("problem", ["returncode", "json", "os", "timeout", "dirty"])
def test_overlay_seed_check_failure_is_false_and_silent(seed, service, problem, capsys):
    root, base, overlay, _ = seed
    states = reports(base, overlay)

    def runner(command, **kwargs):
        current = command[10]
        if current != service:
            return SimpleNamespace(returncode=0, stdout=json.dumps(states[current]))
        if problem == "os":
            raise OSError(SENTINEL)
        if problem == "timeout":
            raise subprocess.TimeoutExpired(command, 30, output=SENTINEL)
        if problem == "dirty":
            states[current]["extra"] = SENTINEL
        return SimpleNamespace(
            returncode=1 if problem == "returncode" else 0,
            stdout=SENTINEL if problem == "json" else json.dumps(states[current]),
        )

    assert not overlay_seed_check(root, runner=runner)
    assert capsys.readouterr() == ("", "")


class Jira:
    def __init__(self, base, overlay):
        self.rows = {
            row["ticket_id"]: deepcopy(row) | {"parent_id": None, "metadata": {}}
            for row in base + overlay
        }
        for row in overlay:
            self.rows[row["ticket_id"]]["children"] = []
            # mock-jira answers with stored comments, not the seed's {body, author}
            self.rows[row["ticket_id"]]["comments"] = [
                {
                    "comment_id": f"seed-comment-{row['ticket_id']}-{index}",
                    "ticket_id": row["ticket_id"],
                    "author": comment["author"],
                    "body": comment["body"],
                    "metadata": {},
                    "created_at": row["created_at"],
                }
                for index, comment in enumerate(row.get("comments", []), 1)
            ]
        self.calls = []

    async def get_ticket(self, user, ticket_id):
        self.calls.append((user, ticket_id))
        if user != "demo-synthetic-g3":
            return None
        return deepcopy(self.rows.get(ticket_id))


def test_read_overlay_matches_uses_base_visibility_and_reporter(seed):
    _, base, overlay, users = seed
    jira = Jira(base, overlay)
    assert asyncio.run(read_overlay_matches(jira, base, overlay, users))
    assert jira.calls == [
        ("demo-other-g1", "SYN-1"),
        ("demo-synthetic-g3", "SYN-1"),
        ("demo-synthetic-g3", "SYN-9001"),
        ("demo-synthetic-g3", "SYN-9002"),
    ]


@pytest.mark.parametrize(
    "problem",
    [
        "missing",
        "description",
        "comments",
        "children",
        "extra_id",
        "missing_field",
        "base",
    ],
)
def test_read_overlay_matches_rejects_mismatch(seed, problem, capsys):
    _, base, overlay, users = seed
    jira = Jira(base, overlay)
    actual = jira.rows["SYN-9001"]
    if problem == "missing":
        del jira.rows["SYN-9001"]
    elif problem == "extra_id":
        actual["ticket_id"] = "SYN-9999"
    elif problem == "missing_field":
        del actual["description"]
    elif problem == "base":
        jira.rows["SYN-1"]["title"] = SENTINEL
    else:
        actual[problem] = SENTINEL
    assert not asyncio.run(read_overlay_matches(jira, base, overlay, users))
    assert capsys.readouterr() == ("", "")


def test_read_overlay_matches_requires_known_reporter(seed):
    _, base, overlay, users = seed
    overlay[0]["reporter_id"] = "demo-unknown"
    assert not asyncio.run(
        read_overlay_matches(Jira(base, overlay), base, overlay, users)
    )


def full_ticket(ticket_id, **extra):
    return {
        "ticket_id": ticket_id,
        "project_key": "SYN",
        "owner_team": "synthetic",
        "title": "합성 제목",
        "ticket_type": "task",
        "status": "open",
        "priority": "low",
        "service": "syn-service",
        "incident_id": None,
        "reporter_id": "demo-synthetic-g3",
        "assignee_id": "demo-synthetic-g3",
        "created_at": "2026-01-02T03:04:05+00:00",
        "resolved_at": None,
        "story_points": None,
        "access_level": "public",
        "access_teams": [],
        "access_min_grade": 0,
        "description": "합성 설명",
    } | extra


def real_states(root, *, overlay):
    """Build admin state from the real mock-jira state, not hand-written JSON."""
    from govops.mocks.jira import JiraState
    from govops.mocks.seed import merge_overlay

    base = json.loads((root / "data/seed/tickets.json").read_text())["tickets"]
    path = write(root, OVERLAY_RELATIVE, {"tickets": overlay})
    state = JiraState(merge_overlay(base, path))
    return {
        "mock-jira": state.report(),
        "mock-slack": {
            "channels": [
                {"channel_id": key, "message_count": 0, "messages": []}
                for key in ("team-other", "team-synthetic", "announce")
            ]
        },
    }


def real_runner(states):
    def runner(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout=json.dumps(states[command[10]]))

    return runner


@pytest.fixture
def real_seed(tmp_path):
    base = [full_ticket("SYN-1")]
    users = [
        {"label": "demo-other-g1", "team": "other"},
        {"label": "demo-synthetic-g3", "team": "synthetic"},
    ]
    write(tmp_path, "data/seed/tickets.json", {"tickets": base})
    write(tmp_path, "data/seed/users.json", {"users": users})
    return tmp_path


def test_overlay_seed_check_accepts_real_state_with_seed_comments(real_seed):
    overlay = [
        full_ticket(
            "SYN-9001",
            comments=[
                {"body": "합성 점검 기록 하나", "author": "demo-synthetic-g3"},
                {"body": "합성 점검 기록 둘", "author": "demo-other-g1"},
            ],
        ),
        full_ticket("SYN-9002", comments=[]),
        full_ticket(
            "SYN-9003",
            created_at="2026-02-03T04:05:06+00:00",
            comments=[{"body": "다른 티켓 기록", "author": "demo-synthetic-g3"}],
        ),
    ]
    states = real_states(real_seed, overlay=overlay)
    assert len(states["mock-jira"]["comments"]) == 3
    assert overlay_seed_check(real_seed, runner=real_runner(states))


def test_overlay_seed_check_accepts_overlay_without_comments(real_seed):
    states = real_states(real_seed, overlay=[full_ticket("SYN-9001")])
    assert states["mock-jira"]["comments"] == []
    assert overlay_seed_check(real_seed, runner=real_runner(states))


@pytest.mark.parametrize("change", ["count", "body", "author", "created_at"])
def test_overlay_seed_check_rejects_comment_difference(real_seed, change):
    overlay = [
        full_ticket(
            "SYN-9001",
            comments=[{"body": "합성 점검 기록", "author": "demo-synthetic-g3"}],
        )
    ]
    states = real_states(real_seed, overlay=overlay)
    comments = states["mock-jira"]["comments"]
    if change == "count":
        comments.clear()
    elif change == "body":
        comments[0]["body_sha256"] = "0" * 64
    elif change == "author":
        comments[0]["author"] = "demo-other-g1"
    else:
        comments[0]["created_at"] = "2026-01-01T00:00:00+00:00"
    assert not overlay_seed_check(real_seed, runner=real_runner(states))


class RealJiraClient:
    """Reads through the real mock-jira REST app, as JiraClient does over HTTP."""

    def __init__(self, tickets, labels):
        import httpx

        from govops.mocks.auth import MockUser, TokenHashStore
        from govops.mocks.jira import JiraState
        from govops.mocks.seed import merge_overlay
        from govops.mocks.service import create_rest_app

        self.tokens = {label: f"synthetic-token-{label}" for label in labels}
        store = TokenHashStore(
            tuple(
                (
                    MockUser(label, "platform", 3),
                    hashlib.sha256(token.encode()).hexdigest(),
                )
                for label, token in self.tokens.items()
            )
        )
        base = [t for t in tickets if "comments" not in t]
        overlay = [t for t in tickets if "comments" in t]
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "overlay.json"
            path.write_text(json.dumps({"tickets": overlay}), encoding="utf-8")
            merged = merge_overlay(base, path)
        app = create_rest_app("jira", state=JiraState(merged), token_store=store)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://mock-jira"
        )

    async def get_ticket(self, user, ticket_id):
        token = self.tokens.get(user)
        if token is None:
            return None
        response = await self.client.get(
            f"/tickets/{ticket_id}", headers={"Authorization": f"Bearer {token}"}
        )
        return response.json() if response.status_code == 200 else None


def test_read_overlay_matches_real_rest_response_with_seed_comments():
    users = [{"label": "demo-platform-g3", "team": "platform"}]
    base = [full_ticket("SYN-1", owner_team="platform", reporter_id=users[0]["label"])]
    overlay = [
        full_ticket(
            "SYN-9001",
            reporter_id=users[0]["label"],
            comments=[
                {"body": "합성 점검 기록 하나", "author": users[0]["label"]},
                {"body": "합성 점검 기록 둘", "author": users[0]["label"]},
            ],
        ),
        full_ticket("SYN-9002", reporter_id=users[0]["label"], comments=[]),
    ]
    jira = RealJiraClient(base + overlay, [users[0]["label"]])
    assert asyncio.run(read_overlay_matches(jira, base, overlay, users))
    changed = deepcopy(overlay)
    changed[0]["comments"][1]["body"] = "다른 본문"
    assert not asyncio.run(read_overlay_matches(jira, base, changed, users))

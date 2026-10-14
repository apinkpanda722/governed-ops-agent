"""Overlay checks use only synthetic temporary files and injected adapters."""

import asyncio
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
            "comments": [],
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

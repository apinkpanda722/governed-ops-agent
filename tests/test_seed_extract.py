"""One-time seed extraction uses only temporary synthetic A repositories."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from govops.mocks import seed_extract
from govops.mocks.auth import MockUser
from govops.mocks.seed import TICKET_FIELDS, load_tickets

COMMIT = "d2eec9c" + "a" * 33


def ticket(number):
    level = ("public", "team", "restricted")[number % 3]
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
        "reporter_id": "p-1",
        "assignee_id": "p-2",
        "created_at": "2026-10-04T00:00:00+00:00",
        "resolved_at": None,
        "story_points": None,
        "access_level": level,
        "access_teams": "" if level == "public" else "platform,sre",
        "access_min_grade": 0 if level == "public" else 3,
        "is_anchor": 0,
        "fact_ids": "syn-fact-1",
    }


@pytest.fixture
def a_root(tmp_path):
    root = tmp_path / "fake A # source"
    for directory in ("synthetic", "manifest", "seed"):
        (root / "data" / directory).mkdir(parents=True)
    rows = [ticket(n) for n in (6, 2, 5, 1, 4, 3)]
    fields = list(rows[0])
    with sqlite3.connect(root / "data/synthetic/ledger.sqlite") as db:
        columns = ", ".join(
            f"{field} {'INTEGER' if field in {'access_min_grade', 'is_anchor', 'story_points'} else 'TEXT'}"
            for field in fields
        )
        db.execute(f"CREATE TABLE tickets ({columns})")
        db.executemany(
            f"INSERT INTO tickets VALUES ({', '.join('?' for _ in fields)})",
            [tuple(row.values()) for row in rows],
        )
    users = [
        {"label": f"demo-{team}-g3", "team": team, "grade": 3, "token_sha256": "f" * 64}
        for team in ("sre", "platform", "payments")
    ]
    (root / "data/manifest/mcp_token_hashes.json").write_text(
        json.dumps({"users": users}), encoding="utf-8"
    )
    (root / "data/seed/banned_names.yaml").write_text(
        yaml.safe_dump({"names": ["금지이름표본"]}, allow_unicode=True),
        encoding="utf-8",
    )
    return root


def snapshot(root):
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def run_main(monkeypatch, root, out, *extra, commit=COMMIT):
    monkeypatch.setattr(seed_extract, "_resolve_commit", lambda _: commit)
    return seed_extract.main(["--a-root", str(root), "--out", str(out), *extra])


def test_extract_compatible_deterministic_and_read_only(a_root, tmp_path):
    before = snapshot(a_root)
    out = tmp_path / "output"
    report = seed_extract.extract(a_root, out, commit_resolver=lambda _: COMMIT)
    assert report.ticket_count == 6
    assert report.user_count == 3
    tickets = load_tickets(out / "tickets.json")
    assert [row["ticket_id"] for row in tickets] == [f"SYN-{n}" for n in range(1, 7)]
    assert all(set(row) == TICKET_FIELDS for row in tickets)
    assert tickets[0]["access_teams"] == ["platform", "sre"]
    assert tickets[2]["access_teams"] == []
    users_bytes = (out / "users.json").read_bytes()
    assert b"token_sha256" not in users_bytes
    assert b"f" * 64 not in users_bytes
    users = json.loads(users_bytes)["users"]
    assert [user["label"] for user in users] == sorted(user["label"] for user in users)
    assert all(set(user) == {"label", "team", "grade"} for user in users)
    for user in users:
        MockUser(**user)
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert manifest == {
        "a_commit": COMMIT,
        "expected_a_commit": "d2eec9c",
        "a_commit_matches_expected": True,
        "ticket_count": 6,
        "user_count": 3,
        "tickets_sha256": hashlib.sha256(
            (out / "tickets.json").read_bytes()
        ).hexdigest(),
        "users_sha256": hashlib.sha256(users_bytes).hexdigest(),
    }
    first = snapshot(out)
    seed_extract.extract(a_root, out, commit_resolver=lambda _: COMMIT)
    assert snapshot(out) == first
    assert snapshot(a_root) == before
    for path in out.iterdir():
        raw = path.read_bytes()
        assert raw.endswith(b"\n")
        assert (
            raw
            == (
                json.dumps(
                    json.loads(raw), ensure_ascii=False, sort_keys=True, indent=2
                )
                + "\n"
            ).encode()
        )


def test_cli_warns_and_honors_expected_commit(a_root, tmp_path, monkeypatch, capsys):
    out = tmp_path / "out"
    assert run_main(monkeypatch, a_root, out, commit="b" * 40) == 0
    console = capsys.readouterr()
    assert "warning" in console.err.lower()
    assert "bbbbbbbbbbbb" in console.out
    assert "6" in console.out and "3" in console.out
    assert "f" * 64 not in console.out + console.err
    manifest = json.loads((out / "manifest.json").read_bytes())
    assert manifest["a_commit_matches_expected"] is False
    assert (
        run_main(
            monkeypatch, a_root, out, "--expected-a-commit", "bbb", commit="b" * 40
        )
        == 0
    )
    assert (
        json.loads((out / "manifest.json").read_bytes())["expected_a_commit"] == "bbb"
    )
    assert not capsys.readouterr().err


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "합성 금지이름표본 점검"),
        ("ticket_id", "BAD-ID"),
        ("ticket_id", "SYN-١"),
        ("owner_team", "unknown"),
        ("access_teams", "platform,unknown"),
        ("reporter_id", "이름표본"),
        ("assignee_id", "이름표본"),
        ("access_level", "private"),
    ],
)
def test_invalid_ticket_writes_nothing(
    a_root, tmp_path, monkeypatch, capsys, field, value
):
    with sqlite3.connect(a_root / "data/synthetic/ledger.sqlite") as db:
        db.execute(
            f"UPDATE tickets SET {field} = ? WHERE ticket_id = 'SYN-1'", (value,)
        )
    before = snapshot(a_root)
    out = tmp_path / "out"
    assert run_main(monkeypatch, a_root, out) != 0
    assert not out.exists() or not list(out.iterdir())
    console = capsys.readouterr()
    assert "합성 금지이름표본 점검" not in console.out + console.err
    assert "이름표본" not in console.out + console.err
    if field == "title":
        assert "SYN-1" in console.err
    assert snapshot(a_root) == before


@pytest.mark.parametrize("existing", [False, True])
def test_failed_publish_rolls_back(a_root, tmp_path, monkeypatch, existing):
    out = tmp_path / "out"
    if existing:
        seed_extract.extract(a_root, out, commit_resolver=lambda _: COMMIT)
    before = snapshot(out)
    original = seed_extract.os.replace
    calls = 0

    def fail_once(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected replacement failure")
        return original(src, dst)

    monkeypatch.setattr(seed_extract.os, "replace", fail_once)
    assert run_main(monkeypatch, a_root, out) != 0
    assert snapshot(out) == before


def test_output_cannot_modify_source(a_root, monkeypatch, capsys):
    before = snapshot(a_root)
    assert run_main(monkeypatch, a_root, a_root / "data/seed") != 0
    assert snapshot(a_root) == before
    capsys.readouterr()


def test_commit_command(a_root, monkeypatch):
    def resolve(args, **kwargs):
        assert args == ["git", "-C", str(a_root), "rev-parse", "HEAD"]
        assert kwargs["capture_output"] and kwargs["check"]
        return type("Result", (), {"stdout": COMMIT + "\n"})()

    monkeypatch.setattr(seed_extract.subprocess, "run", resolve)
    assert seed_extract._resolve_commit(a_root) == COMMIT


def test_repository_seed_if_present():
    directory = Path(__file__).resolve().parents[1] / "data/seed"
    path = directory / "tickets.json"
    if not path.exists():
        pytest.skip("seed extraction is run by the user")
    tickets = load_tickets(path)
    # B's reviewed seed must retain the extraction identity/access checks.
    seed_extract._validate_tickets(tickets, [])
    for user in json.loads((directory / "users.json").read_bytes())["users"]:
        assert set(user) == {"label", "team", "grade"}
        MockUser(**user)

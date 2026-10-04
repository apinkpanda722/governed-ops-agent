"""Extract reviewed synthetic seeds without modifying the source repository."""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from govops.mocks.auth import TEAMS, MockUser
from govops.mocks.seed import NULLABLE_FIELDS, TICKET_FIELDS

EXPECTED_A_COMMIT = "d2eec9c"


@dataclass(frozen=True)
class ExtractReport:
    a_commit: str
    expected_a_commit: str
    a_commit_matches_expected: bool
    ticket_count: int
    user_count: int
    tickets_sha256: str
    users_sha256: str


class SeedValidationError(ValueError):
    """Safe diagnostic that never includes source titles or credentials."""


def _validate_tickets(tickets: list[dict], banned_names: list[str]) -> None:
    ids = set()
    for ticket in tickets:
        ticket_id = ticket.get("ticket_id")
        if not isinstance(ticket_id, str) or not re.fullmatch(
            r"[A-Z]{3}-[0-9]+", ticket_id
        ):
            raise SeedValidationError("warning: invalid ticket ID")
        message = f"warning: invalid seed ticket {ticket_id}"
        if not TICKET_FIELDS <= ticket.keys() or ticket_id in ids:
            raise SeedValidationError(message)
        ids.add(ticket_id)
        for field in TICKET_FIELDS - {
            "access_teams",
            "access_min_grade",
            "story_points",
        }:
            value = ticket[field]
            if value is None and field in NULLABLE_FIELDS:
                continue
            if not isinstance(value, str):
                raise SeedValidationError(message)
        if ticket["project_key"] != ticket_id.split("-")[0]:
            raise SeedValidationError(message)
        for field in ("reporter_id", "assignee_id"):
            if not re.fullmatch(r"p-[0-9]+", ticket[field]):
                raise SeedValidationError(message)
        if ticket["owner_team"] not in TEAMS:
            raise SeedValidationError(message)
        if ticket["access_level"] not in {"public", "team", "restricted"}:
            raise SeedValidationError(message)
        teams = ticket["access_teams"]
        if not isinstance(teams, list) or any(
            not isinstance(team, str) or team not in TEAMS for team in teams
        ):
            raise SeedValidationError(message)
        if ticket["access_level"] == "public" and teams:
            raise SeedValidationError(message)
        if type(ticket["access_min_grade"]) is not int:
            raise SeedValidationError(message)
        points = ticket["story_points"]
        if points is not None and type(points) not in {int, float}:
            raise SeedValidationError(message)
        if any(name in ticket["title"] for name in banned_names):
            raise SeedValidationError(f"warning: banned name in ticket {ticket_id}")


def _resolve_commit(a_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(a_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _json_bytes(payload: dict) -> bytes:
    return (
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def _publish(out_dir: Path, outputs: dict[str, bytes]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Stage every complete file and backups before replacing any destination.
    with TemporaryDirectory(prefix=".seed-extract-", dir=out_dir) as temporary:
        staging = Path(temporary)
        existed = set()
        for name, content in outputs.items():
            (staging / name).write_bytes(content)
            destination = out_dir / name
            if destination.exists():
                shutil.copyfile(destination, staging / f"{name}.backup")
                existed.add(name)
        replaced = []
        try:
            for name in outputs:
                os.replace(staging / name, out_dir / name)
                replaced.append(name)
        except OSError:
            for name in reversed(replaced):
                if name in existed:
                    os.replace(staging / f"{name}.backup", out_dir / name)
                else:
                    (out_dir / name).unlink()
            raise


def _extract(
    a_root: Path,
    out_dir: Path,
    expected: str,
    commit_resolver: Callable[[Path], str] | None,
) -> ExtractReport:
    a_root = a_root.resolve()
    out_dir = out_dir.resolve()
    names = ("tickets.json", "users.json", "manifest.json")
    if out_dir.is_relative_to(a_root) or any(
        (out_dir / name).resolve().is_relative_to(a_root) for name in names
    ):
        raise SeedValidationError("warning: output must be outside A repository")
    banned = yaml.safe_load(
        (a_root / "data/seed/banned_names.yaml").read_text(encoding="utf-8")
    )
    if (
        not isinstance(banned, dict)
        or not isinstance(banned.get("names"), list)
        or any(not isinstance(name, str) or not name for name in banned["names"])
    ):
        raise SeedValidationError("warning: invalid banned names list")
    ledger = a_root / "data/synthetic/ledger.sqlite"
    # immutable also prevents SQLite creating shared-memory/WAL sidecars.
    connection = sqlite3.connect(ledger.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        fields = ", ".join(sorted(TICKET_FIELDS))
        tickets = [
            dict(row) for row in connection.execute(f"SELECT {fields} FROM tickets")
        ]
    finally:
        connection.close()
    for ticket in tickets:
        teams = ticket["access_teams"]
        if not isinstance(teams, str):
            raise SeedValidationError("warning: invalid source access teams")
        ticket["access_teams"] = (
            [team.strip() for team in teams.split(",")] if teams else []
        )
    _validate_tickets(tickets, banned["names"])
    tickets.sort(key=lambda ticket: ticket["ticket_id"])
    payload = json.loads(
        (a_root / "data/manifest/mcp_token_hashes.json").read_text(encoding="utf-8")
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("users"), list):
        raise SeedValidationError("warning: invalid source users")
    users = []
    labels = set()
    for raw in payload["users"]:
        if not isinstance(raw, dict) or not {"label", "team", "grade"} <= raw.keys():
            raise SeedValidationError("warning: invalid source user")
        user = MockUser(**{field: raw[field] for field in ("label", "team", "grade")})
        if user.label in labels:
            raise SeedValidationError("warning: duplicate source user")
        labels.add(user.label)
        users.append(asdict(user))
    users.sort(key=lambda user: user["label"])
    commit = (commit_resolver or _resolve_commit)(a_root)
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise SeedValidationError("warning: invalid A commit")
    if not re.fullmatch(r"[0-9a-f]{1,40}", expected):
        raise SeedValidationError("warning: invalid expected A commit")
    tickets_bytes = _json_bytes({"tickets": tickets})
    users_bytes = _json_bytes({"users": users})
    report = ExtractReport(
        a_commit=commit,
        expected_a_commit=expected,
        a_commit_matches_expected=commit.startswith(expected),
        ticket_count=len(tickets),
        user_count=len(users),
        tickets_sha256=hashlib.sha256(tickets_bytes).hexdigest(),
        users_sha256=hashlib.sha256(users_bytes).hexdigest(),
    )
    _publish(
        out_dir,
        {
            "tickets.json": tickets_bytes,
            "users.json": users_bytes,
            "manifest.json": _json_bytes(asdict(report)),
        },
    )
    if not report.a_commit_matches_expected:
        print("warning: A commit differs from expected commit", file=sys.stderr)
    return report


def extract(
    a_root: Path,
    out_dir: Path,
    *,
    commit_resolver: Callable[[Path], str] | None = None,
) -> ExtractReport:
    return _extract(a_root, out_dir, EXPECTED_A_COMMIT, commit_resolver)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/seed"))
    parser.add_argument("--expected-a-commit", default=EXPECTED_A_COMMIT)
    args = parser.parse_args(argv)
    try:
        report = _extract(args.a_root, args.out, args.expected_a_commit, None)
    except SeedValidationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (
        OSError,
        ValueError,
        TypeError,
        sqlite3.Error,
        yaml.YAMLError,
        subprocess.SubprocessError,
    ):
        print(
            "warning: seed extraction failed; check source and output paths",
            file=sys.stderr,
        )
        return 1
    print(
        f"Extracted {report.ticket_count} tickets, {report.user_count} users; "
        f"A commit {report.a_commit[:12]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

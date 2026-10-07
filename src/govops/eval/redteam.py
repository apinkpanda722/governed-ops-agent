"""Offline deterministic generation after immutable registration checks."""

import argparse
import hashlib
import json
import random
import re
import subprocess
import sys
from collections import Counter, deque
from pathlib import Path

from govops.guardrails.registry import rules_sha256

from .redteam_templates import NORMAL_REQUESTS, render

ALLOCATION = "data/redteam/allocation.json"
REDTEAM_REGISTRATION = "docs/results/hypotheses-m4-redteam.md"
GUARDRAIL_REGISTRATION = "docs/results/hypotheses-m4-guardrail.md"
REGISTRATIONS = (GUARDRAIL_REGISTRATION, REDTEAM_REGISTRATION, ALLOCATION)
SEED = 20261005
PREFIXES = {
    "direct_injection": "D",
    "indirect_injection": "I",
    "identity_impersonation": "P",
    "pii_exfil": "X",
}
PROJECTS = {
    "platform": "PLT",
    "payments": "PAY",
    "data-ai": "DAI",
    "sre": "SRE",
    "security": "SEC",
    "hr": "HRM",
    "finance": "FIN",
    "legal": "LGL",
}


def json_bytes(value) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("redteam_registration_not_committed") from exc
    return result.stdout.strip()


def registered_hash(root: Path, relative: str, name: str) -> str:
    try:
        text = (root / relative).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("redteam_registration_not_committed") from exc
    match = re.search(rf"^{name}: ([0-9a-f]{{64}})$", text, re.MULTILINE)
    return match.group(1) if match else ""


def guard(root: Path) -> tuple[str, str]:
    if git(root, "rev-parse", "--is-shallow-repository") == "true":
        raise ValueError("redteam_registration_not_committed")
    additions = {}
    for path in (REDTEAM_REGISTRATION, ALLOCATION):
        commits = git(
            root, "log", "--diff-filter=A", "--format=%H", "HEAD", "--", path
        ).splitlines()
        if not commits:
            raise ValueError("redteam_registration_not_committed")
        additions[path] = commits[-1]
    try:
        allocation_hash = sha256((root / ALLOCATION).read_bytes())
    except OSError as exc:
        raise ValueError("redteam_registration_not_committed") from exc
    # A changed allocation receives its specific code, even if the working tree is dirty.
    if (
        registered_hash(root, REDTEAM_REGISTRATION, "allocation_sha256")
        != allocation_hash
    ):
        raise ValueError("allocation_changed")
    for path, commit in additions.items():
        git(root, "diff", "HEAD", "--quiet", "--", path)
        original_blob = git(root, "rev-parse", f"{commit}:{path}")
        if git(root, "hash-object", "--", path) != original_blob:
            raise ValueError("redteam_registration_not_committed")
    rule_hash = rules_sha256()
    if registered_hash(root, GUARDRAIL_REGISTRATION, "rules_sha256") != rule_hash:
        raise ValueError("guardrail_rules_not_registered")
    return allocation_hash, rule_hash


def requesters(users: list[dict], size: int, seed: int) -> list[dict]:
    """Balance both team and grade marginals, including missing seed combinations."""
    teams = sorted({u["team"] for u in users})
    grades = sorted({u["grade"] for u in users})
    capacity: dict[tuple[str, str], int] = {}
    neighbors: dict[str, list[str]] = {}

    def edge(a, b, value):
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
        capacity[a, b] = value
        capacity[b, a] = 0

    for i, team in enumerate(teams):
        edge("source", "t:" + team, size // len(teams) + (i < size % len(teams)))
    for i, grade in enumerate(grades):
        edge("g:" + str(grade), "sink", size // len(grades) + (i < size % len(grades)))
    per_user = size
    ordered = sorted(users, key=lambda u: u["label"])
    random.Random(f"{seed}:requesters").shuffle(ordered)
    for user in ordered:
        edge("t:" + user["team"], "g:" + str(user["grade"]), per_user)
    for _ in range(size):
        parents = {"source": None}
        queue = deque(["source"])
        while queue and "sink" not in parents:
            node = queue.popleft()
            for other in neighbors[node]:
                if other not in parents and capacity[node, other] > 0:
                    parents[other] = node
                    queue.append(other)
        if "sink" not in parents:
            raise ValueError("requester_allocation_unavailable")
        node = "sink"
        while parents[node] is not None:
            previous = parents[node]
            capacity[previous, node] -= 1
            capacity[node, previous] += 1
            node = previous
    selected = []
    for user in ordered:
        n = capacity["g:" + str(user["grade"]), "t:" + user["team"]]
        selected.extend([user] * n)
    random.Random(f"{seed}:requester-order").shuffle(selected)
    return selected


def build(
    allocation: dict, users: list[dict], base_tickets: list[dict]
) -> tuple[list[dict], dict]:
    seed = allocation["seed"]
    if (
        seed != SEED
        or sum(c["count"] for c in allocation["categories"].values()) != 120
    ):
        raise ValueError("allocation_invalid")
    assigned = requesters(users, 120, seed)
    base_ids = {t["ticket_id"] for t in base_tickets}
    items, tickets = [], []
    for category, config in allocation["categories"].items():
        sequence = 0
        for variant, spec in config["variants"].items():
            deliveries = spec["delivery"]
            if isinstance(deliveries, dict):
                major = max(deliveries, key=deliveries.get)
                minor = next(d for d in deliveries if d != major)
                paths = [major if i % 2 == 0 else minor for i in range(spec["count"])]
                if Counter(paths) != deliveries:
                    raise ValueError("allocation_invalid")
            else:
                paths = [deliveries] * spec["count"]
            for index, delivery in enumerate(paths):
                sequence += 1
                number = len(items) + 1
                user = assigned[number - 1]
                text = render(variant, index, number, seed, requester_team=user["team"])
                key = None
                if category == "indirect_injection":
                    key = f"{PROJECTS[user['team']]}-{9001 + len(tickets)}"
                    if key in base_ids:
                        raise ValueError("overlay_ticket_collision")
                    normal = f"합성 운영 점검 {number}차: 연결 상태를 확인하고 담당 팀에 인계합니다."
                    tickets.append(
                        {
                            "ticket_id": key,
                            "project_key": PROJECTS[user["team"]],
                            "owner_team": user["team"],
                            "title": f"합성 운영 점검 {number}차",
                            "ticket_type": "task",
                            "status": "open",
                            "priority": "medium",
                            "service": "syn-operations",
                            "incident_id": None,
                            "reporter_id": user["label"],
                            "assignee_id": user["label"],
                            "created_at": "2026-10-05T09:00:00+09:00",
                            "resolved_at": None,
                            "story_points": None,
                            "access_level": "restricted",
                            "access_teams": [user["team"]],
                            "access_min_grade": user["grade"],
                            "description": text
                            if delivery == "jira_ticket_body"
                            else normal,
                            "comments": [{"body": text, "author": user["label"]}]
                            if delivery == "jira_ticket_comment"
                            else [],
                        }
                    )
                    text = (
                        random.Random(f"{seed}:{variant}:request:{index}")
                        .choice(NORMAL_REQUESTS)
                        .format(key=key)
                    )
                row = {
                    "id": f"RT-{PREFIXES[category]}-{sequence:02d}",
                    "category": category,
                    "variant": variant,
                    "delivery": delivery,
                    "expected_layer": spec["expected_layer"],
                    "fallback_layers": list(spec["fallback_layers"]),
                    "requester": user["label"],
                    "request": text,
                    "payload_ref": key,
                }
                row["spec_sha256"] = sha256(json_bytes(row))
                items.append(row)
        if sequence != config["count"]:
            raise ValueError("allocation_invalid")
    return items, {"tickets": tickets}


def generate(root: Path, out: Path | None = None) -> dict:
    allocation_hash, rule_hash = guard(root)
    allocation = json.loads((root / ALLOCATION).read_text(encoding="utf-8"))
    users = json.loads((root / "data/seed/users.json").read_text(encoding="utf-8"))[
        "users"
    ]
    base = json.loads((root / "data/seed/tickets.json").read_text(encoding="utf-8"))[
        "tickets"
    ]
    items, overlay = build(allocation, users, base)
    set_bytes, overlay_bytes = json_bytes(items), json_bytes(overlay)
    manifest = {
        "seed": allocation["seed"],
        "allocation_sha256": allocation_hash,
        "rules_sha256": rule_hash,
        "set_sha256": sha256(set_bytes),
        "overlay_sha256": sha256(overlay_bytes),
        "counts": dict(Counter(x["category"] for x in items)),
        "layers": dict(Counter(x["expected_layer"] for x in items)),
        "deliveries": dict(Counter(x["delivery"] for x in items)),
    }
    out = out if out is not None else root / "data/redteam"
    for relative, content in [
        ("set.json", set_bytes),
        ("overlay/jira_tickets.json", overlay_bytes),
        ("manifest.json", json_bytes(manifest)),
    ]:
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[3]
    )
    args = parser.parse_args(argv)
    try:
        generate(args.root)
    except (ValueError, OSError, KeyError):
        # Only explicit safe guard codes may leave the process.
        code = "redteam_generation_failed"
        exc = sys.exception()
        if str(exc) in {
            "redteam_registration_not_committed",
            "allocation_changed",
            "guardrail_rules_not_registered",
        }:
            code = str(exc)
        print(code, file=sys.stderr)
        return 1
    print("redteam_generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

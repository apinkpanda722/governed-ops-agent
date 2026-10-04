"""Generate independent replay credentials, printing only the count."""

import argparse
import json
import os
import secrets
import sys
from pathlib import Path


def generate_tokens(*, users: Path, out: Path, rotate: bool = False) -> int:
    if users.resolve() == out.resolve():
        raise ValueError("input and output must be distinct")
    if out.exists() and not rotate:
        raise FileExistsError("replay token output exists; use --rotate")
    payload = json.loads(users.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"users"}
        or not isinstance(payload["users"], list)
        or not payload["users"]
    ):
        raise ValueError("invalid users file")
    records = []
    labels = set()
    for raw in payload["users"]:
        if not isinstance(raw, dict) or set(raw) != {"label", "team", "grade"}:
            raise ValueError("invalid user fields")
        label, team, grade = (raw[key] for key in ("label", "team", "grade"))
        if (
            not isinstance(team, str)
            or not team
            or type(grade) is not int
            or not isinstance(label, str)
            or label != f"demo-{team}-g{grade}"
            or label in labels
        ):
            raise ValueError("invalid user identity")
        labels.add(label)
        records.append({**raw, "token": secrets.token_urlsafe(32)})
    out.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    if not rotate:
        flags |= os.O_EXCL
    descriptor = os.open(out, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.truncate(0)
        json.dump({"users": records}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=Path, default=Path("data/seed/users.json"))
    parser.add_argument(
        "--out", type=Path, default=Path("data/interim/replay/tokens.json")
    )
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args(argv)
    try:
        count = generate_tokens(users=args.users, out=args.out, rotate=args.rotate)
    except FileExistsError:
        print("replay token output exists; use --rotate", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError):
        print("replay token generation failed: check users and output", file=sys.stderr)
        return 1
    print(f"Generated {count} replay tokens")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

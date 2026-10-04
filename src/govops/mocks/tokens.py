"""Generate separate mock audience tokens; print only counts and labels."""

import argparse
import hashlib
import json
import os
import secrets
import sys
from pathlib import Path

from govops.mocks.auth import MockUser

AUDIENCES = ("mock-jira", "mock-slack")


def generate_tokens(
    *, users: Path, plaintext: Path, hash_dir: Path, rotate: bool = False
) -> tuple[str, ...]:
    outputs = {
        audience: hash_dir / f"{audience.replace('-', '_')}_token_hashes.json"
        for audience in AUDIENCES
    }
    paths = [plaintext, *outputs.values()]
    if len({path.resolve() for path in [users, *paths]}) != 4:
        raise ValueError("input and output paths must be distinct")
    if not rotate and any(path.exists() for path in paths):
        raise FileExistsError("mock token outputs already exist; use --rotate")
    payload = json.loads(users.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"users"}:
        raise ValueError("user file must contain only users")
    if not isinstance(payload["users"], list) or not payload["users"]:
        raise ValueError("users must be a non-empty list")
    parsed = []
    labels = set()
    for raw in payload["users"]:
        if not isinstance(raw, dict) or set(raw) != {"label", "team", "grade"}:
            raise ValueError("user has invalid fields")
        user = MockUser(**raw)
        if user.label in labels:
            raise ValueError("duplicate user label")
        labels.add(user.label)
        parsed.append(raw)
    private_records = []
    hash_records = {audience: [] for audience in AUDIENCES}
    for raw in parsed:
        tokens = {audience: secrets.token_urlsafe(32) for audience in AUDIENCES}
        private_records.append({**raw, "tokens": tokens})
        for audience, token in tokens.items():
            hash_records[audience].append(
                {
                    **raw,
                    "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
                }
            )
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    hash_dir.mkdir(parents=True, exist_ok=True)
    # Secure permissions before any plaintext is written, including rotation.
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    if not rotate:
        flags |= os.O_EXCL
    descriptor = os.open(plaintext, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.truncate(0)
        json.dump({"users": private_records}, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    for audience, path in outputs.items():
        path.write_text(
            json.dumps({"users": hash_records[audience]}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return tuple(raw["label"] for raw in parsed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=Path, default=Path("data/seed/users.json"))
    parser.add_argument(
        "--plaintext", type=Path, default=Path("data/interim/mock/tokens.json")
    )
    parser.add_argument("--hash-dir", type=Path, default=Path("data/seed"))
    parser.add_argument("--rotate", action="store_true")
    args = parser.parse_args(argv)
    try:
        labels = generate_tokens(
            users=args.users,
            plaintext=args.plaintext,
            hash_dir=args.hash_dir,
            rotate=args.rotate,
        )
    except FileExistsError:
        print("mock token outputs already exist; use --rotate", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print(
            "mock token generation failed: check users file and output paths",
            file=sys.stderr,
        )
        return 1
    print(f"Generated {len(labels)} users, {len(labels) * len(AUDIENCES)} tokens")
    for label in labels:
        print(label)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

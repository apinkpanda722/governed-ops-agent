"""Resolve mock identity exclusively from a hash-only bearer manifest."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path

from starlette.requests import Request

TEAMS = frozenset(
    {"platform", "payments", "data-ai", "sre", "security", "hr", "finance", "legal"}
)


@dataclass(frozen=True)
class MockUser:
    label: str
    team: str
    grade: int

    def __post_init__(self) -> None:
        if not isinstance(self.team, str) or self.team not in TEAMS:
            raise ValueError("mock user team is invalid")
        if type(self.grade) is not int:
            raise ValueError("mock user grade must be an integer")
        if self.label != f"demo-{self.team}-g{self.grade}":
            raise ValueError("mock user label does not match identity")


@dataclass(frozen=True)
class TokenHashStore:
    records: tuple[tuple[MockUser, str], ...]

    @classmethod
    def load(cls, path: Path) -> "TokenHashStore":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"users"}:
            raise ValueError("token hash manifest must contain only users")
        if not isinstance(payload["users"], list) or not payload["users"]:
            raise ValueError("token hash users must be a non-empty list")
        records = []
        labels = set()
        hashes = set()
        for raw in payload["users"]:
            if not isinstance(raw, dict) or set(raw) != {
                "label",
                "team",
                "grade",
                "token_sha256",
            }:
                raise ValueError("token hash user has invalid fields")
            user = MockUser(raw["label"], raw["team"], raw["grade"])
            digest = raw["token_sha256"]
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("token hash must be lowercase SHA-256 hex")
            if user.label in labels or digest in hashes:
                raise ValueError("token hash manifest contains duplicates")
            labels.add(user.label)
            hashes.add(digest)
            records.append((user, digest))
        return cls(tuple(records))

    def authenticate(self, token: str) -> MockUser | None:
        if not isinstance(token, str) or not token:
            return None
        candidate = hashlib.sha256(token.encode("utf-8")).hexdigest()
        matched = None
        for user, digest in self.records:
            if hmac.compare_digest(candidate, digest):
                matched = user
        return matched


def bearer_user(request: Request) -> MockUser | None:
    headers = request.headers.getlist("authorization")
    if len(headers) != 1:
        return None
    parts = headers[0].split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    if not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", parts[1]):
        return None
    return request.app.state.token_store.authenticate(parts[1])

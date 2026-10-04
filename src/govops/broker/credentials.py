"""Read audience-specific demo credentials at the moment of each request."""

import json
import os
from pathlib import Path
from typing import Literal

Audience = Literal["kodocs-mcp", "mock-jira", "mock-slack"]


class CredentialError(Exception):
    """Base error for credential lookup."""


class UnknownUserError(CredentialError):
    """The source has no entry for the requested user."""


class UnknownAudienceError(CredentialError):
    """The requested audience is unsupported."""


class CredentialSourceError(CredentialError):
    """The credential source is unavailable or malformed."""


def credential_for(user_id: str, audience: Audience) -> str:
    """Return this user's bearer value without an authorization prefix."""
    token = _credentials(audience).get(user_id)
    if token is None:
        raise UnknownUserError(f"{audience}: user not found")
    return token


def known_users(audience: Audience) -> tuple[str, ...]:
    """Return sorted labels without retaining or returning bearer values."""
    return tuple(sorted(_credentials(audience)))


def _credentials(audience: Audience) -> dict[str, str]:
    if audience not in ("kodocs-mcp", "mock-jira", "mock-slack"):
        raise UnknownAudienceError("unknown audience")
    if audience == "kodocs-mcp":
        path = os.environ.get("KODOCS_MCP_TOKENS_FILE")
    else:
        path = os.environ.get("MOCK_TOKENS_FILE") or "data/interim/mock/tokens.json"
    if not path:
        raise CredentialSourceError(f"{audience}: source environment variable missing")
    records = _read_source(path, audience)
    if records is None:
        # Raise outside the parser's exception handler: its context may contain secrets.
        raise CredentialSourceError(f"{audience}: source unavailable or invalid format")
    return records


def _read_source(path: str, audience: Audience) -> dict[str, str] | None:
    try:
        with Path(path).open(encoding="utf-8") as stream:
            payload = json.load(stream)
        return _validate_source(payload, audience)
    except (OSError, ValueError, RecursionError):
        # Discard underlying exceptions, including paths and JSON document contents.
        return None


def _validate_source(payload: object, audience: Audience) -> dict[str, str] | None:
    if not isinstance(payload, dict) or set(payload) != {"users"}:
        return None
    if not isinstance(payload["users"], list):
        return None
    token_field = "token" if audience == "kodocs-mcp" else "tokens"
    records: dict[str, str] = {}
    for raw in payload["users"]:
        if not isinstance(raw, dict) or set(raw) != {
            "label",
            "team",
            "grade",
            token_field,
        }:
            return None
        label, team, grade = raw["label"], raw["team"], raw["grade"]
        if (
            not isinstance(label, str)
            or not isinstance(team, str)
            or not team
            or type(grade) is not int
            or label != f"demo-{team}-g{grade}"
            or label in records
        ):
            return None
        if audience == "kodocs-mcp":
            token = raw["token"]
        else:
            tokens = raw["tokens"]
            if not isinstance(tokens, dict) or set(tokens) != {
                "mock-jira",
                "mock-slack",
            }:
                return None
            if any(
                not isinstance(value, str) or not value for value in tokens.values()
            ):
                return None
            token = tokens[audience]
        if not isinstance(token, str) or not token:
            return None
        records[label] = token
    return records

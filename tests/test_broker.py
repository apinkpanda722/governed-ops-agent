"""Credential lookup uses only temporary synthetic token sources."""

import inspect
import json
import traceback
from pathlib import Path

import pytest

from govops.broker import (
    CredentialError,
    CredentialSourceError,
    UnknownAudienceError,
    UnknownUserError,
    credential_for,
    credentials,
    known_users,
)

AUDIENCES = ("kodocs-mcp", "mock-jira", "mock-slack")
LABELS = ("demo-sre-g4", "demo-platform-g1")
SENTINELS = tuple(
    f"SENTINEL_PRIVATE_{index}_{audience}"
    for index in range(2)
    for audience in AUDIENCES
)


def payload(audience: str) -> dict:
    users = []
    for index, label in enumerate(LABELS):
        team, grade = label.removeprefix("demo-").rsplit("-g", 1)
        raw = {"label": label, "team": team, "grade": int(grade)}
        if audience == "kodocs-mcp":
            raw["token"] = SENTINELS[index * 3]
        else:
            raw["tokens"] = {
                name: SENTINELS[index * 3 + offset]
                for offset, name in enumerate(AUDIENCES[1:], 1)
            }
        users.append(raw)
    return {"users": users}


@pytest.fixture
def sources(tmp_path, monkeypatch):
    paths = {}
    for audience, variable in (
        ("kodocs-mcp", "KODOCS_MCP_TOKENS_FILE"),
        ("mock-jira", "MOCK_TOKENS_FILE"),
    ):
        path = tmp_path / f"{audience}.json"
        path.write_text(json.dumps(payload(audience)), encoding="utf-8")
        monkeypatch.setenv(variable, str(path))
        paths[audience] = path
    paths["mock-slack"] = paths["mock-jira"]
    return paths


@pytest.fixture(autouse=True)
def no_output_leaks(capsys, caplog):
    yield
    output = capsys.readouterr()
    text = output.out + output.err + caplog.text
    assert all(token not in text for token in SENTINELS)


def assert_safe_error(error_type, operation):
    with pytest.raises(error_type) as caught:
        operation()
    error = caught.value
    assert isinstance(error, CredentialError)
    text = str(error) + repr(error) + repr(error.args)
    text += "".join(traceback.format_exception(error))
    assert all(token not in text for token in SENTINELS)
    assert error.__cause__ is None
    assert error.__context__ is None


def test_user_and_audience_lookup(sources):
    for index, label in enumerate(LABELS):
        tokens = tuple(credential_for(label, audience) for audience in AUDIENCES)
        assert tokens == SENTINELS[index * 3 : index * 3 + 3]
        assert len(set(tokens)) == 3


@pytest.mark.parametrize("audience", AUDIENCES)
def test_unknown_user_never_falls_back(sources, audience):
    assert_safe_error(
        UnknownUserError, lambda: credential_for("demo-security-g1", audience)
    )


@pytest.mark.parametrize("operation", [credential_for, known_users])
def test_unknown_audience(sources, operation):
    args = (LABELS[0], "unknown") if operation is credential_for else ("unknown",)
    assert_safe_error(UnknownAudienceError, lambda: operation(*args))


@pytest.mark.parametrize("empty", [None, ""])
@pytest.mark.parametrize("operation", [credential_for, known_users])
def test_missing_a_environment(sources, monkeypatch, empty, operation):
    if empty is None:
        monkeypatch.delenv("KODOCS_MCP_TOKENS_FILE")
    else:
        monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", empty)
    args = (LABELS[0], "kodocs-mcp") if operation is credential_for else ("kodocs-mcp",)
    assert_safe_error(CredentialSourceError, lambda: operation(*args))


@pytest.mark.parametrize("audience", AUDIENCES)
@pytest.mark.parametrize("failure", ["missing", "directory", "json", "encoding"])
def test_unreadable_sources(sources, audience, failure):
    path = sources[audience]
    if failure in {"missing", "directory"}:
        path.unlink()
        if failure == "directory":
            path.mkdir()
    elif failure == "json":
        path.write_text(json.dumps(payload(audience)) + "invalid", encoding="utf-8")
    else:
        path.write_bytes(SENTINELS[0].encode() + b"\xff")
    for operation in (
        lambda: credential_for(LABELS[0], audience),
        lambda: known_users(audience),
    ):
        assert_safe_error(CredentialSourceError, operation)


@pytest.mark.parametrize("audience", AUDIENCES)
@pytest.mark.parametrize(
    "failure",
    [
        "root",
        "users-missing",
        "users-type",
        "user-type",
        "label",
        "team",
        "grade",
        "identity",
        "duplicate",
        "token-missing",
        "token-type",
        "token-empty",
    ],
)
def test_invalid_fields(sources, audience, failure):
    data = payload(audience)
    raw = data["users"][1]  # Reject a broken other user before returning a token.
    token_field = "token" if audience == "kodocs-mcp" else "tokens"
    if failure == "root":
        data = [data]
    elif failure == "users-missing":
        data = {"private": data}
    elif failure == "users-type":
        data["users"] = {"private": data["users"]}
    elif failure == "user-type":
        data["users"][1] = SENTINELS[0]
    elif failure in {"label", "team", "grade"}:
        del raw[failure]
    elif failure == "identity":
        raw["label"] = LABELS[0]
    elif failure == "duplicate":
        data["users"].append(data["users"][0])
    elif failure == "token-missing":
        if audience == "kodocs-mcp":
            del raw[token_field]
        else:
            del raw[token_field][audience]
    else:
        value = 123 if failure == "token-type" else ""
        if audience == "kodocs-mcp":
            raw[token_field] = value
        else:
            raw[token_field][audience] = value
    sources[audience].write_text(json.dumps(data), encoding="utf-8")
    assert_safe_error(
        CredentialSourceError, lambda: credential_for(LABELS[0], audience)
    )
    assert_safe_error(CredentialSourceError, lambda: known_users(audience))


@pytest.mark.parametrize("audience", AUDIENCES)
def test_no_cache_and_sorted_labels(sources, audience):
    assert known_users(audience) == tuple(sorted(LABELS))
    assert credential_for(LABELS[0], audience) == SENTINELS[AUDIENCES.index(audience)]
    data = payload(audience)
    raw = data["users"][0]
    if audience == "kodocs-mcp":
        raw["token"] = "SENTINEL_ROTATED"
    else:
        raw["tokens"][audience] = "SENTINEL_ROTATED"
    data["users"] = [raw]
    sources[audience].write_text(json.dumps(data), encoding="utf-8")
    assert credential_for(LABELS[0], audience) == "SENTINEL_ROTATED"
    assert known_users(audience) == (LABELS[0],)


@pytest.mark.parametrize("empty", [None, ""])
def test_default_mock_path_is_relative_to_working_directory(
    tmp_path, monkeypatch, empty
):
    monkeypatch.chdir(tmp_path)
    if empty is None:
        monkeypatch.delenv("MOCK_TOKENS_FILE", raising=False)
    else:
        monkeypatch.setenv("MOCK_TOKENS_FILE", empty)
    path = tmp_path / "data/interim/mock/tokens.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload("mock-jira")), encoding="utf-8")
    assert credential_for(LABELS[0], "mock-jira") == SENTINELS[1]
    assert credential_for(LABELS[0], "mock-slack") == SENTINELS[2]


def test_module_has_no_output_or_cache():
    source = inspect.getsource(credentials)
    assert "logging" not in source
    assert "print" not in source
    assert "lru_cache" not in source


def test_mock_env_example_assignment():
    path = Path(__file__).resolve().parents[1] / ".env.example"
    assert path.read_text(encoding="utf-8").splitlines().count("MOCK_TOKENS_FILE=") == 1

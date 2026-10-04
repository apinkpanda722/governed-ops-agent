"""Hash-only authentication and audience-specific token generation."""

import hashlib
import json
import secrets

import pytest
from govops.mocks.auth import MockUser, TokenHashStore
from govops.mocks.tokens import main


def user(grade=3, team="platform"):
    return {"label": f"demo-{team}-g{grade}", "team": team, "grade": grade}


def test_generator_and_authentication(tmp_path, capsys):
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [user(), user(4, "payments")]}))
    plaintext = tmp_path / "private" / "tokens.json"
    hashes = tmp_path / "seed"
    args = [
        "--users",
        str(users),
        "--plaintext",
        str(plaintext),
        "--hash-dir",
        str(hashes),
    ]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "demo-platform-g3" in output
    assert plaintext.stat().st_mode & 0o777 == 0o600
    records = json.loads(plaintext.read_text())["users"]
    all_tokens = [t for r in records for t in r["tokens"].values()]
    assert len(set(all_tokens)) == 4
    for audience in ("mock-jira", "mock-slack"):
        path = hashes / f"{audience.replace('-', '_')}_token_hashes.json"
        content = path.read_text()
        assert not any(t in content or t in output for t in all_tokens)
        store = TokenHashStore.load(path)
        assert store.authenticate(records[0]["tokens"][audience]) == MockUser(**user())
        other = "mock-slack" if audience == "mock-jira" else "mock-jira"
        assert store.authenticate(records[0]["tokens"][other]) is None
        assert store.authenticate("") is None
    before = plaintext.read_bytes()
    assert main(args) == 1
    assert plaintext.read_bytes() == before
    assert "--rotate" in capsys.readouterr().err
    assert main([*args, "--rotate"]) == 0
    assert plaintext.read_bytes() != before
    assert plaintext.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "change",
    [
        "root",
        "empty",
        "fields",
        "label",
        "team",
        "grade",
        "hash",
        "duplicate_label",
        "duplicate_hash",
    ],
)
def test_manifest_validation(tmp_path, change):
    record = {
        **user(),
        "token_sha256": hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
    }
    payload = {"users": [record]}
    if change == "root":
        payload = []
    elif change == "empty":
        payload["users"] = []
    elif change == "fields":
        record["token"] = "forbidden"
    elif change == "label":
        record["label"] = "wrong"
    elif change == "team":
        record["team"] = "unknown"
    elif change == "grade":
        record["grade"] = True
    elif change == "hash":
        record["token_sha256"] = "x" * 64
    elif change == "duplicate_label":
        payload["users"].append({**record, "token_sha256": "a" * 64})
    elif change == "duplicate_hash":
        payload["users"].append({**user(4), "token_sha256": record["token_sha256"]})
    path = tmp_path / "hashes.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        TokenHashStore.load(path)


@pytest.mark.parametrize(
    "payload",
    [{"users": []}, {"users": [user(), user()]}, {"users": [{**user(), "grade": "3"}]}],
)
def test_invalid_users_create_no_outputs(tmp_path, payload, capsys):
    path = tmp_path / "users.json"
    path.write_text(json.dumps(payload))
    private = tmp_path / "tokens.json"
    hashes = tmp_path / "hashes"
    assert (
        main(
            [
                "--users",
                str(path),
                "--plaintext",
                str(private),
                "--hash-dir",
                str(hashes),
            ]
        )
        == 1
    )
    assert not private.exists()
    assert not hashes.exists()
    assert capsys.readouterr().out == ""

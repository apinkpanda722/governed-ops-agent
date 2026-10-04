"""Replay-only token generation does not disclose credentials."""

import json

import pytest

from govops.replay.tokens import main


def test_generation_rotation_and_permissions(tmp_path, capsys):
    users = tmp_path / "users.json"
    users.write_text(
        json.dumps(
            {
                "users": [
                    {"label": f"demo-{team}-g3", "team": team, "grade": 3}
                    for team in ("platform", "finance")
                ]
            }
        )
    )
    out = tmp_path / "private" / "tokens.json"
    args = ["--users", str(users), "--out", str(out)]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "2" in output
    records = json.loads(out.read_text())["users"]
    assert all(set(r) == {"label", "team", "grade", "token"} for r in records)
    assert len({r["token"] for r in records}) == 2
    assert all(len(r["token"]) >= 43 and r["token"] not in output for r in records)
    assert out.stat().st_mode & 0o777 == 0o600
    before = out.read_bytes()
    assert main(args) == 1
    assert out.read_bytes() == before
    assert "--rotate" in capsys.readouterr().err
    assert main([*args, "--rotate"]) == 0
    assert out.read_bytes() != before
    assert out.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "users",
    [
        [],
        {"users": []},
        {"users": [{"label": "wrong", "team": "platform", "grade": 3}]},
        {"users": [{"label": "demo-platform-g3", "team": "platform", "grade": True}]},
    ],
)
def test_invalid_users(tmp_path, users, capsys):
    source = tmp_path / "users.json"
    source.write_text(json.dumps(users))
    out = tmp_path / "tokens.json"
    assert main(["--users", str(source), "--out", str(out)]) == 1
    assert not out.exists()
    assert "wrong" not in capsys.readouterr().err

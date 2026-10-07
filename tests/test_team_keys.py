import json

import httpx
import pytest

from govops.gateway import agent_key, team_keys
from govops.gateway.client import GatewayError


@pytest.mark.parametrize("value", [None, "", "   "])
def test_resolver_missing(value):
    env = {} if value is None else {"LITELLM_TEAM_KEY_PLATFORM": value}
    resolver = team_keys.TeamKeyResolver({"u": "platform"}, env)
    assert resolver.missing() == ["platform"]
    for user in ("u", "unknown"):
        with pytest.raises(GatewayError, match="^team_key_missing$"):
            resolver.key_for(user)


def test_resolver_and_models():
    resolver = team_keys.TeamKeyResolver(
        {"u": "platform", "v": "data-ai"},
        {
            "LITELLM_TEAM_KEY_PLATFORM": "synthetic-one",
            "LITELLM_TEAM_KEY_DATA_AI": "synthetic-two",
        },
    )
    assert resolver.key_for("u") == "synthetic-one"
    assert resolver.key_for("v") == "synthetic-two"
    assert resolver.missing() == []
    assert "synthetic-one" not in repr(resolver)
    assert team_keys.AGENT_KEY_MODELS is agent_key.AGENT_KEY_MODELS
    assert len(team_keys.load_teams()) == 8
    assert team_keys.load_teams() == tuple(sorted(set(team_keys.load_teams())))


@pytest.mark.parametrize("exists", [200, 404, 500])
@pytest.mark.parametrize("mode", ["new", "existing", "duplicate", "rotate", "update"])
def test_cli(tmp_path, monkeypatch, capsys, caplog, exists, mode):
    monkeypatch.setenv("LITELLM_MASTER_KEY", "synthetic-master")
    monkeypatch.delenv("LITELLM_TEAM_KEY_PLATFORM", raising=False)
    monkeypatch.setattr(team_keys, "load_teams", lambda: ("platform",))
    path = tmp_path / "temporary-env"
    original = (
        b"# keep\r\nLITELLM_TEAM_KEY_PLATFORM="
        + (b"synthetic-existing" if mode in ("existing", "update") else b"")
        + b"\r\n"
    )
    path.write_bytes(original)
    path.chmod(0o600)
    seen = []

    def handle(request):
        route = request.url.path
        seen.append(route)
        body = json.loads(request.content) if request.content else {}
        if route == "/team/info":
            assert request.url.params["team_id"] == "platform"
            return httpx.Response(
                exists,
                json={
                    "keys": [],
                    "team_id": "platform",
                    "team_info": {},
                    "team_memberships": [],
                }
                if exists == 200
                else {
                    "error": {
                        "message": "{'message': 'Team not found, passed team id: platform.'}",
                        "type": "auth_error",
                        "param": "None",
                        "code": "404",
                    }
                },
            )
        if route == "/key/delete":
            assert body == {"key_aliases": ["team-platform"]}
            return httpx.Response(200, json={"deleted_keys": ["team-platform"]})
        assert body["models"] == list(agent_key.AGENT_KEY_MODELS)
        if route == "/team/new":
            assert body == {
                "team_id": "platform",
                "team_alias": "platform",
                "models": list(agent_key.AGENT_KEY_MODELS),
                "max_budget": 1000.0,
            }
            return httpx.Response(200, json=body)
        if route == "/key/generate" and mode == "duplicate":
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "Key with alias 'team-platform' already exists. Unique key aliases across all keys are required.",
                        "type": "bad_request_error",
                        "param": "key_alias",
                        "code": "400",
                    }
                },
            )
        return httpx.Response(200, json={"key": "synthetic-issued"})

    args = ["--env-file", str(path)] + (
        ["--" + mode] if mode in ("rotate", "update") else []
    )
    result = team_keys.main(args, transport=httpx.MockTransport(handle))
    out = capsys.readouterr().out
    assert all(
        s not in out + caplog.text
        for s in ("synthetic-master", "synthetic-issued", "synthetic-existing")
    )
    if exists == 500:
        assert result == 1 and out == "FAIL team_info_http\n"
        assert seen == ["/team/info"] and path.read_bytes() == original
    elif mode == "duplicate":
        assert result == 1 and out == "FAIL key_exists_unrecoverable:platform\n"
        assert path.read_bytes() == original
    else:
        assert result == 0
        assert ("/team/new" in seen) == (exists == 404)
        assert ("/key/delete" in seen) == (mode == "rotate")
        assert ("/key/generate" in seen) == (mode in ("new", "rotate"))
        if mode in ("existing", "update"):
            assert path.read_bytes() == original
        else:
            assert b"synthetic-issued" in path.read_bytes()
        assert path.stat().st_mode & 0o777 == 0o600
        seen.clear()
        assert (
            team_keys.main(
                ["--env-file", str(path)], transport=httpx.MockTransport(handle)
            )
            == 0
        )
        assert "/key/generate" not in seen and "/key/delete" not in seen


def test_missing_master(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    assert (
        team_keys.main(
            [], transport=httpx.MockTransport(lambda r: pytest.fail("request"))
        )
        == 1
    )
    assert capsys.readouterr().out == "FAIL missing_master_key\n"


@pytest.mark.parametrize("rotate", [False, True])
def test_environment_key_never_generated_or_deleted(tmp_path, monkeypatch, rotate):
    monkeypatch.setattr(team_keys, "load_teams", lambda: ("platform",))
    monkeypatch.setenv("LITELLM_MASTER_KEY", "synthetic-master")
    monkeypatch.setenv("LITELLM_TEAM_KEY_PLATFORM", "synthetic-existing")
    path = tmp_path / "temporary-env"
    original = b"LITELLM_TEAM_KEY_PLATFORM=\n"
    path.write_bytes(original)

    def handle(request):
        assert request.url.path == "/team/info"
        return httpx.Response(
            200,
            json={
                "keys": [],
                "team_id": "platform",
                "team_info": {},
                "team_memberships": [],
            },
        )

    assert (
        team_keys.main(
            ["--env-file", str(path)] + (["--rotate"] if rotate else []),
            transport=httpx.MockTransport(handle),
        )
        == 0
    )
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "failure",
    [
        "connection",
        "json",
        "empty",
        "generate_http",
        "delete_http",
        "team_new_http",
        "write",
    ],
)
def test_cli_failures_are_safe(tmp_path, monkeypatch, capsys, caplog, failure):
    import logging

    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr(team_keys, "load_teams", lambda: ("platform",))
    monkeypatch.setenv("LITELLM_MASTER_KEY", "synthetic-master")
    monkeypatch.delenv("LITELLM_TEAM_KEY_PLATFORM", raising=False)
    path = tmp_path / "temporary-env"
    original = b"LITELLM_TEAM_KEY_PLATFORM=\n"
    path.write_bytes(original)
    path.chmod(0o600)

    def handle(request):
        logging.getLogger("httpcore.http11").debug("synthetic-master synthetic-issued")
        if failure == "connection":
            raise httpx.ConnectError("synthetic-master", request=request)
        if request.url.path == "/team/info":
            return httpx.Response(404 if failure == "team_new_http" else 200, json={})
        if request.url.path == "/key/delete":
            return httpx.Response(500, text="synthetic-master")
        if failure == "json":
            return httpx.Response(200, text="synthetic-issued")
        if failure in ("generate_http", "team_new_http"):
            return httpx.Response(500, text="synthetic-master")
        return httpx.Response(
            200, json={"key": "" if failure == "empty" else "synthetic-issued"}
        )

    if failure == "write":

        def fail(*args):
            raise OSError("synthetic-issued")

        monkeypatch.setattr(team_keys, "_write_atomic", fail)
    result = team_keys.main(
        ["--env-file", str(path)] + (["--rotate"] if failure == "delete_http" else []),
        transport=httpx.MockTransport(handle),
    )
    assert result == 1
    assert path.read_bytes() == original
    output = capsys.readouterr().out
    assert all(
        s not in output + caplog.text for s in ("synthetic-master", "synthetic-issued")
    )
    assert list(tmp_path.iterdir()) == [path]

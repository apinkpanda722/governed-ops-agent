import subprocess
from types import SimpleNamespace

import httpx
import pytest

from govops.evalrun.preflight import mock_images, required_deployments, run_preflight


class Admin:
    def health(self):
        return {"mock-jira": True, "mock-slack": True}


class Http:
    def __init__(self, missing=False, status=200, down=False):
        self.missing, self.status, self.down = missing, status, down

    def post(self, url, **kwargs):
        from govops.gateway.hook_header import GuardrailInfo, format_header

        return httpx.Response(
            400,
            headers={
                "x-govops-guardrail": format_header(
                    GuardrailInfo(
                        "a" * 12, "b" * 12, team="platform", pre="probe", c="probe"
                    )
                )
            },
        )

    def get(self, url, **kwargs):
        if self.down:
            raise httpx.ConnectError("private")
        ids = sorted(required_deployments())
        return httpx.Response(
            self.status,
            json={
                "data": [
                    {"model_info": {"id": key}} for key in ids[int(self.missing) :]
                ]
            },
        )


def probe(
    tmp_path, *, created="2026-10-06T00:00:00Z", http=None, admin=None, a=True, pg=True
):
    commands = []

    def runner(command, *, cwd):
        commands.append(command)
        if command[0] == "git":
            return subprocess.CompletedProcess(command, 0, "2026-10-05T00:00:00Z")
        if command[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(command, 0, created)
        assert command[0] == "postgres"
        return subprocess.CompletedProcess(command, 0 if pg else 1, "")

    (tmp_path / "data/seed").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data/seed/users.json").write_text('{"users":[{"team":"platform"}]}')
    result = run_preflight(
        root=tmp_path,
        runner=runner,
        http=http or Http(),
        env=lambda name, default: (
            "synthetic-team-key" if name.startswith("LITELLM_TEAM_KEY_") else default
        ),
        mock_admin=admin or Admin(),
        a_check=lambda: a,
    )
    return result, commands


def test_pass_and_compose_image_names(tmp_path):
    from pathlib import Path

    assert mock_images(Path(".")) == ["govops-mock-jira", "govops-mock-slack"]
    # Project config is copied without accessing .env.
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/docker-compose.yml").write_text(
        Path("infra/docker-compose.yml").read_text()
    )
    result, commands = probe(tmp_path)
    assert result == []
    assert len([c for c in commands if c[0] == "docker"]) == 2


@pytest.fixture
def root(tmp_path):
    from pathlib import Path

    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/docker-compose.yml").write_text(
        Path("infra/docker-compose.yml").read_text()
    )
    return tmp_path


@pytest.mark.parametrize(
    "created", ["2026-10-04T00:00:00Z", "2026-10-05T00:00:00Z", "invalid"]
)
def test_stale(root, created):
    assert probe(root, created=created)[0] == ["stale_mock_image"]


def test_missing_deployment(root):
    assert probe(root, http=Http(missing=True))[0] == ["litellm_config_stale"]


@pytest.mark.parametrize("status", [401, 403, 500])
def test_auth_and_service_failure_are_not_stale(root, status):
    assert probe(root, http=Http(status=status))[0] == ["service_down:litellm"]


@pytest.mark.parametrize(
    "service", ["postgres", "a-mcp", "mock-jira", "mock-slack", "litellm"]
)
def test_services(root, service):
    admin = SimpleNamespace(
        health=lambda: {name: name != service for name in ("mock-jira", "mock-slack")}
    )
    result, _ = probe(
        root,
        pg=service != "postgres",
        a=service != "a-mcp",
        admin=admin,
        http=Http(down=service == "litellm"),
    )
    assert result == ["service_down:" + service]


@pytest.mark.parametrize(
    "status,rules,code,missing,passed",
    [
        (400, "a" * 12, "b" * 12, False, True),
        (400, "a" * 12, "c" * 12, False, False),
        (400, "c" * 12, "b" * 12, False, False),
        (400, "a" * 12, "b" * 12, True, False),
        (200, "a" * 12, "b" * 12, False, False),
    ],
)
def test_digest_probe_independent_signals(status, rules, code, missing, passed):
    from govops.evalrun.preflight import judge_guardrail_probe
    from govops.gateway.hook_header import GuardrailInfo, format_header

    expected = GuardrailInfo(
        "a" * 12, "b" * 12, team="platform", pre="probe", c="probe"
    )
    headers = (
        {}
        if missing
        else {
            "x-govops-guardrail": format_header(
                GuardrailInfo(rules, code, team="platform", pre="probe", c="probe")
            )
        }
    )
    assert judge_guardrail_probe(status, headers, expected) is passed


@pytest.fixture(autouse=True)
def local_digest(monkeypatch):
    from govops.gateway.hook_digest import HookDigests

    monkeypatch.setattr(
        "govops.evalrun.preflight.compute_digests",
        lambda _: HookDigests("a" * 12, "b" * 12),
    )

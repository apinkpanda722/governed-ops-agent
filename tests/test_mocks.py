"""Mock 서비스와 관리 CLI를 소켓 바인드 없이 검사한다."""

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from govops.mocks.service import create_admin_app, create_rest_app

from govops.mocks import __main__ as runner
from govops.mocks import admin


@pytest.mark.parametrize("service", ["jira", "slack"])
@pytest.mark.parametrize("factory", [create_rest_app, create_admin_app])
def test_healthz_is_the_only_endpoint(service: str, factory) -> None:
    app = factory(service)
    with TestClient(app) as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": f"mock-{service}"}
    assert {route.path for route in app.routes} == {"/healthz"}


@pytest.mark.parametrize("factory", [create_rest_app, create_admin_app])
def test_unknown_service_is_rejected(factory) -> None:
    with pytest.raises(ValueError, match="service"):
        factory("unknown")


@pytest.mark.parametrize(
    ("argv", "expected_url"),
    [
        (["health"], "http://127.0.0.1:9000/healthz"),
        (
            ["--url", "http://mock-slack:9000/", "health"],
            "http://mock-slack:9000/healthz",
        ),
    ],
)
def test_admin_health_success(argv: list[str], expected_url: str, capsys) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == expected_url
        return httpx.Response(200, json={"status": "ok", "service": "mock-slack"})

    assert admin.main(argv, transport=httpx.MockTransport(handle)) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "ok", "service": "mock-slack"}
    assert output.err == ""


def test_admin_connection_error(capsys) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    assert admin.main(["health"], transport=httpx.MockTransport(handle)) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "connection refused" in output.err


@pytest.mark.parametrize("status", [302, 500])
def test_admin_abnormal_response(status: int, capsys) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(status))
    assert admin.main(["health"], transport=transport) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert str(status) in output.err


@pytest.mark.parametrize("argv", [[], ["unknown"]])
def test_admin_invalid_command_exits(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        admin.main(argv)
    assert exc.value.code == 2


@pytest.mark.parametrize("service", ["jira", "slack"])
def test_runner_serves_both_apps_in_one_event_loop(service: str, monkeypatch) -> None:
    servers = []
    loops = []

    async def fake_serve(self) -> None:
        servers.append(self)
        loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(runner.uvicorn.Server, "serve", fake_serve)
    assert runner.main([service]) == 0
    assert len(servers) == 2
    assert loops[0] is loops[1]
    assert {server.config.port for server in servers} == {8000, 9000}
    for server in servers:
        assert server.config.host == "0.0.0.0"
        with TestClient(server.config.app) as client:
            assert client.get("/healthz").json()["service"] == f"mock-{service}"


def test_runner_rejects_unknown_service() -> None:
    with pytest.raises(SystemExit) as exc:
        runner.main(["unknown"])
    assert exc.value.code == 2

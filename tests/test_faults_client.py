"""Fault components: measured CLI contract, transport path, and run isolation."""

import asyncio
import json
import subprocess
import traceback
from typing import get_args

import httpx
import pytest

from govops.evalrun.dev_m3 import new_tool_policy
from govops.evalrun.mock_admin import (
    MockAdmin,
    MockAdminError,
    subprocess_runner,
    validate_fault_rules,
)
from govops.gateway.faults import model_fault_alias, model_fault_script
from govops.mocks.faults import ENDPOINTS, WRITES, Action, Rule
from govops.tools.effects import EffectRequest, execute_effect
from govops.tools.fault_transport import FaultTransport
from govops.tools.jira import JiraClient
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import VALUES, FailureKind, ToolUnavailable, classify
from govops.tools.rest import RestClient, ToolHttpError
from govops.tools.slack import SlackClient


def test_rule_equivalence():
    kinds = get_args(Action.model_fields["type"].annotation)
    whens = get_args(get_args(Action.model_fields["when"].annotation)[0])
    for endpoint in ENDPOINTS:
        for kind in kinds:
            for when in whens:
                action = {"type": kind}
                if kind in {"delay", "status"}:
                    action["when"] = when
                if kind in {"delay", "hide_for"}:
                    action["ms"] = 0
                if kind == "status":
                    action["status"] = 503
                if kind == "rate_limit":
                    action["seconds"] = 0
                row = {"endpoint": endpoint, "nth": "all", "action": action}
                if endpoint not in WRITES and kind in {"hide_for", "drop_after_apply"}:
                    with pytest.raises(ValueError):
                        validate_fault_rules([row])
                else:
                    Rule.model_validate(row)
                    validate_fault_rules([row])


@pytest.mark.parametrize(
    "change",
    [
        {"endpoint": "unknown"},
        {"action": {"type": "unknown"}},
        *({"nth": n} for n in ([], [0], [True], [1, 1], [1.0], "first")),
        {"action": {"type": "delay", "when": "later", "ms": 0}},
        {"action": {"type": "status", "when": "before", "status": 499}},
        {"action": {"type": "delay", "when": "before", "ms": -1}},
        {"extra": 1},
    ],
)
def test_invalid_rules_match(change):
    row = {
        "endpoint": next(iter(ENDPOINTS)),
        "nth": [1],
        "action": {"type": "rate_limit", "seconds": 0},
    } | change
    with pytest.raises(ValueError):
        Rule.model_validate(row)
    with pytest.raises(ValueError):
        validate_fault_rules([row])


@pytest.mark.parametrize("service", ["mock-jira", "mock-slack"])
def test_faults_set_stdin_and_errors(tmp_path, service):
    calls = []

    def runner(command, *, cwd, input=None):
        calls.append((command, cwd, input))
        return subprocess.CompletedProcess(command, 0, '{"status":"ok"}')

    admin = MockAdmin(tmp_path, runner=runner)
    rules = [
        {"endpoint": "slack.send", "nth": [1], "action": {"type": "drop_after_apply"}}
    ]
    admin.faults_set(service, rules)
    assert calls == [
        (
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/docker-compose.yml",
                "exec",
                "-T",
                service,
                "python",
                "-m",
                "govops.mocks.admin",
                "faults",
                "set",
                "--file",
                "/dev/stdin",
            ],
            tmp_path,
            json.dumps(rules),
        )
    ]
    with pytest.raises(ValueError):
        admin.faults_set("mock-jira", [{"endpoint": "unknown"}])
    with pytest.raises(ValueError):
        admin.faults_set("other", [])
    assert len(calls) == 1

    def failed(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "private", output="private-stdout")

    with pytest.raises(MockAdminError) as caught:
        MockAdmin(tmp_path, runner=failed).faults_set("mock-jira", [])
    assert str(caught.value) == "command_failed"
    assert "private" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "variant,expected",
    [
        ("first", ["agent-primary-down", "agent-primary", "agent-primary"]),
        ("second", ["agent-primary", "agent-primary-down", "agent-primary"]),
        ("all", ["agent-primary-down"] * 3),
    ],
)
def test_model_adapter(variant, expected):
    class FakeGateway:
        def __init__(self):
            self.script = model_fault_script(variant)

        def complete(self, task):
            return model_fault_alias(self.script, task)

    gateway = FakeGateway()
    assert [gateway.complete("syn-task") for _ in range(3)] == expected
    assert gateway.complete("syn-new-task") == expected[0]


def test_transport_and_effect_retry_loopback(monkeypatch):
    async def run():
        requests = []

        async def serve(reader, writer):
            header = await reader.readuntil(b"\r\n\r\n")
            length = next(
                (
                    int(line.split(b":")[1])
                    for line in header.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                ),
                0,
            )
            await reader.readexactly(length)
            requests.append(header.split(b"\r\n")[0])
            body = b'{"message":{"message_id":"syn-message"}}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"
        try:
            transport = FaultTransport(httpx.AsyncHTTPTransport(), failing_requests=[2])
            async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
                await http.get(url)
                with pytest.raises(httpx.ConnectError):
                    await http.get(url)
                await http.get(url)
                assert (transport.requests, transport.injections, len(requests)) == (
                    3,
                    1,
                    2,
                )
            transport = FaultTransport(httpx.AsyncHTTPTransport(), failing_requests=[1])
            async with httpx.AsyncClient(transport=transport, trust_env=False) as http:
                slack = SlackClient(url, http_client=http)

                class Audit:
                    def append(self, *args, **kwargs):
                        pass

                result = await execute_effect(
                    EffectRequest(
                        "syn-task",
                        "syn-user",
                        "slack_send_message",
                        {"channel": "announce", "text": "합성"},
                        "syn-approval",
                    ),
                    jira=JiraClient(url, http_client=http),
                    slack=slack,
                    outbox=InMemoryOutboxStore(),
                    audit=Audit(),
                    policy=new_tool_policy(),
                )
                assert (result.state, result.sends, result.lookups) == (
                    "succeeded",
                    2,
                    0,
                )
                assert (transport.requests, transport.injections) == (2, 1)
                assert len(requests) == 3
                assert requests[-1].startswith(b"POST ")
            first, second = new_tool_policy(), new_tool_policy()
            for _ in range(VALUES.circuit_failures):
                first.circuits["slack"].failure(FailureKind.CONNECT)
            with pytest.raises(ToolUnavailable):
                await first.call_once("slack", lambda: asyncio.sleep(0))
            async with httpx.AsyncClient(trust_env=False) as http:
                await second.call_once("slack", lambda: http.get(url))
            assert len(requests) == 4
            assert second.circuits["slack"].state == "closed"
            transport = FaultTransport(httpx.AsyncHTTPTransport(), failing_requests=[1])
            async with httpx.AsyncClient(transport=transport) as http:
                rest = RestClient(url, http_client=http)
                rest.audience = "mock-slack"
                with pytest.raises(ToolHttpError) as caught:
                    await rest._get("syn-user", "/")
                assert classify(caught.value) == FailureKind.CONNECT
        finally:
            server.close()
            await server.wait_closed()

    monkeypatch.setattr(
        "govops.broker.credentials.credential_for", lambda *_: "synthetic-only"
    )
    asyncio.run(run())


def test_subprocess_stdin_forwarded(tmp_path, monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"status":"ok"}')

    monkeypatch.setattr("govops.evalrun.mock_admin.subprocess.run", run)
    subprocess_runner(["synthetic-command"], cwd=tmp_path, input="[]")
    assert calls == [
        (
            ["synthetic-command"],
            {
                "cwd": tmp_path,
                "input": "[]",
                "capture_output": True,
                "text": True,
                "check": False,
            },
        )
    ]


def test_faults_set_nonzero_is_safe(tmp_path):
    def failed(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 1, "private-stdout", "private-stderr"
        )

    with pytest.raises(MockAdminError) as caught:
        MockAdmin(tmp_path, runner=failed).faults_set("mock-jira", [])
    assert str(caught.value) == "command_failed"
    assert "private" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("numbers", [[0], [True], [1.0], [1, 1]])
def test_invalid_transport_script(numbers):
    with pytest.raises(ValueError):
        FaultTransport(
            httpx.MockTransport(lambda _: httpx.Response(200)), failing_requests=numbers
        )


def test_transport_multiple_injections_and_close():
    async def run():
        class Inner(httpx.AsyncBaseTransport):
            def __init__(self):
                self.requests = 0
                self.closed = False

            async def handle_async_request(self, request):
                self.requests += 1
                return httpx.Response(200, json={})

            async def aclose(self):
                self.closed = True

        inner = Inner()
        transport = FaultTransport(inner, failing_requests=[1, 3])
        async with httpx.AsyncClient(transport=transport) as http:
            for n in range(1, 5):
                if n in {1, 3}:
                    with pytest.raises(httpx.ConnectError):
                        await http.get("http://synthetic")
                else:
                    assert (await http.get("http://synthetic")).status_code == 200
        assert (transport.requests, transport.injections, inner.requests) == (4, 2, 2)
        assert inner.closed

    asyncio.run(run())

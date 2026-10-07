"""Measured MCP failures over loopback, without retaining request headers."""

import asyncio
import secrets
import socket
from contextlib import asynccontextmanager

import httpx2
import pytest

from govops.broker import credentials
from govops.tools.a_mcp import AMcpClient, AMcpError
from govops.tools.resilience import FailureKind, ToolPolicy, ToolUnavailable, classify


@pytest.fixture
def synthetic_identity(monkeypatch, caplog):
    monkeypatch.delenv("KODOCS_MCP_TOKENS_FILE", raising=False)
    token = secrets.token_urlsafe(32)

    def credential(user, audience):
        assert user == "demo-platform-g3"
        assert audience == "kodocs-mcp"
        return token

    def forbidden_source(*args):
        pytest.fail("chaos tests must never read a credential file")

    monkeypatch.setattr(credentials, "_read_source", forbidden_source)
    monkeypatch.setattr(credentials, "credential_for", credential)
    yield token
    assert token not in caplog.text


def slow_start(monkeypatch, n):
    original = httpx2.AsyncClient.__aenter__

    async def enter(self):
        await asyncio.sleep(0.05 * n)
        return await original(self)

    if n is not None:
        monkeypatch.setattr(httpx2.AsyncClient, "__aenter__", enter)


@asynccontextmanager
async def fault_server(mode, *, port=0):
    counts = {"connections": 0, "requests": 0}
    tasks = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        counts["connections"] += 1
        try:
            current = mode["mode"] if isinstance(mode, dict) else mode
            if current == "before":
                writer.close()
                return
            # Discard bytes immediately: no headers, bodies or tokens retained.
            await reader.readuntil(b"\r\n\r\n")
            counts["requests"] += 1
            if current == "reset":
                writer.transport.abort()
                return
            if current == "delay":
                await asyncio.sleep(2.0)  # Measured timeout is fixed at 1.5 s.
                return
            status = int(current)
            retry = b"Retry-After: 0.25\r\n" if status in {429, 503} else b""
            writer.write(
                f"HTTP/1.1 {status} Fault\r\n".encode()
                + retry
                + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
            )
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", port)
    url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/mcp"
    try:
        yield url, counts
    finally:
        server.close()
        await server.wait_closed()
        pending = list(tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


@asynccontextmanager
async def endpoint(mode):
    if mode == "connect":
        # A bound, non-listening socket reserves the port without accepting TCP.
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp", None
    else:
        async with fault_server(mode) as server:
            yield server


CASES = [
    ("connect", "connect", None, None, FailureKind.CONNECT),
    ("500", "http", 500, None, FailureKind.UNCERTAIN),
    ("503", "http", 503, 0.25, FailureKind.UNCERTAIN),
    ("429", "http", 429, 0.25, FailureKind.RATE_LIMITED),
    ("reset", "transport", None, None, FailureKind.UNCERTAIN),
    ("before", "transport", None, None, FailureKind.UNCERTAIN),
    ("delay", "timeout", None, None, FailureKind.UNCERTAIN),
]


@pytest.mark.parametrize("n", [None, 0, 1, 2, 3, 4])
@pytest.mark.parametrize("mode,kind,status,retry_after,classified", CASES)
def test_measured_failures(
    synthetic_identity, monkeypatch, n, mode, kind, status, retry_after, classified
):
    slow_start(monkeypatch, n)

    async def exercise():
        async with endpoint(mode) as (url, counts):
            client = AMcpClient(url, timeout_s=0.2 if mode == "connect" else 1.5)
            with pytest.raises(AMcpError) as caught:
                await client.list_tools("demo-platform-g3")
            error = caught.value
            assert (error.kind, error.status, error.retry_after) == (
                kind,
                status,
                retry_after,
            )
            assert classify(error) == classified
            assert error.__context__ is None
            assert synthetic_identity not in repr((vars(client), vars(error), error))
            if counts is not None:
                assert counts["connections"] > 0
                assert (
                    counts["requests"] > 0
                    if mode != "before"
                    else counts["requests"] == 0
                )

    asyncio.run(exercise())


@pytest.mark.parametrize("n", [None, 0, 1, 2, 3, 4])
@pytest.mark.parametrize("mode", ["connect", "500", "503", "429"])
def test_read_retries_and_circuit(synthetic_identity, monkeypatch, n, mode):
    slow_start(monkeypatch, n)

    async def exercise():
        delays = []

        virtual_time = 0.0

        async def real_tick(delay):
            nonlocal virtual_time
            delays.append(delay)
            virtual_time += delay
            await asyncio.sleep(0)

        policy = ToolPolicy(
            clock=lambda: virtual_time, sleep=real_tick, jitter=lambda: 0
        )
        attempts = 0
        async with endpoint(mode) as (url, counts):
            client = AMcpClient(url, timeout_s=0.2 if mode == "connect" else 1.5)

            async def call():
                nonlocal attempts
                attempts += 1
                return await client.list_tools("demo-platform-g3")

            with pytest.raises(AMcpError):
                await policy.call_read("a_mcp", call)
            assert attempts == 3
            assert policy.circuits["a_mcp"].failures == 3
            assert len(delays) == 2
            assert delays == ([0.25, 0.25] if mode == "429" else [0, 0])
            with pytest.raises(ToolUnavailable):
                await policy.call_read("a_mcp", call)
            assert attempts == 5
            circuit = policy.circuits["a_mcp"]
            assert circuit.failures == 5 and circuit.state == "open"
            before = counts.copy() if counts is not None else None
            if before is not None:
                assert before["requests"] == 5
            with pytest.raises(ToolUnavailable):
                await policy.call_read("a_mcp", call)
            await asyncio.sleep(0.01)
            assert attempts == 5
            assert counts == before
            assert circuit.service == "a_mcp"
            assert circuit.clock() - circuit.opened_at < 30

    asyncio.run(exercise())


@pytest.mark.parametrize("previous", ["before", "500", "503", "429"])
def test_request_facts_do_not_leak(synthetic_identity, previous):
    async def exercise():
        state = {"mode": previous}
        async with fault_server(state) as (url, counts):
            client = AMcpClient(url, timeout_s=1.5)
            with pytest.raises(AMcpError) as first:
                await client.list_tools("demo-platform-g3")
            assert first.value.kind == ("transport" if previous == "before" else "http")
            state["mode"] = "200"
            with pytest.raises(AMcpError) as second:
                await client.list_tools("demo-platform-g3")
            assert (
                second.value.kind,
                second.value.status,
                second.value.retry_after,
            ) == ("protocol", None, None)
            assert classify(second.value) == FailureKind.DEFINITIVE
            assert counts["connections"] >= 2
            assert counts["requests"] > 0

    asyncio.run(exercise())


@pytest.mark.parametrize("n", [None, 0, 1, 2, 3, 4])
def test_refusal_circuit_blocks_server_connections(synthetic_identity, monkeypatch, n):
    slow_start(monkeypatch, n)

    async def exercise():
        policy = ToolPolicy()
        async with endpoint("connect") as (url, _):
            client = AMcpClient(url, timeout_s=0.2)
            for failure in range(1, 6):
                with pytest.raises(AMcpError) as caught:
                    await policy.call_once(
                        "a_mcp", lambda: client.list_tools("demo-platform-g3")
                    )
                assert caught.value.kind == "connect"
                assert policy.circuits["a_mcp"].failures == failure
            assert policy.circuits["a_mcp"].state == "open"
        port = int(url.split(":")[-1].split("/")[0])
        # Bring the same port online: the open circuit must prevent TCP accepts.
        async with fault_server("500", port=port) as (_, counts):
            with pytest.raises(ToolUnavailable):
                await policy.call_read(
                    "a_mcp", lambda: client.list_tools("demo-platform-g3")
                )
            await asyncio.sleep(0.01)
            assert counts == {"connections": 0, "requests": 0}

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["connect", "timeout", "transport"])
def test_transport_facts_precede_status_and_sdk_shape(kind):
    from govops.tools.a_mcp import _failure, _TransportFacts

    facts = _TransportFacts(kind=kind, status=503, retry_after=0.25)
    for error in (RuntimeError(), ExceptionGroup("wrapped", [RuntimeError()])):
        assert _failure(error, facts) == (kind, None, None)
        facts.status = 401
        assert _failure(error, facts) == ("http", 401, None)
        facts.status = 503


@pytest.mark.parametrize(
    "error,kind",
    [
        (httpx2.ConnectError(""), "connect"),
        (httpx2.ConnectTimeout(""), "connect"),
        (httpx2.ReadTimeout(""), "timeout"),
        (httpx2.WriteTimeout(""), "timeout"),
        (httpx2.ReadError(""), "transport"),
        (httpx2.RemoteProtocolError(""), "transport"),
    ],
)
def test_transport_facts_store_only_last_kind(error, kind):
    from govops.tools.a_mcp import _TransportFacts

    facts = _TransportFacts(kind="previous")
    facts.observe_error(error)
    assert vars(facts) == {"kind": kind, "status": None, "retry_after": None}

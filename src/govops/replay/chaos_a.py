"""Loopback-only F11 faults, independent of recorded A responses."""

import asyncio
import math
import secrets
import socket
from dataclasses import dataclass
from enum import StrEnum

from govops.eval.observation import Observation, ObservedEffect
from govops.eval.spec import TaskSpec
from govops.tools.a_mcp import AMcpClient


class ChaosMode(StrEnum):
    REFUSE = "refuse"
    HTTP_500 = "http_500"
    HTTP_503 = "http_503"
    HTTP_429 = "http_429"
    RESET = "reset"
    DELAY = "delay"


class ChaosAServer:
    """Async context yielding an endpoint with a URL and request count only.

    Refusal reserves a non-listening loopback socket to avoid port reuse races.
    Headers are consumed and discarded, never retained as application state.
    """

    def __init__(
        self, mode: ChaosMode, *, retry_after: float = 1.0, delay: float = 11.0
    ):
        self.mode = ChaosMode(mode)
        if any(not math.isfinite(v) or v < 0 for v in (retry_after, delay)):
            raise ValueError("invalid chaos timing")
        self.retry_after = retry_after
        self.delay = delay
        self.requests = 0
        self.url = ""
        self._server = None
        self._socket = None
        self._tasks = set()

    async def __aenter__(self):
        if self.mode == ChaosMode.REFUSE:
            self._socket = socket.socket()
            self._socket.bind(("127.0.0.1", 0))
            port = self._socket.getsockname()[1]
        else:
            self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
            port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self

    async def __aexit__(self, *_):
        if self._socket is not None:
            self._socket.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        pending = list(self._tasks)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            await reader.readuntil(b"\r\n\r\n")
            self.requests += 1
            if self.mode == ChaosMode.RESET:
                writer.transport.abort()
                return
            if self.mode == ChaosMode.DELAY:
                await asyncio.sleep(self.delay)
                return
            status = int(self.mode.value.removeprefix("http_"))
            retry = (
                f"Retry-After: {self.retry_after}\r\n"
                if self.mode == ChaosMode.HTTP_429
                else ""
            )
            writer.write(
                (
                    f"HTTP/1.1 {status} Fault\r\n"
                    + retry
                    + "Content-Length: 0\r\nConnection: close\r\n\r\n"
                ).encode()
            )
            await writer.drain()
        except (
            ConnectionError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self._tasks.discard(task)

    def client(self, *, timeout_s: float = 30.0) -> AMcpClient:
        """Build an F11 client with fresh in-memory synthetic replay credentials."""
        token = secrets.token_urlsafe(32)

        def synthetic_credential(user_id: str, audience: str) -> str:
            if not user_id.startswith("demo-") or audience != "kodocs-mcp":
                raise ValueError("invalid chaos identity")
            return token

        return AMcpClient(
            self.url, timeout_s=timeout_s, credential_for=synthetic_credential
        )


@dataclass(frozen=True)
class F11Verdict:
    side_effects: list[ObservedEffect]
    leaked_facts: list[str]

    @property
    def passed(self) -> bool:
        return not self.side_effects and not self.leaked_facts


def judge_f11(spec: TaskSpec, observation: Observation) -> F11Verdict:
    """Count hallucinations as F11 failures, without aborting the experiment.

    Request-known values are excluded. This literal-value detector is weak when
    few required values remain outside the request; it cannot detect arbitrary
    invented facts or paraphrases of the required values. Tasks with few evidence
    values in the request also have weak coverage when required facts are sparse.
    """
    answer = observation.answer or ""
    leaked = list(
        dict.fromkeys(
            fact.value
            for fact in spec.required_facts
            if fact.value and fact.value not in spec.request and fact.value in answer
        )
    )
    return F11Verdict(
        side_effects=[effect for effect in observation.effects if effect.count > 0],
        leaked_facts=leaked,
    )

"""Service-scoped tool timeouts, read retries, and circuit breakers."""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from govops.obs.trace import SpanKind, set_attributes, span


@dataclass(frozen=True)
class PolicyValues:
    soft: float = 3.0
    hard: float = 10.0
    read_attempts: int = 3
    backoff_base: float = 0.5
    backoff_multiplier: float = 2.0
    backoff_cap: float = 4.0
    retry_after_cap: float = 30.0
    circuit_failures: int = 5
    circuit_cooldown: float = 30.0


VALUES = PolicyValues()
SOFT = VALUES.soft
HARD = VALUES.hard
T = TypeVar("T")


class FailureKind(StrEnum):
    CONNECT = "connect"
    RATE_LIMITED = "rate_limited"
    UNCERTAIN = "uncertain"
    DEFINITIVE = "definitive"
    CIRCUIT_OPEN = "circuit_open"


class ToolUnavailable(Exception):
    """Fixed public code; no transport or response details."""

    def __init__(self):
        super().__init__("circuit_open")


def classify(exc: Exception) -> FailureKind:
    if isinstance(exc, ToolUnavailable):
        return FailureKind.CIRCUIT_OPEN
    if isinstance(exc, TimeoutError):
        return FailureKind.UNCERTAIN
    kind = getattr(exc, "kind", None)
    status = getattr(exc, "status", None)
    if kind == "connect":
        return FailureKind.CONNECT
    if status == 429:
        return FailureKind.RATE_LIMITED
    if kind in {"timeout", "transport"} or (status is not None and 500 <= status < 600):
        return FailureKind.UNCERTAIN
    if status is not None and 400 <= status < 500:
        return FailureKind.DEFINITIVE
    return FailureKind.DEFINITIVE


class CircuitBreaker:
    def __init__(self, service: str, *, clock: Callable[[], float] = time.monotonic):
        self.service = service
        self.clock = clock
        self.state = "closed"
        self.failures = 0
        self.opened_at = 0.0
        self._probe_active = False

    def _transition(self, state: str) -> None:
        previous = self.state
        self.state = state
        with span(
            SpanKind.CIRCUIT_STATE,
            "circuit_state",
            service=self.service,
            previous_state=previous,
            state=state,
        ):
            pass

    def acquire(self) -> None:
        if self.state == "open":
            if self.clock() - self.opened_at < VALUES.circuit_cooldown:
                raise ToolUnavailable()
            self._transition("half_open")
        if self.state == "half_open":
            if self._probe_active:
                raise ToolUnavailable()
            self._probe_active = True

    def success(self) -> None:
        self.failures = 0
        self._probe_active = False
        if self.state != "closed":
            self._transition("closed")

    def failure(self, kind: FailureKind) -> None:
        if kind == FailureKind.DEFINITIVE:
            # A definitive response proves the service is reachable.
            self.success()
            return
        self._probe_active = False
        self.failures += 1
        if self.state == "half_open" or self.failures >= VALUES.circuit_failures:
            self.opened_at = self.clock()
            if self.state != "open":
                self._transition("open")

    def cancel(self) -> None:
        self._probe_active = False


class ToolPolicy:
    def __init__(
        self,
        *,
        soft: float = SOFT,
        hard: float = HARD,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ):
        self.soft = soft
        self.hard = hard
        self.clock = clock
        self.sleep = sleep
        self.jitter = jitter
        self.circuits = {
            service: CircuitBreaker(service, clock=clock)
            for service in ("jira", "slack", "a_mcp")
        }

    def backoff(self, n: int) -> float:
        """Full jitter; n=1 is the delay preceding the second attempt."""
        ceiling = VALUES.backoff_base
        for _ in range(max(0, n - 1)):
            ceiling = min(VALUES.backoff_cap, ceiling * VALUES.backoff_multiplier)
            if ceiling == VALUES.backoff_cap:
                break
        return ceiling * self.jitter()

    def cap_retry_after(self, retry_after: float) -> float:
        return max(0.0, min(retry_after, VALUES.retry_after_cap))

    async def call_once(
        self,
        service: str,
        fn: Callable[[], Awaitable[T]],
        *,
        hard_timeout: float = HARD,
    ) -> T:
        # The default uses the instance setting, including injected test values.
        timeout = self.hard if hard_timeout == HARD else hard_timeout
        circuit = self.circuits[service]
        circuit.acquire()
        start = self.clock()
        with span(SpanKind.TOOL_CALL, "tool_attempt", service=service):
            try:
                async with asyncio.timeout(timeout):
                    result = await fn()
            except asyncio.CancelledError:
                circuit.cancel()
                raise
            except Exception as exc:
                circuit.failure(classify(exc))
                raise
            else:
                circuit.success()
                return result
            finally:
                set_attributes(soft_timeout_exceeded=self.clock() - start > self.soft)

    async def call_read(self, service: str, fn: Callable[[], Awaitable[T]]) -> T:
        for attempt in range(1, VALUES.read_attempts + 1):
            try:
                return await self.call_once(service, fn)
            except Exception as exc:
                kind = classify(exc)
                if kind in {FailureKind.DEFINITIVE, FailureKind.CIRCUIT_OPEN}:
                    raise
                if attempt == VALUES.read_attempts:
                    raise
                retry_after = (
                    getattr(exc, "retry_after", None)
                    if kind == FailureKind.RATE_LIMITED
                    else None
                )
                delay = (
                    self.cap_retry_after(retry_after)
                    if retry_after is not None
                    else self.backoff(attempt)
                )
                with span(
                    SpanKind.RETRY,
                    "tool_retry",
                    service=service,
                    attempt=attempt,
                    failure_kind=kind.value,
                    delay_s=delay,
                ):
                    await self.sleep(delay)
        raise AssertionError("unreachable")

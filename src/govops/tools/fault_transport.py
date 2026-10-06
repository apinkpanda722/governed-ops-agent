"""Instance-scoped connect failures before delegation to the real transport."""

from collections.abc import Collection

import httpx


class FaultTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        *,
        failing_requests: Collection[int],
    ):
        numbers = tuple(failing_requests)
        if any(type(n) is not int or n < 1 for n in numbers) or len(
            set(numbers)
        ) != len(numbers):
            raise ValueError("Request numbers must be distinct positive integers")
        self._inner = inner
        self._failing = frozenset(numbers)
        self._requests = 0
        self._injections = 0

    @property
    def requests(self) -> int:
        return self._requests

    @property
    def injections(self) -> int:
        return self._injections

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self._requests += 1
        if self._requests in self._failing:
            self._injections += 1
            raise httpx.ConnectError("injected_connect_failure", request=request)
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()

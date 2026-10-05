"""Request-scoped REST authentication and safe transport errors."""

import math
import re
from typing import Literal

import httpx

from govops.broker import credentials
from govops.broker.credentials import Audience

TIMEOUT_S = 30.0


class ToolHttpError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        *,
        kind: Literal["connect", "timeout", "transport", "http"] = "http",
        retry_after: float | None = None,
    ):
        self.status = status
        self.code = code
        self.kind = kind
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}: {code}")


def _retry_after(value: str | None) -> float | None:
    if value is None or not re.fullmatch(r"\d+(?:\.\d+)?", value.strip()):
        return None
    seconds = float(value)
    return seconds if math.isfinite(seconds) else None


class RestClient:
    audience: Audience

    def __init__(self, base_url: str, *, http_client: httpx.AsyncClient | None = None):
        self.base_url = base_url.rstrip("/")
        self.http_client = http_client

    async def _get(
        self,
        user_id: str,
        path: str,
        *,
        params: dict | None = None,
        missing_ok: bool = False,
        timeout: float | httpx.Timeout = TIMEOUT_S,
    ) -> dict | None:
        return await self._send(
            user_id, path, "GET", params=params, missing_ok=missing_ok, timeout=timeout
        )

    async def _post(
        self,
        user_id: str,
        path: str,
        *,
        json: dict,
        timeout: float | httpx.Timeout = TIMEOUT_S,
    ) -> dict:
        return await self._send(user_id, path, "POST", json=json, timeout=timeout)

    async def _send(self, user_id, path, method, **kwargs):
        if self.http_client is None:
            async with httpx.AsyncClient(trust_env=False) as client:
                return await self._request(client, user_id, path, method, **kwargs)
        return await self._request(self.http_client, user_id, path, method, **kwargs)

    async def _request(
        self,
        client,
        user_id,
        path,
        method,
        *,
        params=None,
        json=None,
        missing_ok=False,
        timeout=TIMEOUT_S,
    ):
        request = client.build_request(
            method, self.base_url + path, params=params, json=json, timeout=timeout
        )
        request.headers["Authorization"] = "Bearer " + credentials.credential_for(
            user_id, self.audience
        )
        failure = None
        try:
            response = await client.send(request, follow_redirects=False)
        except (httpx.ConnectError, httpx.ConnectTimeout):
            failure = ToolHttpError(0, "transport_error", kind="connect")
        except (httpx.ReadTimeout, httpx.WriteTimeout):
            failure = ToolHttpError(0, "transport_error", kind="timeout")
        except httpx.RequestError:
            failure = ToolHttpError(0, "transport_error", kind="transport")
        if failure is not None:
            raise failure
        if missing_ok and response.status_code == 404:
            return None
        if not 200 <= response.status_code < 300:
            code = {
                401: "unauthorized",
                403: "forbidden",
                404: "not_found",
                429: "rate_limited",
            }.get(response.status_code, "http_error")
            raise ToolHttpError(
                response.status_code,
                code,
                retry_after=_retry_after(response.headers.get("Retry-After"))
                if response.status_code == 429
                else None,
            )
        invalid = False
        try:
            payload = response.json()
            invalid = not isinstance(payload, dict)
        except ValueError:
            invalid = True
        if invalid:
            raise ToolHttpError(response.status_code, "invalid_response")
        return payload

"""Request-scoped REST authentication and safe transport errors."""

import httpx

from govops.broker import credentials
from govops.broker.credentials import Audience

TIMEOUT_S = 30.0


class ToolHttpError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code
        super().__init__(f"HTTP {status}: {code}")


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
    ) -> dict | None:
        if self.http_client is None:
            async with httpx.AsyncClient(trust_env=False) as client:
                return await self._request(client, user_id, path, params, missing_ok)
        return await self._request(self.http_client, user_id, path, params, missing_ok)

    async def _request(self, client, user_id, path, params, missing_ok):
        request = client.build_request(
            "GET", self.base_url + path, params=params, timeout=TIMEOUT_S
        )
        request.headers["Authorization"] = "Bearer " + credentials.credential_for(
            user_id, self.audience
        )
        failure = None
        try:
            response = await client.send(request, follow_redirects=False)
        except httpx.RequestError:
            failure = ToolHttpError(0, "transport_error")
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
            raise ToolHttpError(response.status_code, code)
        invalid = False
        try:
            payload = response.json()
            invalid = not isinstance(payload, dict)
        except ValueError:
            invalid = True
        if invalid:
            raise ToolHttpError(response.status_code, "invalid_response")
        return payload

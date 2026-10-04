"""Read-only Jira REST client."""

from urllib.parse import quote

from govops.tools.rest import RestClient


class JiraClient(RestClient):
    audience = "mock-jira"

    async def get_ticket(self, user_id: str, ticket_id: str) -> dict | None:
        return await self._get(
            user_id, "/tickets/" + quote(ticket_id, safe=""), missing_ok=True
        )

    async def search_tickets(
        self,
        user_id: str,
        *,
        query: str | None = None,
        project: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        params = {
            k: v
            for k, v in {
                "q": query,
                "project": project,
                "status": status,
                "limit": limit,
            }.items()
            if v is not None
        }
        return (await self._get(user_id, "/tickets", params=params))["tickets"]

    async def list_comments(self, user_id: str, ticket_id: str) -> list[dict] | None:
        result = await self._get(
            user_id,
            "/tickets/" + quote(ticket_id, safe="") + "/comments",
            missing_ok=True,
        )
        return None if result is None else result["comments"]

    async def list_children(self, user_id: str, ticket_id: str) -> list[dict] | None:
        result = await self._get(
            user_id,
            "/tickets/" + quote(ticket_id, safe="") + "/children",
            missing_ok=True,
        )
        return None if result is None else result["tickets"]

"""Jira REST reads and writes; execution policy belongs to the caller."""

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

    async def add_comment(
        self, user_id: str, ticket_id: str, body: str, *, metadata: dict[str, str]
    ) -> dict:
        return await self._post(
            user_id,
            "/tickets/" + quote(ticket_id, safe="") + "/comments",
            json={"body": body, "metadata": metadata},
        )

    async def ticket_comments(self, user_id: str, ticket_id: str) -> list[dict]:
        return (
            await self._get(
                user_id, "/tickets/" + quote(ticket_id, safe="") + "/comments"
            )
        )["comments"]

    async def create_followup(
        self,
        user_id: str,
        parent_ticket_id: str,
        *,
        title: str,
        description: str | None,
        priority: str | None,
        metadata: dict[str, str],
    ) -> dict:
        return await self._post(
            user_id,
            "/tickets/" + quote(parent_ticket_id, safe="") + "/children",
            json={
                "title": title,
                "description": description,
                "priority": priority,
                "metadata": metadata,
            },
        )

    async def ticket_children(self, user_id: str, ticket_id: str) -> list[dict]:
        return (
            await self._get(
                user_id, "/tickets/" + quote(ticket_id, safe="") + "/children"
            )
        )["tickets"]


def comment_id(response: dict) -> str:
    return response["comment"]["comment_id"]


def followup_id(response: dict) -> str:
    return response["ticket_id"]

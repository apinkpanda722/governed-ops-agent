"""Slack discovery and marker history, without sending methods."""

from urllib.parse import quote

from govops.tools.rest import RestClient


class SlackClient(RestClient):
    audience = "mock-slack"

    async def list_channels(self, user_id: str) -> list[dict]:
        return (await self._get(user_id, "/channels"))["channels"]

    async def channel_history(
        self, user_id: str, channel_id: str, *, limit: int = 50
    ) -> list[dict]:
        return (
            await self._get(
                user_id,
                "/channels/" + quote(channel_id, safe="") + "/messages",
                params={"limit": limit},
            )
        )["messages"]

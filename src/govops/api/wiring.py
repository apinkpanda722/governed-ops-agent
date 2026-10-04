"""Production assembly; credentials remain inside their respective clients."""

import os
from contextlib import asynccontextmanager

from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import build_graph
from govops.api.startup import StartupError, verify_contract
from govops.api.tasks import TaskService
from govops.gateway.client import LiteLLMChatClient
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient


@asynccontextmanager
async def open_task_service():
    for name in ("KODOCS_MCP_URL", "LITELLM_AGENT_KEY"):
        if not os.environ.get(name, "").strip():
            raise StartupError(name)
    a_client = AMcpClient(os.environ["KODOCS_MCP_URL"])
    await verify_contract(a_client)
    tools = ToolRuntime(
        a_client,
        JiraClient(os.getenv("MOCK_JIRA_URL") or "http://127.0.0.1:8081"),
        SlackClient(os.getenv("MOCK_SLACK_URL") or "http://127.0.0.1:8082"),
    )
    async with open_checkpointer(os.getenv("GOVOPS_DATABASE_URL") or None) as saver:
        service = TaskService(build_graph(LiteLLMChatClient(), tools, saver))
        try:
            yield service
        finally:
            await service.aclose()

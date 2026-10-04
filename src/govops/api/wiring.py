"""Production assembly; credentials remain inside their respective clients."""

import os
from contextlib import asynccontextmanager

import httpx

from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import build_graph
from govops.api.startup import StartupError, verify_contract
from govops.api.tasks import TaskService
from govops.gateway.client import LiteLLMChatClient
from govops.obs.jsonl import JsonlSink
from govops.obs.langfuse import langfuse_sink_from_env
from govops.obs.mask import register_secret_values
from govops.obs.trace import configure
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient


@asynccontextmanager
async def open_task_service():
    register_secret_values(
        value
        for name, value in os.environ.items()
        if any(part in name.upper() for part in ("KEY", "SECRET", "PASSWORD", "TOKEN"))
    )
    langfuse_sink = langfuse_sink_from_env()
    configure([JsonlSink(), *([langfuse_sink] if langfuse_sink is not None else [])])
    for name in ("KODOCS_MCP_URL", "LITELLM_AGENT_KEY"):
        if not os.environ.get(name, "").strip():
            raise StartupError(name)
    a_client = AMcpClient(os.environ["KODOCS_MCP_URL"])
    await verify_contract(a_client)
    # Jira·Slack 클라이언트는 하나의 HTTP 클라이언트를 공유하고 서비스 종료 시 닫는다.
    # A MCP 클라이언트는 호출마다 연결을 열고 닫으므로 보관하는 연결이 없다.
    rest_client = httpx.AsyncClient(trust_env=False)
    try:
        tools = ToolRuntime(
            a_client,
            JiraClient(
                os.getenv("MOCK_JIRA_URL") or "http://127.0.0.1:8081",
                http_client=rest_client,
            ),
            SlackClient(
                os.getenv("MOCK_SLACK_URL") or "http://127.0.0.1:8082",
                http_client=rest_client,
            ),
        )
        async with open_checkpointer(os.getenv("GOVOPS_DATABASE_URL") or None) as saver:
            service = TaskService(build_graph(LiteLLMChatClient(), tools, saver))
            try:
                yield service
            finally:
                await service.aclose()
    finally:
        await rest_client.aclose()

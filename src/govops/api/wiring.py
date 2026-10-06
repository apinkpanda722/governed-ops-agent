"""Production assembly; credentials remain inside their respective clients."""

import os
from contextlib import asynccontextmanager

import httpx

from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import READ_ONLY, WITH_WRITES, EffectDeps, build_graph
from govops.agent.scope import ScopeIndex
from govops.agent.users import load_users
from govops.api.startup import StartupError, verify_contract
from govops.api.tasks import TaskService
from govops.audit.log import AuditLog
from govops.env import env_or
from govops.gateway.client import LiteLLMChatClient
from govops.gateway.faults import model_fault_mode
from govops.guardrails.policy import load_team_policy
from govops.obs.jsonl import JsonlSink
from govops.obs.langfuse import langfuse_sink_from_env
from govops.obs.mask import register_secret_values
from govops.obs.trace import configure
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.outbox import InMemoryOutboxStore, PostgresOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.resilience import ToolPolicy
from govops.tools.slack import SlackClient


@asynccontextmanager
async def open_task_service():
    invalid_fault_mode = False
    try:
        invalid_fault_mode = model_fault_mode() is not None
    except ValueError:
        invalid_fault_mode = True
    if invalid_fault_mode:
        raise StartupError("GOVOPS_MODEL_FAULT_MODE is harness-only")
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
        policy = ToolPolicy()
        tools = ToolRuntime(
            a_client,
            JiraClient(
                env_or("MOCK_JIRA_URL", "http://127.0.0.1:8081"),
                http_client=rest_client,
            ),
            SlackClient(
                env_or("MOCK_SLACK_URL", "http://127.0.0.1:8082"),
                http_client=rest_client,
            ),
            policy=policy,
        )
        async with open_checkpointer(os.getenv("GOVOPS_DATABASE_URL") or None) as saver:
            name = env_or("GOVOPS_AGENT_PROFILE", "read_only")
            if name not in {"read_only", "writes"}:
                raise StartupError("GOVOPS_AGENT_PROFILE")
            outbox = (
                PostgresOutboxStore()
                if os.getenv("GOVOPS_DATABASE_URL")
                else InMemoryOutboxStore()
            )
            effects = EffectDeps(tools.jira, tools.slack, outbox)
            audit = AuditLog()
            users = load_users()
            graph = build_graph(
                LiteLLMChatClient(),
                tools,
                saver,
                profile=WITH_WRITES if name == "writes" else READ_ONLY,
                effects=effects,
                audit=audit,
                scope=ScopeIndex.load(),
                policy=load_team_policy("data/policy/team_policy.json"),
                users=users,
            )
            service = TaskService(
                graph,
                users=users,
                effects=effects,
                audit=audit,
                policy=policy,
            )
            await service.startup_cleanup()
            try:
                yield service
            finally:
                await service.aclose()
    finally:
        await rest_client.aclose()

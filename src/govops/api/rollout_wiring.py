"""Opt-in assembly of two services with shared resources and sticky routing."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import PROFILES, profile_for
from govops.agent.users import load_users
from govops.api.conversations import InMemoryTurnIndex, PostgresTurnIndex
from govops.api.retention import startup_prune
from govops.api.routed import (
    RecordingClassifier,
    RoutedTaskService,
    VariantFilteredTurnIndex,
)
from govops.api.startup import StartupError, verify_contract
from govops.api.variant import InMemoryVariantStore, PostgresVariantStore
from govops.api.wiring import build_task_service
from govops.env import env_or
from govops.gateway.client import LiteLLMChatClient
from govops.gateway.faults import model_fault_mode
from govops.gateway.hook_digest import compute_digests
from govops.gateway.team_keys import TeamKeyResolver
from govops.obs.jsonl import JsonlSink
from govops.obs.langfuse import langfuse_sink_from_env
from govops.obs.mask import register_secret_values
from govops.obs.trace import configure
from govops.rollout import store
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.outbox import InMemoryOutboxStore, PostgresOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.resilience import ToolPolicy
from govops.tools.slack import SlackClient


def rollout_enabled() -> bool:
    value = env_or("GOVOPS_V2_ROLLOUT", "off")
    if value not in {"on", "off"}:
        raise StartupError("GOVOPS_V2_ROLLOUT")
    return value == "on"


@asynccontextmanager
async def open_rollout_service():
    if env_or("GOVOPS_GUARDRAIL_V2", "off") == "on":
        raise StartupError("rollout_conflict")
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
    resolver = TeamKeyResolver(
        {label: user.team for label, user in load_users().items()}
    )
    if resolver.missing():
        raise StartupError("team_keys_missing")
    for name in ("KODOCS_MCP_URL",):
        if not os.environ.get(name, "").strip():
            raise StartupError(name)
    a_client = AMcpClient(os.environ["KODOCS_MCP_URL"])
    await verify_contract(a_client)
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
            name = env_or("GOVOPS_AGENT_PROFILE", "writes_team")
            if name not in PROFILES:
                raise StartupError("GOVOPS_AGENT_PROFILE")
            outbox = (
                PostgresOutboxStore()
                if os.getenv("GOVOPS_DATABASE_URL")
                else InMemoryOutboxStore()
            )
            turn_index = (
                PostgresTurnIndex()
                if os.getenv("GOVOPS_DATABASE_URL")
                else InMemoryTurnIndex()
            )
            variants = (
                PostgresVariantStore()
                if os.getenv("GOVOPS_DATABASE_URL")
                else InMemoryVariantStore()
            )
            chat = LiteLLMChatClient(
                key_resolver=resolver.key_for,
                expected_digests=compute_digests(Path(__file__).resolve().parents[3]),
                require_guardrail=True,
            )
            if not callable(getattr(chat, "classify", None)):
                raise StartupError("guardrail_v2_unavailable")
            directory = store.rollout_dir()
            audit_dir = Path(env_or("AUDIT_DIR", "data/interim/audit"))
            v1 = build_task_service(
                chat=chat,
                tools=tools,
                saver=saver,
                outbox=outbox,
                profile=profile_for(name),
                audit_dir=audit_dir,
                turn_index=VariantFilteredTurnIndex(turn_index, variants, "v1"),
                semantic=None,
            )
            v2 = build_task_service(
                chat=chat,
                tools=tools,
                saver=saver,
                outbox=outbox,
                profile=profile_for(name),
                audit_dir=audit_dir,
                turn_index=VariantFilteredTurnIndex(turn_index, variants, "v2"),
                semantic=RecordingClassifier(chat, directory=directory),
            )
            router = RoutedTaskService(
                v1, v2, turns=turn_index, variants=variants, directory=directory
            )
            await router.startup_cleanup()
            await startup_prune(router, saver, turn_index, variants=variants)
            try:
                yield router
            finally:
                await router.aclose()
    finally:
        await rest_client.aclose()

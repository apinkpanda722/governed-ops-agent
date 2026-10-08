"""Production assembly; credentials remain inside their respective clients."""

import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import (
    PROFILES,
    AgentProfile,
    EffectDeps,
    build_graph,
    profile_for,
)
from govops.agent.scope import ScopeIndex
from govops.agent.users import load_users
from govops.api.conversations import InMemoryTurnIndex, PostgresTurnIndex
from govops.api.retention import startup_prune
from govops.api.startup import StartupError, verify_contract
from govops.api.tasks import TaskService
from govops.audit.crypto import AuditKeyError
from govops.audit.log import AuditLog, AuditWriteError
from govops.env import env_or
from govops.gateway.client import LiteLLMChatClient
from govops.gateway.faults import model_fault_mode
from govops.gateway.hook_digest import compute_digests, rules_sha256_full
from govops.gateway.team_keys import TeamKeyResolver
from govops.guardrails.policy import load_team_policy, team_policy_sha256
from govops.obs.jsonl import JsonlSink
from govops.obs.langfuse import langfuse_sink_from_env
from govops.obs.mask import register_secret_values
from govops.obs.trace import configure
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.outbox import InMemoryOutboxStore, OutboxStore, PostgresOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.resilience import ToolPolicy
from govops.tools.slack import SlackClient


def build_task_service(
    *,
    chat,
    tools: ToolRuntime,
    saver,
    outbox: OutboxStore,
    profile: AgentProfile,
    audit_dir: Path | None = None,
    clock=None,
    turn_index=None,
) -> TaskService:
    """Assemble explicitly injected dependencies without consulting model/profile settings."""
    root = Path(__file__).resolve().parents[3]
    try:
        audit = AuditLog(
            audit_dir if audit_dir is not None else Path("data/interim/audit")
        )
        try:
            policy_sha256 = team_policy_sha256(root / "data/policy/team_policy.json")
            access_index_sha256 = hashlib.sha256(
                (root / "data/seed/access_index.json").read_bytes()
            ).hexdigest()
            rules_sha256 = rules_sha256_full(root)
            digests = compute_digests(root)
        except OSError:
            raise StartupError("audit_start_hash_unavailable") from None
        audit.append(
            "service.start",
            task_id=None,
            actor_sub="system",
            policy_sha256=policy_sha256,
            rules_sha256=rules_sha256,
            access_index_sha256=access_index_sha256,
            hook_rules_digest=digests.rules,
            hook_code_digest=digests.code,
        )
    except AuditKeyError as error:
        raise StartupError(error.code) from None
    except AuditWriteError:
        raise StartupError("audit_write_failed") from None
    effects = EffectDeps(tools.jira, tools.slack, outbox)
    users = load_users()
    graph = build_graph(
        chat,
        tools,
        saver,
        profile=profile,
        effects=effects,
        audit=audit,
        scope=ScopeIndex.load(),
        policy=load_team_policy("data/policy/team_policy.json"),
        users=users,
        **({"clock": clock} if clock is not None else {}),
    )
    return TaskService(
        graph,
        turns=turn_index
        if turn_index is not None
        else (
            PostgresTurnIndex()
            if os.getenv("GOVOPS_DATABASE_URL")
            else InMemoryTurnIndex()
        ),
        users=users,
        effects=effects,
        audit=audit,
        policy=tools.policy,
        clock=clock,
    )


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
            name = env_or("GOVOPS_AGENT_PROFILE", "writes_org")
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
            service = build_task_service(
                chat=LiteLLMChatClient(
                    key_resolver=resolver.key_for,
                    expected_digests=compute_digests(
                        Path(__file__).resolve().parents[3]
                    ),
                    require_guardrail=True,
                ),
                tools=tools,
                saver=saver,
                outbox=outbox,
                profile=profile_for(name),
                audit_dir=Path(env_or("AUDIT_DIR", "data/interim/audit")),
                turn_index=turn_index,
            )
            await service.startup_cleanup()
            await startup_prune(service, saver, turn_index)
            try:
                yield service
            finally:
                await service.aclose()
    finally:
        await rest_client.aclose()

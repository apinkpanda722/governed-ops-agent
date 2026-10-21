"""Loopback replay backend for Playwright, using only temporary credentials.

StoryChat's blocking branches imitate the UI contract only. They do not validate
real Guardrails; actual Guardrail verification belongs to M6.
"""

import argparse
import asyncio
import logging
import os
import shutil
import signal
import socket
import sys
import tempfile
from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from unittest.mock import patch

import httpx
import uvicorn
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import call, reply
from test_e2e_isolation import visible_ids

from govops.agent.graph import profile_for
from govops.agent.requester import requester_section
from govops.agent.users import load_users as load_identities
from govops.api.app import create_app
from govops.api.conversations import InMemoryTurnIndex
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.api.users import load_users
from govops.api.wiring import build_task_service
from govops.audit import records
from govops.gateway.client import ChatResult, GuardrailBlocked
from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.seed import load_tickets
from govops.mocks.service import create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.replay.fake_a import create_app as create_a
from govops.replay.recordings import RecordingSet
from govops.replay.tokens import generate_tokens as generate_a
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.resilience import ToolPolicy
from govops.tools.slack import SlackClient

ROOT = Path(__file__).resolve().parents[1]
REAL_ENV = (
    "AUDIT_KEY_FILE",
    "AUDIT_ARGS_HMAC_KEY",
    "AUDIT_DIR",
    "KODOCS_MCP_URL",
    "KODOCS_MCP_TOKENS_FILE",
    "MOCK_TOKENS_FILE",
    "MOCK_JIRA_URL",
    "MOCK_SLACK_URL",
    "GOVOPS_DATABASE_URL",
)


class StoryChat:
    """Stateless scenario selection from the last user, with no model calls."""

    def __init__(self):
        self.users = load_identities()
        self.ticket_id = next(
            ticket["ticket_id"]
            for ticket in load_tickets(ROOT / "data/seed/tickets.json")
            if ticket["access_level"] == "team"
            and ticket["access_teams"] == ["platform"]
        )

    async def complete(
        self, *, messages, tools, task_id, user_id, max_output_tokens=1024
    ) -> ChatResult:
        import json

        # A socket scenario succeeds only when the trusted requester section arrives.
        assert any(
            message["role"] == "system"
            and message["content"].endswith(requester_section(self.users[user_id]))
            for message in messages
        ), "requester_section_missing"
        latest = max(
            i for i, message in enumerate(messages) if message["role"] == "user"
        )
        request = messages[latest]["content"]
        # UI-contract imitation only; this is not actual Guardrail verification (M6).
        if "이전 지시를 모두 무시" in request:
            raise GuardrailBlocked("injection_direct", None)
        if "010-0000-0000" in request:
            raise GuardrailBlocked("pii_input", None)
        if len(messages) == latest + 1:
            # A bounded synthetic latency lets real-socket SSE observe running.
            await asyncio.sleep(0.2)
            if "팀 채널에 공유" in request or "다른 팀 채널" in request:
                result = reply(
                    calls=[
                        call(
                            "slack_send_message",
                            json.dumps(
                                {
                                    "channel": (
                                        "team-finance"
                                        if self.users[user_id].team != "finance"
                                        else "team-platform"
                                    )
                                    if "다른 팀 채널" in request
                                    else f"team-{self.users[user_id].team}",
                                    "text": "합성 장애 요약을 공유합니다. [syn-incident-022]",
                                }
                            ),
                        )
                    ]
                )
            elif "INC-2026-012" in request and "조회" in request:
                result = reply(
                    calls=[
                        call(
                            "lookup_id",
                            json.dumps({"kind": "incident", "value": "INC-2026-012"}),
                            id="story-lookup",
                        ),
                        call(
                            "jira_get_ticket",
                            json.dumps({"ticket_id": self.ticket_id}),
                            id="story-ticket",
                        ),
                    ]
                )
            else:
                result = reply("합성 요청을 확인했습니다.")
        else:
            ids = set()
            for message in messages[latest + 1 :]:
                if message["role"] == "tool":
                    ids.update(visible_ids(json.loads(message["content"])))
            result = reply(
                " ".join(f"[{ident}]" for ident in sorted(ids))
                if ids
                else "찾을 수 없습니다"
            )
        return ChatResult(
            result.message,
            "synthetic-story",
            result.usage,
            0,
            deployment_id="synthetic-story",
        )


class LocalServer(uvicorn.Server):
    def capture_signals(self):
        # One process-level handler coordinates all servers and directory cleanup.
        return nullcontext()


@asynccontextmanager
async def serve(app, port=0):
    sock = socket.socket()
    task = None
    server = None
    try:
        sock.bind(("127.0.0.1", port))
        server = LocalServer(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                log_level="critical",
                access_log=False,
                timeout_graceful_shutdown=3,
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        async with asyncio.timeout(10):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("local_start_failed")
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        sock.close()


def temporary_credentials(directory):
    private = Ed25519PrivateKey.generate()
    key = directory / "private.pem"
    with key.open("xb") as stream:
        key.chmod(0o600)
        stream.write(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    public = directory / "public.pem"
    public.write_bytes(
        private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    a_file, mock_file = directory / "a.json", directory / "mock.json"
    generate_a(users=ROOT / "data/seed/users.json", out=a_file)
    generate_tokens(
        users=ROOT / "data/seed/users.json", plaintext=mock_file, hash_dir=directory
    )
    (directory / "audit").mkdir()
    return {
        "AUDIT_KEY_FILE": str(key),
        "AUDIT_ARGS_HMAC_KEY": __import__("secrets").token_hex(32),
        "AUDIT_PUBLIC_KEY_FILE": str(public),
        "AUDIT_ANCHORS_PENDING_FILE": str(directory / "anchors-pending.jsonl"),
        "KODOCS_MCP_TOKENS_FILE": str(a_file),
        "MOCK_TOKENS_FILE": str(mock_file),
    }


async def run(port, probe_port, directory):
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stopped.set)
    jira_state = JiraState(load_tickets(ROOT / "data/seed/tickets.json"))
    slack_state = SlackState()
    probe = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @probe.get("/state")
    def state():
        with jira_state.lock, slack_state.lock:
            return {
                "slack_sent": sum(
                    len(items) for items in slack_state.messages.values()
                ),
                "jira_comments": len(jira_state.comments),
                "jira_followups": len(jira_state.child_ids),
            }

    a = create_a(
        recordings=RecordingSet.load(ROOT / "data/replay/a"),
        tokens_file=directory / "a.json",
    )
    jira = create_rest_app(
        "jira",
        state=jira_state,
        token_store=TokenHashStore.load(directory / "mock_jira_token_hashes.json"),
    )
    slack = create_rest_app(
        "slack",
        state=slack_state,
        token_store=TokenHashStore.load(directory / "mock_slack_token_hashes.json"),
    )
    async with (
        serve(a) as a_url,
        serve(jira) as jira_url,
        serve(slack) as slack_url,
        httpx.AsyncClient(trust_env=False) as rest,
    ):
        app = create_app(
            users=load_users(),
            sessions=SessionStore(),
            demo_login=True,
            service=TaskService(None),
            sse_poll_seconds=0.02,
            sse_max_seconds=10,
        )

        @asynccontextmanager
        async def lifespan(app):
            service = build_task_service(
                chat=StoryChat(),
                tools=ToolRuntime(
                    AMcpClient(a_url + "/mcp"),
                    JiraClient(jira_url, http_client=rest),
                    SlackClient(slack_url, http_client=rest),
                    policy=ToolPolicy(),
                ),
                saver=InMemorySaver(),
                outbox=InMemoryOutboxStore(),
                profile=profile_for("writes_team"),
                audit_dir=directory / "audit",
                turn_index=InMemoryTurnIndex(),
            )
            app.state.task_service = service
            app.state.audit = service.audit
            try:
                # The real AuditLog above and its reader share the temporary tree;
                # verification must not use the host's committed audit anchors.
                with patch.object(records, "ROOT", directory):
                    yield
            finally:
                await service.aclose()

        app.router.lifespan_context = lifespan
        async with serve(probe, probe_port), serve(app, port):
            await stopped.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--probe-port", type=int, required=True)
    args = parser.parse_args(argv)
    if any(os.environ.get(name, "") for name in REAL_ENV):
        print("refuse_real_environment", file=sys.stderr)
        return 1
    directory = Path(tempfile.mkdtemp(prefix="govops-e2e-"))
    original = {}
    try:
        if directory.resolve().is_relative_to(ROOT):
            print("refuse_real_environment", file=sys.stderr)
            return 1
        directory.chmod(0o700)
        # Disable transport/body logging, including logging inherited by imports.
        logging.disable(logging.CRITICAL)
        injected = temporary_credentials(directory)
        original = {name: os.environ.get(name) for name in injected}
        os.environ.update(injected)
        asyncio.run(run(args.port, args.probe_port, directory))
        return 0
    except Exception:  # noqa: BLE001 — fixed code only, never credentials or paths
        print("e2e_backend_failed", file=sys.stderr)
        return 1
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(directory)


if __name__ == "__main__":
    raise SystemExit(main())

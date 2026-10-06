"""Session-to-server isolation using committed replay and real tool transports."""

import asyncio
import copy
import json
import logging
import pickle
from pathlib import Path
from uuid import uuid4

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import call, reply
from test_effects_matrix import (
    setup as effect_setup,  # noqa: F401 — parametrized ASGI fixture
)

from govops.agent.graph import build_graph
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.seed import load_tickets
from govops.mocks.service import create_admin_app, create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.obs.jsonl import JsonlSink
from govops.obs.mask import register_secret_values
from govops.replay.fake_a import create_app as create_a
from govops.replay.recordings import RecordingSet
from govops.replay.tokens import generate_tokens as generate_a
from govops.tools.a_mcp import AMcpClient
from govops.tools.contract import find_identity_fields
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient

ROOT = Path(__file__).resolve().parents[1]
USERS = ("demo-platform-g3", "demo-finance-g3")
REQUEST = "INC-2026-012 장애와 관련 티켓을 조회해 주세요"


class Capture:
    """Capture HTTP wire data, keeping concurrent requests independently bound."""

    def __init__(self, app):
        self.app = app
        self.requests = []
        self.responses = []

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = {
            "headers": scope["headers"],
            "url": scope["path"].encode() + b"?" + scope["query_string"],
            "method": scope["method"],
            "body": b"",
        }
        self.requests.append(request)

        async def receiving():
            message = await receive()
            request["body"] += message.get("body", b"")
            return message

        async def sending(message):
            self.responses.append(copy.deepcopy(message))
            await send(message)

        await self.app(scope, receiving, sending)


def visible_ids(value):
    ids = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"doc_id", "ticket_id"} and isinstance(child, str):
                ids.add(child)
            ids.update(visible_ids(child))
    elif isinstance(value, list):
        for child in value:
            ids.update(visible_ids(child))
    return ids


def plain_containers(value):
    """Preserve all serialized checkpoint bytes without defaultdict factories."""
    if isinstance(value, dict):
        return {key: plain_containers(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain_containers(child) for child in value]
    return value


class ScriptedChat:
    def __init__(self, ticket_id):
        self.ticket_id = ticket_id
        self.mode = "read"
        self.calls = []

    async def complete(self, **kwargs):
        # Only messages/tools are model context; user_id is gateway metadata.
        self.calls.append(copy.deepcopy(kwargs))
        messages = kwargs["messages"]
        if messages[-1]["role"] == "user":
            scripts = {
                "read": [
                    ("lookup_id", {"kind": "incident", "value": "INC-2026-012"}),
                    ("jira_get_ticket", {"ticket_id": self.ticket_id}),
                ],
                "missing": [("jira_get_ticket", {"ticket_id": "PLT-999999"})],
                "write": [
                    ("slack_send_message", {"channel": "announce", "text": "합성"}),
                    ("jira_add_comment", {"ticket_id": self.ticket_id, "body": "합성"}),
                    (
                        "jira_create_followup_ticket",
                        {"parent_ticket_id": self.ticket_id, "title": "합성"},
                    ),
                ],
                "miss": [("search_docs", {"query": "기록에 없는 합성 질의"})],
            }
            return reply(
                calls=[
                    call(name, json.dumps(args), id=f"call-{i}")
                    for i, (name, args) in enumerate(scripts[self.mode])
                ]
            )
        ids = set()
        for message in reversed(messages):
            if message["role"] != "tool":
                break
            ids.update(visible_ids(json.loads(message["content"])))
        return reply(
            " ".join(f"[{ident}]" for ident in sorted(ids))
            if ids
            else "찾을 수 없습니다"
        )


def test_e2e_isolation(tmp_path, monkeypatch, serve_asgi, caplog, capsys):
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("govops.obs.trace._defaults", (JsonlSink(trace_dir),))
    caplog.set_level(logging.DEBUG)
    for name in tuple(logging.Logger.manager.loggerDict):
        caplog.set_level(logging.DEBUG, logger=name)
    for name in ("httpx", "httpcore", "httpx2", "httpcore2", "mcp", "uvicorn"):
        caplog.set_level(logging.DEBUG, logger=name)
    users = tmp_path / "users.json"
    users.write_text(
        json.dumps(
            {
                "users": [
                    u
                    for u in json.loads((ROOT / "data/seed/users.json").read_text())[
                        "users"
                    ]
                    if u["label"] in USERS
                ]
            }
        )
    )
    seed = tmp_path / "tickets.json"
    seed.write_bytes((ROOT / "data/seed/tickets.json").read_bytes())
    tickets = load_tickets(seed)
    selected = next(
        t
        for t in tickets
        if t["access_level"] == "team" and t["access_teams"] == ["platform"]
    )
    assert all(t["ticket_id"] != "PLT-999999" for t in tickets)
    a_file, mock_file = tmp_path / "a.json", tmp_path / "mock.json"
    generate_a(users=users, out=a_file)
    generate_tokens(users=users, plaintext=mock_file, hash_dir=tmp_path)
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(a_file))
    monkeypatch.setenv("MOCK_TOKENS_FILE", str(mock_file))
    secrets = ["SENTINEL-LITELLM-AGENT-KEY", "SENTINEL-LITELLM-MASTER-KEY"]
    for name, value in zip(
        ("LITELLM_AGENT_KEY", "LITELLM_MASTER_KEY"), secrets, strict=True
    ):
        monkeypatch.setenv(name, value)
    a_tokens = {u["label"]: u["token"] for u in json.loads(a_file.read_text())["users"]}
    mock_tokens = {
        u["label"]: u["tokens"] for u in json.loads(mock_file.read_text())["users"]
    }
    secrets += list(a_tokens.values()) + [
        v for u in mock_tokens.values() for v in u.values()
    ]
    register_secret_values(secrets)
    a_capture = Capture(
        create_a(
            recordings=RecordingSet.load(ROOT / "data/replay/a"), tokens_file=a_file
        )
    )
    jira_state, slack_state = JiraState(tickets), SlackState()
    captures = [a_capture]
    for audience, state in (("jira", jira_state), ("slack", slack_state)):
        captures.append(
            Capture(
                create_rest_app(
                    audience,
                    state=state,
                    token_store=TokenHashStore.load(
                        tmp_path / f"mock_{audience}_token_hashes.json"
                    ),
                )
            )
        )
    a_client = AMcpClient(serve_asgi(a_capture) + "/mcp")
    chat, saver = ScriptedChat(selected["ticket_id"]), InMemorySaver()
    public_responses = []

    async def exercise():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=captures[1])) as jh,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=captures[2])) as sh,
        ):
            runtime = ToolRuntime(
                a_client,
                JiraClient("http://127.0.0.1", http_client=jh),
                SlackClient("http://127.0.0.1", http_client=sh),
            )
            service = TaskService(build_graph(chat, runtime, saver))
            app = create_app(
                users=frozenset(USERS),
                sessions=SessionStore(),
                demo_login=True,
                service=service,
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
                ) as api,
            ):
                headers = {}
                for user in USERS:
                    login = await api.post("/sessions", json={"user": user})
                    assert login.status_code == 201
                    token = login.json()["session_token"]
                    secrets.append(token)
                    public_responses.append(repr(dict(login.headers)).encode())
                    headers[user] = {"Authorization": "Bearer " + token}

                async def request(method, path, user, **kwargs):
                    response = await api.request(
                        method, path, headers=headers[user], **kwargs
                    )
                    public_responses.append(
                        response.content + repr(dict(response.headers)).encode()
                    )
                    return response

                async def task(user):
                    created = await request(
                        "POST",
                        "/tasks",
                        user,
                        json={
                            "request": REQUEST,
                            "user": USERS[0] if user == USERS[1] else USERS[1],
                            "team": "platform",
                            "grade": 99,
                        },
                    )
                    assert created.status_code == 202
                    ident = created.json()["task_id"]
                    for _ in range(500):
                        result = await request("GET", "/tasks/" + ident, user)
                        if result.json()["status"] != "running":
                            break
                        await asyncio.sleep(0.01)
                    assert result.json()["status"] == "completed"
                    return ident, result.json()

                platform_id, platform = await task(USERS[0])
                _, finance = await task(USERS[1])
                for key in ("observed_doc_ids", "cited_doc_ids"):
                    assert "syn-incident-022" in platform[key]
                    assert "syn-incident-022" not in finance[key]
                assert finance["answer"] == "찾을 수 없습니다"
                assert selected["ticket_id"] in platform["observed_doc_ids"]
                denied = json.loads(chat.calls[3]["messages"][-1]["content"])
                assert denied["error"]["code"] == "not_found"
                hidden = await request("GET", "/tasks/" + platform_id, USERS[1])
                missing = await request("GET", "/tasks/" + uuid4().hex, USERS[1])
                assert hidden.status_code == missing.status_code == 404
                assert (hidden.content, dict(hidden.headers)) == (
                    missing.content,
                    dict(missing.headers),
                )
                counts = (len(chat.calls), [len(c.requests) for c in captures])
                for _ in range(5):
                    assert (
                        await request("GET", "/tasks/" + platform_id, USERS[0])
                    ).json() == platform
                assert counts == (len(chat.calls), [len(c.requests) for c in captures])
                chat.mode = "missing"
                await task(USERS[1])
                assert json.loads(chat.calls[-1]["messages"][-1]["content"]) == denied
                chat.mode = "write"
                before = [len(c.requests) for c in captures]
                await task(USERS[0])
                assert before == [len(c.requests) for c in captures]
                assert [
                    json.loads(m["content"])["error"]["code"]
                    for m in chat.calls[-1]["messages"]
                    if m["role"] == "tool"
                ] == ["tool_not_available"] * 3
                chat.mode = "miss"
                await task(USERS[0])
                assert (
                    json.loads(chat.calls[-1]["messages"][-1]["content"])["error"][
                        "code"
                    ]
                    == "replay_miss"
                )
            for audience, state in (("jira", jira_state), ("slack", slack_state)):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=create_admin_app(audience, state=state)
                    ),
                    base_url="http://127.0.0.1",
                ) as admin:
                    report = (await admin.get("/state")).json()
                    if audience == "jira":
                        assert report["comments"] == report["children"] == []
                        assert report["ticket_count"] == len(tickets)
                    else:
                        assert all(
                            c["message_count"] == 0 and c["messages"] == []
                            for c in report["channels"]
                        )

    asyncio.run(exercise())
    for capture, tokens in zip(
        captures[:2],
        (a_tokens, {u: t["mock-jira"] for u, t in mock_tokens.items()}),
        strict=True,
    ):
        received = set()
        for req in capture.requests:
            auth = [v for k, v in req["headers"] if k.lower() == b"authorization"]
            assert len(auth) == 1
            assert auth[0] in {f"Bearer {v}".encode() for v in tokens.values()}
            received.add(auth[0])
            for forbidden in (*USERS, "platform", "finance", "user", "team", "grade"):
                assert forbidden.encode() not in req["url"] + req["body"]
            if capture is captures[1]:
                assert req["method"] == "GET"
            else:
                assert req["method"] == "POST"
                body = json.loads(req["body"])
                if body.get("method") == "tools/call":
                    assert body["params"]["name"] in {"lookup_id", "search_docs"}
        assert received == {f"Bearer {v}".encode() for v in tokens.values()}
    assert not set(a_tokens.values()) & {
        v for t in mock_tokens.values() for v in t.values()
    }
    assert len(set(secrets)) == len(secrets)
    assert captures[2].requests == []
    for recorded in chat.calls:
        assert len(recorded["tools"]) == 6
        assert all(
            not find_identity_fields(t["function"]["parameters"])
            for t in recorded["tools"]
        )
        for message in recorded["messages"]:
            assert all(user not in json.dumps(message) for user in USERS)
            if message["role"] in {"system", "user"}:
                assert all(
                    team not in message["content"] for team in ("platform", "finance")
                )
    trace_files = list(trace_dir.glob("*.jsonl"))
    assert len(trace_files) == 5
    trace_output = b"".join(path.read_bytes() for path in trace_files)
    assert b"Authorization" not in trace_output and b"Bearer" not in trace_output
    for path in trace_files:
        records = [json.loads(line) for line in path.read_text().splitlines()]
        roots = [r for r in records if r["kind"] == "task"]
        assert len(roots) == 1 and roots[0]["trace_id"] == path.stem
        owner = roots[0]["attributes"]["user_id"]
        assert all(
            other not in json.dumps(records) for other in USERS if other != owner
        )
    outputs = [
        trace_output,
        b"".join(public_responses),
        json.dumps(
            [{k: c[k] for k in ("messages", "tools")} for c in chat.calls]
        ).encode(),
        pickle.dumps(plain_containers((saver.storage, saver.writes, saver.blobs))),
        (caplog.text + str(capsys.readouterr())).encode(),
        pickle.dumps([c.responses for c in captures]),
    ]
    for output in outputs:
        for secret in secrets:
            assert secret.encode() not in output, (
                "secret leaked into public/persisted output"
            )


def test_write_graph_calls_in_process_services_as_requester(effect_setup, monkeypatch):  # noqa: F811 — imported pytest fixture
    from langgraph.types import Command
    from test_agent_graph import FakeChat

    from govops.agent.graph import WITH_WRITES, AgentContext, EffectDeps, run_task
    from govops.agent.scope import AccessRow, ScopeIndex
    from govops.agent.users import UserInfo
    from govops.broker import credentials
    from govops.guardrails.policy import TeamPolicy

    s = effect_setup
    requester = UserInfo("demo-platform-g3", "platform", 3)
    approver = UserInfo("demo-platform-g4", "platform", 4)
    received = []
    credential = credentials.credential_for

    def credential_spy(user, audience):
        received.append((user, audience))
        return credential(user, audience)

    monkeypatch.setattr(credentials, "credential_for", credential_spy)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=s["app"])
        ) as http:
            jira = JiraClient("http://synthetic", http_client=http)
            slack = SlackClient("http://synthetic", http_client=http)
            tools = ToolRuntime(None, jira, slack)
            chat = FakeChat(
                reply(calls=[call(s["req"].tool, json.dumps(s["req"].args))])
            )
            scope = ScopeIndex(
                {}, {s["target"]: AccessRow("public", (), 0)}, (requester, approver)
            )
            graph = build_graph(
                chat,
                tools,
                InMemorySaver(),
                profile=WITH_WRITES,
                effects=EffectDeps(jira, slack, s["outbox"]),
                audit=s["audit"],
                scope=scope,
                policy=TeamPolicy({"platform": "block"}),
                users={u.label: u for u in (requester, approver)},
                clock=s["clock"].timestamp,
            )
            task_id = uuid4().hex
            state = await run_task(
                graph, task_id=task_id, user_id=requester.label, request="합성 초안"
            )
            assert state["status"] == "awaiting_approval"
            actor = approver if s["service"] == "slack" else requester
            state = await graph.ainvoke(
                Command(
                    resume={
                        "approver": actor.label,
                        "decision": "approve",
                        "draft_sha256": state["draft_sha256"],
                    }
                ),
                {"configurable": {"thread_id": task_id}},
                context=AgentContext(requester.label),
            )
            assert state["status"] == "completed"
            assert received == [(requester.label, "mock-" + s["service"])]
            assert len(await s["outbox"].list_open()) == 0

    asyncio.run(exercise())


def test_approval_api_and_send_free_restart_recovery(
    effect_setup,  # noqa: F811
    monkeypatch,
    tmp_path,
):
    import hashlib
    from dataclasses import replace

    from test_agent_graph import FakeChat

    from govops.agent.graph import WITH_WRITES, EffectDeps
    from govops.agent.scope import AccessRow, ScopeIndex
    from govops.agent.users import UserInfo
    from govops.broker import credentials
    from govops.eval.duplicates import count_effects
    from govops.eval.spec import Effect
    from govops.mocks.auth import MockUser
    from govops.tools.outbox import OutboxState

    s = effect_setup
    requester = UserInfo("demo-platform-g6", "platform", 6)
    approver = UserInfo("demo-platform-g4", "platform", 4)
    users = {u.label: u for u in (requester, approver)}
    received = []
    credential = credentials.credential_for
    secret = credential(requester.label, "mock-" + s["service"])
    rest_app = create_rest_app(
        s["service"],
        state=s["ss"] if s["service"] == "slack" else s["js"],
        token_store=TokenHashStore(
            (
                (
                    MockUser(requester.label, requester.team, requester.grade),
                    hashlib.sha256(secret.encode()).hexdigest(),
                ),
            )
        ),
        faults=s["faults"],
    )
    trace_dir = tmp_path / "traces"
    monkeypatch.setattr("govops.obs.trace._defaults", (JsonlSink(trace_dir),))

    def spy(user, audience):
        received.append((user, audience))
        return credential(user, audience)

    monkeypatch.setattr(credentials, "credential_for", spy)

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=rest_app)
        ) as rest:
            jira = JiraClient("http://synthetic", http_client=rest)
            slack = SlackClient("http://synthetic", http_client=rest)
            tools = ToolRuntime(None, jira, slack)
            effects = EffectDeps(jira, slack, s["outbox"])
            graph = build_graph(
                FakeChat(reply(calls=[call(s["req"].tool, json.dumps(s["req"].args))])),
                tools,
                InMemorySaver(),
                profile=WITH_WRITES,
                effects=effects,
                audit=s["audit"],
                scope=ScopeIndex(
                    {}, {s["target"]: AccessRow("public", (), 0)}, tuple(users.values())
                ),
                policy=TeamPolicy({"platform": "block"}),
                users=users,
                clock=s["clock"].timestamp,
            )
            service = TaskService(
                graph,
                users=users,
                effects=effects,
                audit=s["audit"],
                policy=tools.policy,
            )
            ident = await service.start(requester.label, "합성 요청")
            await asyncio.gather(*service._tasks)
            waiting = await service.get(requester.label, ident)
            actor = approver.label if s["service"] == "slack" else requester.label
            sessions = SessionStore()
            token, _ = sessions.issue(actor)
            app = create_app(
                users=frozenset(users),
                sessions=sessions,
                demo_login=False,
                service=service,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
            ) as api:

                async def approve():
                    return await api.post(
                        f"/tasks/{ident}/approval",
                        headers={"Authorization": "Bearer " + token},
                        json={
                            "decision": "approve",
                            "draft_sha256": waiting["draft_sha256"],
                        },
                    )

                responses = await asyncio.gather(approve(), approve())
                assert responses[0].json() == responses[1].json()
                assert responses[0].json()["status"] == "completed"
            assert received == [(requester.label, "mock-" + s["service"])]
            key, row = next(iter(s["outbox"].rows.items()))
            # Simulate an effect committed remotely but lost before checkpoint completion.
            await s["outbox"].update(key, state=OutboxState.UNCERTAIN)
            await graph.aupdate_state(
                {"configurable": {"thread_id": ident}}, {"status": "running"}
            )
            fresh = TaskService(
                graph,
                users=users,
                effects=effects,
                audit=s["audit"],
                policy=tools.policy,
            )
            await fresh.startup_cleanup()
            view = await fresh.get(requester.label, ident)
            assert view["status"] == "interrupted" and view["effect_result"] == "sent"
            assert view["retry_hint"] == "다시 요청해 주세요"
            assert received == [(requester.label, "mock-" + s["service"])] * 2
            assert (await s["outbox"].get(key)).attempts == 1
            # A different marker has never been sent; two absent lookups confirm that.
            absent = replace(row, idempotency_key="0" * 64, state=OutboxState.UNCERTAIN)
            await s["outbox"].claim(absent)
            await fresh.startup_cleanup()
            assert (await fresh.get(requester.label, ident))[
                "effect_result"
            ] == "not_sent"
            assert (await s["outbox"].get(key)).attempts == 1
            assert (await s["outbox"].get(absent.idempotency_key)).attempts == 1
            assert all(
                e[1].get("sends", 0) == 0
                for e in s["audit"].events
                if e[0] == "side_effect.result" and e[1].get("lookups", 0)
            )
            report = count_effects(
                {
                    "slack": s["ss"].report(bodies=True),
                    "jira": s["js"].report(bodies=True),
                },
                task_id=ident,
                expected=[
                    Effect(kind=s["kind"], target=s["target"], count=1, structure={})
                ],
            )
            assert report.duplicates == 0 and report.missing == 0
            output = repr(graph.checkpointer.storage) + repr(responses[0].json())
            output += "".join(p.read_text() for p in tmp_path.rglob("*.jsonl"))
            assert all(value not in output for value in (token, secret))
        await service.aclose()

    from govops.guardrails.policy import TeamPolicy

    asyncio.run(exercise())

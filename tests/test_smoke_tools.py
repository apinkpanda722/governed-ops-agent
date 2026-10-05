import asyncio
import inspect
import json
import logging
from pathlib import Path

import httpx
import pytest
from test_mock_jira import ticket

from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.service import create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.replay.tokens import generate_tokens as generate_a
from govops.tools import smoke
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.slack import SlackClient


@pytest.mark.parametrize(
    "level,team,grade,minimum,expected",
    [
        ("public", "finance", 0, 9, True),
        ("team", "platform", 0, 9, True),
        ("team", "finance", 9, 0, False),
        ("restricted", "platform", 3, 3, True),
        ("restricted", "platform", 2, 3, False),
        ("restricted", "finance", 9, 3, False),
        ("unknown", "platform", 9, 0, False),
    ],
)
def test_oracle(level, team, grade, minimum, expected):
    assert (
        smoke.expected_visible(
            {"team": team, "grade": grade},
            ticket(
                access_level=level, access_teams=["platform"], access_min_grade=minimum
            ),
        )
        is expected
    )


def test_sample():
    tickets = [
        ticket(i, owner_team=team, access_level=level)
        for i, (team, level) in enumerate(
            (
                (team, level)
                for team in ("platform", "finance", "payments", "security")
                for level in ("public", "team", "restricted")
                for _ in range(8)
            ),
            1,
        )
    ]
    first = smoke.select_sample(tickets)
    assert len(first) == 40
    assert first == smoke.select_sample(list(reversed(tickets)))
    assert smoke.sample_sha256(first) == smoke.sample_sha256(
        smoke.select_sample(tickets)
    )
    for team in ("platform", "finance", "payments", "security"):
        for level, n in [("public", 1), ("team", 2), ("restricted", 2)]:
            required = sorted(
                [
                    t
                    for t in tickets
                    if t["owner_team"] == team and t["access_level"] == level
                ],
                key=lambda t: t["ticket_id"],
            )[:n]
            assert all(t in first for t in required)
    source = inspect.getsource(smoke)
    assert "PLT-1004" not in source and "SYN-1" not in source


def test_real_layers(tmp_path, monkeypatch, serve_asgi):
    users = [
        {"label": f"demo-{team}-g3", "team": team, "grade": 3}
        for team in ("platform", "finance")
    ] + [{"label": "demo-platform-g2", "team": "platform", "grade": 2}]
    users_file = tmp_path / "users.json"
    users_file.write_text(json.dumps({"users": users}))
    private = tmp_path / "mock.json"
    a_private = tmp_path / "a.json"
    generate_tokens(users=users_file, plaintext=private, hash_dir=tmp_path)
    generate_a(users=users_file, out=a_private)
    monkeypatch.setenv("MOCK_TOKENS_FILE", str(private))
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(a_private))
    tickets = [ticket(access_level="team", access_teams=["platform"])]
    a = AMcpClient(
        serve_asgi(
            create_app(
                recordings=RecordingSet.load(
                    Path(__file__).parent / "fixtures/a_recordings"
                ),
                tokens_file=a_private,
            )
        )
        + "/mcp"
    )
    jira_app = create_rest_app(
        "jira",
        state=JiraState(tickets),
        token_store=TokenHashStore.load(tmp_path / "mock_jira_token_hashes.json"),
    )
    slack_app = create_rest_app(
        "slack",
        state=SlackState(),
        token_store=TokenHashStore.load(tmp_path / "mock_slack_token_hashes.json"),
    )

    async def exercise():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=jira_app)) as jh,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=slack_app)) as sh,
        ):
            jira = JiraClient("http://jira", http_client=jh)
            slack = SlackClient("http://slack", http_client=sh)
            assert (await smoke.smoke_contract(a)).ok
            assert (await smoke.smoke_identity(a, jira, tickets)).ok
            result = await smoke.smoke_visibility(a, jira, tickets, users)
            assert result.ok and any("불일치 0" in line for line in result.lines)
            assert (await smoke.smoke_mock(jira, slack, tickets)).ok
            from govops.tools.a_mcp import ToolResult

            class LeakyA:
                async def call_tool(self, user, name, arguments):
                    result = await a.call_tool(user, name, arguments)
                    if user == "demo-finance-g3":
                        if name == "run_sql":
                            return ToolResult(
                                {
                                    "columns": ["ticket_id"],
                                    "rows": [[tickets[0]["ticket_id"]]],
                                    "truncated": False,
                                },
                                False,
                            )
                        return ToolResult(
                            {"documents": [{"doc_id": smoke.EXPECTED_DOC}]}, False
                        )
                    return result

            for mismatch in [
                await smoke.smoke_identity(LeakyA(), jira, tickets),
                await smoke.smoke_visibility(LeakyA(), jira, tickets, users),
            ]:
                assert not mismatch.ok
                assert any(
                    "demo-finance-g3" in line and "A 불일치" in line
                    for line in mismatch.lines
                )
            original = jira.get_ticket

            async def leak(user, tid):
                return (
                    tickets[0]
                    if user == "demo-finance-g3"
                    else await original(user, tid)
                )

            monkeypatch.setattr(jira, "get_ticket", leak)
            for result in [
                await smoke.smoke_identity(a, jira, tickets),
                await smoke.smoke_visibility(a, jira, tickets, users),
            ]:
                assert not result.ok
                assert any(
                    "demo-finance-g3" in line and "SYN-1" in line and "mock" in line
                    for line in result.lines
                )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "status,ok,word",
    [
        (421, True, "거절됨"),
        (403, True, "거절됨"),
        (200, False, "수락됨"),
        (401, False, "판정 불가"),
    ],
)
def test_host(tmp_path, monkeypatch, caplog, capsys, status, ok, word):
    token = "sentinel-host-secret"
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "label": "demo-platform-g3",
                        "team": "platform",
                        "grade": 3,
                        "token": token,
                    }
                ]
            }
        )
    )
    path.chmod(0o600)
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    caplog.set_level(logging.DEBUG)
    real_client = httpx.AsyncClient

    def handle(request):
        assert request.headers["host"] == "host.docker.internal:8765"
        assert request.headers["authorization"] == "Bearer " + token
        assert json.loads(request.content)["method"] == "tools/list"
        return httpx.Response(status)

    monkeypatch.setattr(
        smoke.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    )
    result = asyncio.run(
        smoke.smoke_host_header("http://127.0.0.1:8765/mcp", user_id="demo-platform-g3")
    )
    assert result.ok is ok and any(word in line for line in result.lines)
    assert token not in repr(result) + caplog.text + str(capsys.readouterr())


def test_cli_failures(monkeypatch, capsys):
    monkeypatch.delenv("KODOCS_MCP_URL", raising=False)
    assert smoke.main(["contract"]) == 1
    assert "KODOCS_MCP_URL" in capsys.readouterr().out


def test_host_real_transport(tmp_path, monkeypatch, serve_asgi, caplog, capsys):
    token = "sentinel-real-host-secret"
    path = tmp_path / "a.json"
    path.write_text(
        json.dumps(
            {
                "users": [
                    {
                        "label": "demo-platform-g3",
                        "team": "platform",
                        "grade": 3,
                        "token": token,
                    }
                ]
            }
        )
    )
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(path))
    caplog.set_level(logging.DEBUG)
    seen = []

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        headers = dict(scope["headers"])
        seen.append((headers[b"host"], headers[b"authorization"]))
        await send({"type": "http.response.start", "status": 421, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    result = asyncio.run(
        smoke.smoke_host_header(serve_asgi(app) + "/mcp", user_id="demo-platform-g3")
    )
    assert result.ok
    assert seen == [(b"host.docker.internal:8765", ("Bearer " + token).encode())]
    assert token not in repr(result) + caplog.text + str(capsys.readouterr())


def test_host_connection_failure(monkeypatch, caplog, capsys):
    token = "sentinel-exception-secret"
    monkeypatch.setattr(smoke.credentials, "credential_for", lambda *a: token)
    real_client = httpx.AsyncClient

    def fail(request):
        raise httpx.ConnectError(token, request=request)

    monkeypatch.setattr(
        smoke.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(fail), **kw),
    )
    result = asyncio.run(
        smoke.smoke_host_header("http://127.0.0.1/mcp", user_id="demo-platform-g3")
    )
    assert not result.ok
    assert token not in repr(result) + caplog.text + str(capsys.readouterr())


def test_contract_mismatch_and_cli(monkeypatch, capsys):
    from govops.tools.contract import load_snapshot

    class Changed:
        async def list_tools(self, user):
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,
                    "output_schema": t.output_schema,
                    "version": 2,
                }
                for t in load_snapshot().values()
            ]

    monkeypatch.setattr(
        smoke.credentials, "known_users", lambda audience: ("demo-platform-g3",)
    )
    result = asyncio.run(smoke.smoke_contract(Changed()))
    assert not result.ok and any("version mismatch" in line for line in result.lines)
    for ok in (True, False):

        async def run(*args, ok=ok):
            return smoke.SmokeResult(ok, ["판정"])

        monkeypatch.setattr(smoke, "_run", run)
        assert smoke.main(["contract"]) == (0 if ok else 1)
        assert "판정" in capsys.readouterr().out


@pytest.mark.parametrize(
    "duplicate,missing", [(False, False), (True, False), (False, True)]
)
def test_effects_fake_transport(monkeypatch, duplicate, missing):
    monkeypatch.setattr(
        smoke.credentials, "credential_for", lambda *args: "fake-smoke-key"
    )
    records = {}
    posts = []

    def handle(request):
        path = request.url.path
        kind = (
            "messages"
            if "channels" in path
            else "comments"
            if "comments" in path
            else "tickets"
        )
        if request.method == "POST":
            payload = json.loads(request.content)
            assert "smoke" in str(payload)
            posts.append(path)
            item = {
                **payload,
                {
                    "messages": "message_id",
                    "comments": "comment_id",
                    "tickets": "ticket_id",
                }[kind]: {
                    "messages": "message-1",
                    "comments": "comment-1",
                    "tickets": "PLT-9999",
                }[kind],
            }
            records[path] = [item, item] if duplicate else [item]
            return httpx.Response(
                200,
                json={"message": item}
                if kind == "messages"
                else {"comment": item}
                if kind == "comments"
                else item,
            )
        return httpx.Response(
            200, json={kind: [] if missing else records.get(path, [])}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            return await smoke.smoke_effects(
                JiraClient("http://jira", http_client=http),
                SlackClient("http://slack", http_client=http),
                [
                    ticket(
                        ticket_id="PLT-1",
                        access_level="team",
                        access_teams=["platform"],
                    )
                ],
                [{"label": "demo-platform-g3", "team": "platform", "grade": 3}],
                user_id="demo-platform-g3",
            )

    result = asyncio.run(run())
    assert result.ok is (not duplicate and not missing)
    assert len(posts) == 3 and len(set(posts)) == 3
    assert all(line.startswith(("PASS ", "FAIL ")) for line in result.lines)
    assert "fake-smoke-key" not in repr(result)


def test_effects_cli_safe_failure(monkeypatch, capsys):
    async def fail(*args):
        raise ValueError("secret-body-Authorization")

    monkeypatch.setattr(smoke, "_run", fail)
    assert smoke.main(["effects"]) == 1
    assert capsys.readouterr().out == "FAIL effects\n"

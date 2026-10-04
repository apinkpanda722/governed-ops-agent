import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest
from test_a_mcp_client import Capture as BaseCapture
from test_mock_jira import ticket

from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.service import create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.replay.fake_a import create_app
from govops.replay.recordings import RecordingSet
from govops.replay.tokens import generate_tokens as generate_a
from govops.tools.a_mcp import AMcpClient
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolNotExecutable, ToolRuntime
from govops.tools.rest import ToolHttpError
from govops.tools.slack import SlackClient


class Capture(BaseCapture):
    async def __call__(self, scope, receive, send):
        index = len(self.requests)
        await super().__call__(scope, receive, send)
        if scope["type"] == "http":
            self.requests[index]["url"] = (
                scope["path"] + "?" + scope["query_string"].decode()
            )


def test_real_layers(tmp_path, monkeypatch, serve_asgi, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    users = tmp_path / "users.json"
    users.write_text(
        json.dumps(
            {
                "users": [
                    {"label": f"demo-{team}-g3", "team": team, "grade": 3}
                    for team in ("platform", "finance")
                ]
            }
        )
    )
    private = tmp_path / "mock.json"
    a_private = tmp_path / "a.json"
    generate_tokens(users=users, plaintext=private, hash_dir=tmp_path)
    generate_a(users=users, out=a_private)
    monkeypatch.setenv("MOCK_TOKENS_FILE", str(private))
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", str(a_private))
    a_capture = Capture(
        create_app(
            recordings=RecordingSet.load(
                Path(__file__).parent / "fixtures/a_recordings"
            ),
            tokens_file=a_private,
        )
    )
    jira_capture = Capture(
        create_rest_app(
            "jira",
            state=JiraState([ticket(access_level="team", access_teams=["platform"])]),
            token_store=TokenHashStore.load(tmp_path / "mock_jira_token_hashes.json"),
        )
    )
    slack_capture = Capture(
        create_rest_app(
            "slack",
            state=SlackState(),
            token_store=TokenHashStore.load(tmp_path / "mock_slack_token_hashes.json"),
        )
    )
    a = AMcpClient(serve_asgi(a_capture) + "/mcp")

    async def exercise():
        async with (
            httpx.AsyncClient(transport=httpx.ASGITransport(app=jira_capture)) as jh,
            httpx.AsyncClient(transport=httpx.ASGITransport(app=slack_capture)) as sh,
        ):
            jira = JiraClient("http://jira", http_client=jh)
            slack = SlackClient("http://slack", http_client=sh)
            runtime = ToolRuntime(a, jira, slack)
            for name in (
                "jira_add_comment",
                "jira_create_followup_ticket",
                "slack_send_message",
                "unknown",
            ):
                with pytest.raises(ToolNotExecutable):
                    await runtime.call(name, {}, user_id="demo-platform-g3")
            for name, args in [
                ("lookup_id", {"kind": "wrong", "value": "x"}),
                ("jira_get_ticket", {"ticket_id": "bad"}),
                ("jira_search_tickets", {"limit": 21}),
                ("lookup_id", {"kind": "incident", "value": "x", "user_id": "spoof"}),
            ]:
                result = await runtime.call(name, args, user_id="demo-platform-g3")
                assert (
                    result.is_error
                    and result.payload["error"]["code"] == "invalid_argument"
                )
            assert not any(c.requests for c in (a_capture, jira_capture, slack_capture))
            results = []
            for user in ("demo-platform-g3", "demo-finance-g3"):
                results.append(
                    await runtime.call(
                        "lookup_id",
                        {"kind": "incident", "value": "INC-2026-012"},
                        user_id=user,
                    )
                )
            assert "syn-incident-022" in results[0].observed_ids
            assert "syn-incident-022" not in results[1].observed_ids
            platform = await runtime.call(
                "jira_get_ticket", {"ticket_id": "SYN-1"}, user_id="demo-platform-g3"
            )
            denied = await runtime.call(
                "jira_get_ticket", {"ticket_id": "SYN-1"}, user_id="demo-finance-g3"
            )
            missing = await runtime.call(
                "jira_get_ticket", {"ticket_id": "SYN-999"}, user_id="demo-finance-g3"
            )
            assert platform.observed_ids == ("SYN-1",)
            assert denied == missing and denied.is_error
            found = await runtime.call(
                "jira_search_tickets",
                {"project": "SYN", "query": "합성", "status": "open"},
                user_id="demo-platform-g3",
            )
            assert found.observed_ids == ("SYN-1",)
            assert await jira.list_comments("demo-platform-g3", "SYN-1") == []
            assert await jira.list_children("demo-platform-g3", "SYN-1") == []
            assert await jira.list_comments("demo-finance-g3", "SYN-1") is None
            assert await jira.list_children("demo-finance-g3", "SYN-1") is None
            assert await slack.list_channels("demo-platform-g3")
            assert (
                await slack.channel_history("demo-platform-g3", "team-platform") == []
            )
            with pytest.raises(ToolHttpError) as error:
                await slack.channel_history("demo-finance-g3", "team-platform")
            assert error.value.status == 403
            secrets = [r["token"] for r in json.loads(a_private.read_text())["users"]]
            secrets += [
                t
                for r in json.loads(private.read_text())["users"]
                for t in r["tokens"].values()
            ]
            output = (
                repr(results + [platform, denied, found])
                + str(error.value)
                + repr(error.value)
                + repr(vars(jira))
                + repr(vars(slack))
                + repr(vars(a))
                + caplog.text
                + str(capsys.readouterr())
            )
            assert all(t not in output for t in secrets)

    asyncio.run(exercise())
    audience_tokens = []
    for capture in (a_capture, jira_capture, slack_capture):
        values = set()
        for request in capture.requests:
            authorization = [
                v for k, v in request["headers"] if k.lower() == b"authorization"
            ]
            assert len(authorization) == 1
            values.add(authorization[0])
            assert b"demo-" not in request["body"]
            assert all(
                word not in request["url"]
                for word in ("demo-", "finance", "grade", "user_id")
            )
            # Channel IDs are legitimate routing arguments, not identity fields.
            assert (
                "team=" not in request["url"]
                and "platform" not in request["url"]
                or "/channels/team-platform/" in request["url"]
            )
        audience_tokens.append(values)
    assert all(
        not audience_tokens[i] & audience_tokens[j] for i in range(3) for j in range(i)
    )
    for cls, names in [
        (JiraClient, ("add_comment", "create_followup_ticket")),
        (SlackClient, ("send_message",)),
    ]:
        assert all(not hasattr(cls, name) for name in names)


def test_rest_safe_failures_and_request_credentials(monkeypatch):
    from govops.broker import credentials

    calls = []
    token = "synthetic-secret-for-test"

    def credential(user, audience):
        calls.append((user, audience))
        return token

    monkeypatch.setattr(credentials, "credential_for", credential)

    async def exercise():
        for mode in ("transport", "status", "json"):

            def handle(request, mode=mode):
                assert request.headers.get_list("Authorization") == ["Bearer " + token]
                assert token not in str(request.url)
                if mode == "transport":
                    raise httpx.ConnectError(token, request=request)
                if mode == "status":
                    return httpx.Response(
                        500, json={"error": {"code": token, "message": token}}
                    )
                return httpx.Response(200, content=token)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
                client = JiraClient("http://jira", http_client=http)
                with pytest.raises(ToolHttpError) as caught:
                    await client.get_ticket("demo-platform-g3", "SYN-1")
                assert token not in str(caught.value) + repr(caught.value) + repr(
                    vars(client)
                )
                assert caught.value.__context__ is None
        assert calls == [("demo-platform-g3", "mock-jira")] * 3

    asyncio.run(exercise())

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from govops.costs import BudgetExceededError
from govops.evalrun.dev import guard_live, infrastructure_failure, run_dev, select_tasks
from govops.gateway.client import ChatResult, GatewayError, Usage
from govops.tools.registry import ToolOutcome

ROOT = Path(__file__).resolve().parents[1]


def specs():
    return json.loads((ROOT / "data/eval/tasks.json").read_text())


def test_selection():
    assert [s.task_id for s in select_tasks(specs())] == [
        "T1-01",
        "T1-05",
        "T1-08",
        "T5-01",
        "T5-02",
        "T5-05",
        "T5-08",
    ]
    for row in specs():
        if row["split"] == "test" or row["executable_in"] == "m3":
            with pytest.raises(ValueError):
                select_tasks(specs(), [row["task_id"]])


@pytest.mark.parametrize(
    "tracked,head,clean",
    [(False, True, True), (True, False, True), (True, True, False)],
)
def test_git_guard(tracked, head, clean):
    with pytest.raises(ValueError):
        guard_live(
            {
                "hypotheses_tracked": tracked,
                "hypotheses_in_head": head,
                "worktree_clean": clean,
            }
        )


@pytest.mark.parametrize(
    "error,expected",
    [
        (GatewayError("gateway HTTP 503"), True),
        (GatewayError("gateway HTTP 400"), False),
        (GatewayError("gateway timeout"), True),
        (GatewayError("gateway connection failure"), True),
        (httpx.ConnectError("private"), True),
        (httpx.ReadTimeout("private"), True),
        (BudgetExceededError("private"), False),
        (ValueError("private"), False),
    ],
)
def test_infra(error, expected):
    assert infrastructure_failure(error) == expected


class Chat:
    def __init__(self, errors=()):
        self.errors = list(errors)
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        if not any(m["role"] == "tool" for m in kwargs["messages"]):
            message = {
                "content": None,
                "tool_calls": [
                    {"id": "c1", "name": "search_docs", "arguments": '{"query":"합성"}'}
                ],
            }
        else:
            message = {"content": "찾을 수 없습니다", "tool_calls": []}
        return ChatResult(message, "gpt-6-luna", Usage(1, 0, 1), 0.0)


class Tools:
    async def call(self, *args, **kwargs):
        return ToolOutcome({"hits": []}, False, ())


def metadata():
    return {
        "hypotheses_tracked": True,
        "hypotheses_in_head": True,
        "worktree_clean": True,
        "commit_hash": "a" * 40,
        "a_git_head": "d2eec9c",
        "index_name": "synthetic",
        "hypotheses_sha256": "b" * 64,
        "hypotheses_registration_commit": "c" * 40,
    }


def exercise(tmp_path, chat, seed=True, task="T5-02"):
    async def check():
        return seed

    return asyncio.run(
        run_dev(
            select_tasks(specs(), [task]),
            root=tmp_path,
            chat=chat,
            tools=Tools(),
            seed_check=check,
            metadata=metadata(),
            costs=lambda: {"agent_primary": 0.0},
        )
    )


def test_one_run_artifacts_and_replay(tmp_path):
    result = exercise(tmp_path, Chat())
    run = Path(result["directory"])
    for file in [
        run / "observations.jsonl",
        run / "manifest.json",
        run / "grades.json",
        tmp_path / "data/replay/llm/T5-02.jsonl",
        tmp_path / "data/replay/a_run/T5-02.jsonl",
        tmp_path / "data/replay/golden/status.json",
    ]:
        assert file.is_file()
    manifest = json.loads((run / "manifest.json").read_text())
    assert {
        "commit_hash",
        "worktree_clean",
        "mode",
        "model_id",
        "call_params",
        "prompt_hash",
        "a_git_head",
        "contract_snapshot_sha256",
        "index_name",
        "hypotheses_sha256",
        "hypotheses_registration_commit",
        "costs_by_purpose",
        "trace_ids",
        "infrastructure_failures",
        "fallback_used",
        "models_seen",
        "started_at",
        "finished_at",
    } <= manifest.keys()
    for file in tmp_path.rglob("*.json*"):
        assert all(
            word not in file.read_text()
            for word in ["Bearer", "Authorization", "api_key"]
        )
    from govops.agent.graph import build_graph
    from govops.golden import digest, replay_task

    obs = asyncio.run(
        replay_task(
            "T5-02",
            llm_dir=tmp_path / "data/replay/llm",
            a_dir=tmp_path / "data/replay/a_run",
            mock_executor=Tools(),
            graph_factory=build_graph,
        )
    )
    status = json.loads((tmp_path / "data/replay/golden/status.json").read_text())[
        "T5-02"
    ]
    assert status["observation_digest"] == digest(obs)


@pytest.mark.parametrize(
    "errors,count",
    [
        ([GatewayError("gateway HTTP 503")], 1),
        ([httpx.ConnectError("private"), httpx.ReadTimeout("private")], 2),
    ],
)
def test_retry(tmp_path, errors, count):
    result = exercise(tmp_path, Chat(errors))
    assert result["manifest"]["infrastructure_failures"] == count
    assert len(result["grades"]) == 1
    assert len(result["manifest"]["attempts"]["T5-02"]) == 2
    assert len(list(Path(result["directory"]).glob("attempts/*/llm/*.jsonl"))) == 2
    if count == 2:
        assert not result["grades"]["T5-02"]["tcr"]["passed"]


def test_tcr_failure_no_retry(tmp_path):
    class EmptyChat(Chat):
        async def complete(self, **kwargs):
            result = await super().complete(**kwargs)
            if not result.message.get("tool_calls"):
                result.message["content"] = ""
            return result

    result = exercise(tmp_path, EmptyChat(), task="T1-01")
    assert len(result["manifest"]["attempts"]["T1-01"]) == 1
    assert not result["grades"]["T1-01"]["tcr"]["passed"]
    assert json.loads((tmp_path / "data/replay/golden/status.json").read_text())[
        "T1-01"
    ]["recorded_fail"]


def test_seed_mismatch_stops_before_task(tmp_path):
    chat = Chat()
    with pytest.raises(ValueError, match="mock_seed_mismatch"):
        exercise(tmp_path, chat, seed=False)
    assert chat.calls == 0


def test_budget_no_retry(tmp_path):
    chat = Chat([BudgetExceededError("private")])
    with pytest.raises(BudgetExceededError, match="ADR-013"):
        exercise(tmp_path, chat)
    assert chat.calls == 1


def test_non_synthetic_a_excludes_task(tmp_path):
    class NonSynthetic(Tools):
        async def call(self, *args, **kwargs):
            return ToolOutcome(
                {"hits": [{"doc_id": "external-1", "body": "비합성"}]},
                False,
                ("external-1",),
            )

    async def check():
        return True

    asyncio.run(
        run_dev(
            select_tasks(specs(), ["T5-02"]),
            root=tmp_path,
            chat=Chat(),
            tools=NonSynthetic(),
            seed_check=check,
            metadata=metadata(),
            costs=lambda: {"agent_primary": 0.0},
        )
    )
    row = json.loads((tmp_path / "data/replay/golden/status.json").read_text())["T5-02"]
    assert not row["eligible"]
    assert row["excluded_reason"] == "non_synthetic_a_call"
    assert "external-1" not in (tmp_path / "data/replay/a_run/T5-02.jsonl").read_text()


def test_read_seed_hash_checks_ids_and_fields():
    from govops.evalrun.dev import read_seed_matches

    rows = [{"ticket_id": "AAA-1", "title": "합성"}]
    users = [{"label": "demo-one"}]

    class Jira:
        async def get_ticket(self, user, key):
            return dict(rows[0])

    assert asyncio.run(read_seed_matches(Jira(), rows, users))

    class Changed:
        async def get_ticket(self, user, key):
            return {"ticket_id": "AAA-2", "title": "합성"}

    assert not asyncio.run(read_seed_matches(Changed(), rows, users))


def test_internal_seed_probe_hash(monkeypatch):
    from types import SimpleNamespace

    from govops.evalrun.dev import mock_seed_check

    users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
    ticket_count = len(
        json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"]
    )
    reports = [
        {"ticket_count": ticket_count, "comments": [], "children": []},
        {
            "channels": [
                {"channel_id": c, "message_count": 0, "messages": []}
                for c in [
                    *(f"team-{team}" for team in sorted({u["team"] for u in users})),
                    "announce",
                ]
            ]
        },
    ]
    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0, stdout=json.dumps(reports[len(calls) % 2 - 1])
        )

    monkeypatch.setattr("govops.evalrun.dev.subprocess.run", probe)
    assert mock_seed_check(ROOT)
    assert all("exec" in c and "state" in c and "-T" in c for c in calls)
    reports[0]["ticket_count"] += 1
    assert not mock_seed_check(ROOT)


def test_runner_with_real_mock_runtime(tmp_path, monkeypatch):
    from govops.mocks.auth import TokenHashStore
    from govops.mocks.jira import JiraState
    from govops.mocks.seed import load_tickets
    from govops.mocks.service import create_rest_app
    from govops.mocks.slack import SlackState
    from govops.mocks.tokens import generate_tokens
    from govops.tools.a_mcp import ToolResult
    from govops.tools.jira import JiraClient
    from govops.tools.registry import ToolRuntime
    from govops.tools.slack import SlackClient

    credentials = tmp_path / "credentials.json"
    generate_tokens(
        users=ROOT / "data/seed/users.json", plaintext=credentials, hash_dir=tmp_path
    )
    monkeypatch.setenv("MOCK_TOKENS_FILE", str(credentials))

    class A:
        async def call_tool(self, user, name, arguments):
            return ToolResult({"hits": []}, False)

    class ReadChat(Chat):
        async def complete(self, **kwargs):
            result = await super().complete(**kwargs)
            if result.message.get("tool_calls"):
                result.message["tool_calls"].append(
                    {
                        "id": "c2",
                        "name": "jira_get_ticket",
                        "arguments": '{"ticket_id":"DAI-1001"}',
                    }
                )
            return result

    async def check():
        return True

    async def exercise_runtime():
        async with (
            httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=create_rest_app(
                        "jira",
                        state=JiraState(load_tickets(ROOT / "data/seed/tickets.json")),
                        token_store=TokenHashStore.load(
                            tmp_path / "mock_jira_token_hashes.json"
                        ),
                    )
                )
            ) as jh,
            httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=create_rest_app(
                        "slack",
                        state=SlackState(),
                        token_store=TokenHashStore.load(
                            tmp_path / "mock_slack_token_hashes.json"
                        ),
                    )
                )
            ) as sh,
        ):
            tools = ToolRuntime(
                A(),
                JiraClient("http://127.0.0.1", http_client=jh),
                SlackClient("http://127.0.0.1", http_client=sh),
            )
            return await run_dev(
                select_tasks(specs(), ["T5-02"]),
                root=tmp_path,
                chat=ReadChat(),
                tools=tools,
                seed_check=check,
                metadata=metadata(),
                costs=lambda: {"agent_primary": 0.0},
            )

    result = asyncio.run(exercise_runtime())
    row = json.loads((Path(result["directory"]) / "observations.jsonl").read_text())
    assert [c["name"] for c in row["tool_calls"]] == ["search_docs", "jira_get_ticket"]
    assert all(c["ok"] for c in row["tool_calls"])

    # The credential files themselves are inputs, not runner artifacts.
    def strings(value):
        if isinstance(value, dict):
            return [s for v in value.values() for s in strings(v)]
        if isinstance(value, list):
            return [s for v in value for s in strings(v)]
        return [value] if isinstance(value, str) else []

    secret_values = [
        s for s in strings(json.loads(credentials.read_text())) if len(s) > 35
    ]
    for path in (tmp_path / "data").rglob("*.json*"):
        assert not any(s in path.read_text() for s in secret_values)


def test_observations_remain_grader_readable(tmp_path):
    from govops.eval.observation import load_observations

    result = exercise(tmp_path, Chat())
    rows = load_observations(Path(result["directory"]) / "observations.jsonl")
    assert len(rows) == 1
    assert rows[0].tool_results[0]["name"] == "search_docs"


def test_retry_observation_denominator_is_one(tmp_path):
    from govops.eval.observation import load_observations

    result = exercise(tmp_path, Chat([GatewayError("gateway HTTP 502")]))
    observations = load_observations(Path(result["directory"]) / "observations.jsonl")
    assert len(observations) == 1
    assert observations[0].run_index == 1


def test_git_probe_registration(monkeypatch, tmp_path):
    from govops.evalrun.dev import git_metadata

    path = tmp_path / "docs/results/hypotheses.md"
    path.parent.mkdir(parents=True)
    path.write_text("사전 등록")

    def probe(root, *args):
        if args[0] == "status":
            return 0, ""
        if args[0] == "rev-parse":
            return 0, "d2eec9c" if root.name == "a" else "d" * 40
        if args[0] == "log":
            return 0, "e" * 40
        return 0, ""

    monkeypatch.setattr("govops.evalrun.dev._git", probe)
    monkeypatch.setenv("KODOCS_INDEX_NAME", "synthetic")
    result = git_metadata(tmp_path, tmp_path / "a")
    assert result["hypotheses_registration_commit"] == "e" * 40
    import hashlib

    assert result["hypotheses_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_models_seen_includes_rejected_response(tmp_path):
    class RejectedChat:
        async def complete(self, **kwargs):
            return ChatResult(
                {"content": "doc_id: external-1", "tool_calls": []},
                "gpt-6-luna",
                Usage(1, 0, 1),
                0.0,
            )

    result = exercise(tmp_path, RejectedChat())
    assert result["manifest"]["models_seen"] == ["gpt-6-luna"]

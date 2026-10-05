"""Committed expectations and optional user-created golden recordings."""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from govops.agent.graph import build_graph
from govops.golden import (
    GOLDEN_TASKS,
    compare,
    digest,
    golden_expectations,
    load_golden_specs,
    manifest_tasks,
    replay_task,
)
from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.seed import load_tickets
from govops.mocks.service import create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.replay.llm import _safe
from govops.replay.recordings import Recording
from govops.replay.synthetic import screen_recording
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data/replay/golden"
LLM = ROOT / "data/replay/llm"
A = ROOT / "data/replay/a_run"


def test_registered_expectations_match_specs():
    assert GOLDEN_TASKS == (
        "T1-01",
        "T1-05",
        "T1-08",
        "T5-01",
        "T5-02",
        "T5-05",
        "T5-08",
    )
    specs = load_golden_specs()
    assert len(specs) == 7
    assert all(s.split == "dev" and s.executable_in == "m2" for s in specs.values())
    assert json.loads(
        (GOLDEN / "expectations.json").read_text()
    ) == golden_expectations(specs.values())


def test_all_recordings_are_synthetic_and_credential_free():
    for path in LLM.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            _safe(json.loads(line))
    for path in A.glob("*.jsonl"):
        for line in path.read_text().splitlines():
            entry = json.loads(line)
            _safe(entry)
            assert screen_recording(Recording(**entry)).recorded


def test_optional_live_golden_replay(tmp_path, monkeypatch):
    rows = manifest_tasks(golden_dir=GOLDEN, llm_dir=LLM, a_dir=A)
    eligible = {key: row for key, row in rows.items() if row["golden_eligible"]}
    if not eligible:
        pytest.skip("미기록: 사용자 live dev 실행 전")
    credentials = tmp_path / "mock.json"
    generate_tokens(
        users=ROOT / "data/seed/users.json", plaintext=credentials, hash_dir=tmp_path
    )
    monkeypatch.setenv("MOCK_TOKENS_FILE", str(credentials))
    expectations = json.loads((GOLDEN / "expectations.json").read_text())

    class NoA:
        async def call_tool(self, *args, **kwargs):
            pytest.fail("A replay cannot access a server")

    async def exercise():
        for task_id, row in eligible.items():
            jira = JiraState(load_tickets(ROOT / "data/seed/tickets.json"))
            slack = SlackState()
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(
                        app=create_rest_app(
                            "jira",
                            state=jira,
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
                            state=slack,
                            token_store=TokenHashStore.load(
                                tmp_path / "mock_slack_token_hashes.json"
                            ),
                        )
                    )
                ) as sh,
            ):
                runtime = ToolRuntime(
                    NoA(),
                    JiraClient("http://127.0.0.1", http_client=jh),
                    SlackClient("http://127.0.0.1", http_client=sh),
                )
                obs = await replay_task(
                    task_id,
                    llm_dir=LLM,
                    a_dir=A,
                    mock_executor=runtime,
                    graph_factory=build_graph,
                )
                assert compare(expectations[task_id], row, digest(obs)) == []
                assert jira.comments == jira.child_ids == []
                assert all(c["message_count"] == 0 for c in slack.report()["channels"])

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["missing", "orphan", "excluded"])
def test_manifest_consistency(tmp_path, mode):
    golden, llm, a = (tmp_path / d for d in ("golden", "llm", "a"))
    for directory in (golden, llm, a):
        directory.mkdir()
    task_id = GOLDEN_TASKS[0]
    assert manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a) == {}
    if mode == "missing":
        (golden / "status.json").write_text(
            json.dumps({task_id: {"golden_eligible": True, "status": "pass"}})
        )
    elif mode == "orphan":
        (a / f"{task_id}.jsonl").write_text("")
    else:
        row = {
            "golden_eligible": False,
            "status": "excluded",
            "reason_codes": ["non_synthetic_a_call"],
        }
        (golden / "status.json").write_text(json.dumps({task_id: row}))
        assert manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a) == {task_id: row}
        return
    with pytest.raises(ValueError):
        manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a)

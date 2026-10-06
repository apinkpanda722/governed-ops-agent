import asyncio
import json
import re
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import call, reply

from govops.costs import BudgetExceededError
from govops.evalrun.dev_m3 import (
    guard_live_m3,
    run_dev_m3,
    select_tasks,
    trace_mismatch,
)
from govops.evalrun.explore import candidate, longest_streak
from govops.gateway.client import GatewayError
from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.seed import load_tickets
from govops.mocks.service import create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.tools.a_mcp import ToolResult
from govops.tools.jira import JiraClient
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.resilience import ToolPolicy
from govops.tools.slack import SlackClient

ROOT = Path(__file__).resolve().parents[1]


def specs():
    return select_tasks(json.loads((ROOT / "data/eval/tasks.json").read_text()))


def metadata():
    return {
        "worktree_clean": True,
        "hypotheses_m3_committed": True,
        "hypotheses_m3_unchanged": True,
        "hypotheses_m3_prompt_committed": True,
        "hypotheses_m3_prompt_unchanged": True,
        "previous_supersedes": {"previous_run_id": "m2"},
        "a_git_head": "d2eec9c",
        "index_name": "synthetic",
        "commit_hash": "a" * 40,
        "hypotheses_m3_sha256": "b" * 64,
        "hypotheses_m3_registration_commit": "c" * 40,
        "supersedes": {
            "reason": "synthetic",
            "run_id": "previous",
            "sha256": "d" * 64,
            "commit": "e" * 40,
        },
    }


def test_selection_and_guards():
    assert len(specs()) == 20
    rows = json.loads((ROOT / "data/eval/tasks.json").read_text())
    test_id = next(r["task_id"] for r in rows if r["split"] == "test")
    with pytest.raises(ValueError, match="test_task_refused"):
        select_tasks(rows, [test_id])
    with pytest.raises(ValueError, match="partial_dev_refused"):
        select_tasks(rows, [specs()[0].task_id])
    for field, code in [
        ("worktree_clean", "dirty_worktree"),
        ("hypotheses_m3_committed", "hypotheses_m3_not_committed"),
        ("hypotheses_m3_unchanged", "hypotheses_m3_not_committed"),
    ]:
        with pytest.raises(ValueError, match=code):
            guard_live_m3(metadata() | {field: False})


def test_trace_mismatch():
    assert trace_mismatch(
        {"messages": [{"role": "assistant", "tool_calls": [call()]}]}, []
    )
    assert not trace_mismatch(
        {"messages": [{"role": "assistant", "tool_calls": [call()]}]}, [{}]
    )


class Chat:
    def __init__(self, scripts, errors=(), mixed=False):
        self.scripts = scripts
        self.errors = list(errors)
        self.mixed = mixed
        self.unmapped = False
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        request = next(m["content"] for m in kwargs["messages"] if m["role"] == "user")
        count = sum(m["role"] == "assistant" for m in kwargs["messages"])
        sequence = self.scripts[request]
        result = sequence[min(count, len(sequence) - 1)]
        return replace(
            result,
            model="claude-sonnet-5-5" if self.mixed and count else "gpt-6-luna",
            fallback_used=bool(self.mixed and count),
            unmapped_model=self.unmapped,
            deployment_id="synthetic-unmapped" if self.unmapped else None,
        )


async def yield_only(_):
    await asyncio.sleep(0)


async def exercise(
    tmp_path,
    monkeypatch,
    *,
    errors=(),
    selected=None,
    mixed=False,
    preflight=(),
    service_factory=None,
    report_extra=False,
    strip_marker=False,
    smoke=False,
    unmapped=False,
):
    monkeypatch.setattr(
        "govops.evalrun.dev_m3.guard_grading_rules",
        lambda root, rules: {
            "grading_rules_sha256": "f" * 64,
            "grading_rules_registration_commit": "a" * 40,
        },
    )
    credentials = tmp_path / "credentials.json"
    generate_tokens(
        users=ROOT / "data/seed/users.json", plaintext=credentials, hash_dir=tmp_path
    )
    monkeypatch.setenv("MOCK_TOKENS_FILE", str(credentials))
    js, ss = JiraState(load_tickets(ROOT / "data/seed/tickets.json")), SlackState()

    class Admin:
        resets = 0

        def reset(self):
            self.resets += 1
            js.reset()
            ss.reset()

        def state(self, *, bodies=False):
            report = {
                "jira": js.report(bodies=bodies),
                "slack": ss.report(bodies=bodies),
            }
            if strip_marker:
                for channel in report["slack"]["channels"]:
                    for message in channel["messages"]:
                        message["metadata"] = {}
            if report_extra:
                report["jira"]["comments"] = [
                    {
                        "ticket_id": "DAI-1001",
                        "body": "합성",
                        "body_sha256": "a" * 64,
                        "metadata": {},
                    }
                ]
            return report

    class A:
        async def call_tool(self, user, name, arguments):
            if smoke and selected and selected[0].split == "smoke":
                return ToolResult(
                    {"results": [{"doc_id": "syn-incident-001", "text": "합성 본문"}]},
                    False,
                )
            return ToolResult(
                {"hits": [{"doc_id": "syn-fixture", "body": "합성 본문"}]}, False
            )

    chosen = selected or specs()
    scripts = {}
    # Derive fixture actions from user requests, never from expected effects.
    teams = {
        u["label"]: u["team"]
        for u in json.loads((ROOT / "data/seed/users.json").read_text())["users"]
    }
    for spec in chosen:
        reads = [
            reply(calls=[call("search_docs", '{"query":"합성"}')]),
            reply(calls=[call("search_docs", '{"query":"합성"}')]),
        ]
        if spec.task_id.startswith(("T2", "T4", "T6")) or spec.task_id == "T5-11":
            target = (
                "announce"
                if "전사" in spec.request
                else re.search(r"team-[a-z-]+", spec.request).group(0)
            )
            if spec.task_id.startswith("T4"):
                reads = [
                    reply(
                        calls=[
                            call("run_sql", '{"sql":"SELECT COUNT(*) FROM tickets"}')
                        ]
                    ),
                    *reads,
                ]
            action = call(
                "slack_send_message",
                json.dumps({"channel": target, "text": "합성 요약"}),
            )
            scripts[spec.request] = reads + [reply(calls=[action])]
        elif spec.task_id.startswith("T3"):
            ticket = next(
                key
                for key in re.findall(r"[A-Z]{3}-[0-9]+", spec.request)
                if not key.startswith("INC-")
            )
            reads = [
                reply(
                    calls=[call("jira_get_ticket", json.dumps({"ticket_id": ticket}))]
                ),
                *reads,
            ]
            if "후속" in spec.request:
                action = call(
                    "jira_create_followup_ticket",
                    json.dumps(
                        {
                            "parent_ticket_id": ticket,
                            "title": "합성 후속",
                            "description": "합성 요약",
                        }
                    ),
                )
            else:
                action = call(
                    "jira_add_comment",
                    json.dumps({"ticket_id": ticket, "body": "합성 요약"}),
                )
            scripts[spec.request] = reads + [reply(calls=[action])]
        else:
            scripts[spec.request] = reads + [reply("찾을 수 없습니다")]
    assert all(teams[s.user] for s in chosen)
    chat, admin = Chat(scripts, errors, mixed), Admin()
    chat.unmapped = unmapped
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=create_rest_app(
                    "jira",
                    state=js,
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
                    state=ss,
                    token_store=TokenHashStore.load(
                        tmp_path / "mock_slack_token_hashes.json"
                    ),
                )
            )
        ) as sh,
    ):
        tools = ToolRuntime(
            A(),
            JiraClient("http://synthetic", http_client=jh),
            SlackClient("http://synthetic", http_client=sh),
            policy=ToolPolicy(sleep=yield_only),
        )

        async def seed_check():
            return True

        result = await run_dev_m3(
            chosen,
            root=tmp_path,
            chat=chat,
            tools=tools,
            saver=InMemorySaver(),
            outbox=InMemoryOutboxStore(),
            mock_admin=admin,
            seed_check=seed_check,
            metadata=metadata(),
            costs=dict,
            preflight=lambda: list(preflight),
            driver_kwargs={"sleep": yield_only, "poll_interval": 0.001},
            service_factory=service_factory,
            smoke=smoke,
        )
    return result, admin, chat


def test_all_twenty_actual_graph_tool_trace_and_manifest(tmp_path, monkeypatch):
    result, admin, _ = asyncio.run(exercise(tmp_path, monkeypatch, mixed=True))
    manifest = result["manifest"]
    assert admin.resets == 20
    assert len(result["grades"]) == len(result["observations"]) == 20
    assert manifest["profile"] == "writes_org"
    assert manifest["supersedes"] == metadata()["supersedes"]
    assert manifest["previous_supersedes"] == metadata()["previous_supersedes"]
    assert manifest["models_seen"] == ["claude-sonnet-5-5", "gpt-6-luna"]
    assert manifest["mixed_model_tasks"]["count"] == 20
    assert manifest["mixed_model_tcr"]["total"] == 20
    assert manifest["mixed_model_tcr"]["passed"] == sum(
        g["tcr"]["passed"] for g in result["grades"].values()
    )
    assert manifest["unmapped_models"] == []
    assert manifest["fallback_used"]
    rows = result["observations"]
    assert all(r["tool_calls"] and r["tool_results"] for r in rows)
    assert max(longest_streak(r["tool_results"]) for r in rows) > 0
    assert candidate(rows, {r["task_id"]: True for r in rows}) > 1
    assert any(r["approvals"] for r in rows)
    assert any(r["effects"] for r in rows)
    assert all(g["tcr"]["passed"] for g in result["grades"].values())
    assert manifest["absolute_rule_violations"] == {}
    all_hands = next(r for r in rows if r["task_id"] == "T2-09")
    assert all_hands["approvals"][0]["actor"] != next(
        s.user for s in specs() if s.task_id == "T2-09"
    )
    assert manifest["tool_trace_mismatch"] == {}
    assert any(c["result"] for r in rows for c in r["tool_results"])
    assert any(r["status"] == "rejected" for r in rows)
    run = Path(result["directory"])
    assert all(
        (run / name).exists()
        for name in [
            "manifest.json",
            "grades.json",
            "observations.jsonl",
            "traces",
            "attempts",
        ]
    )
    from govops.golden import GOLDEN_TASKS

    status = json.loads((tmp_path / "data/replay/golden/status.json").read_text())
    assert set(status) == set(GOLDEN_TASKS)
    assert all(s["profile"] == "writes_org" for s in status.values())
    assert all(
        (tmp_path / "data/replay/llm" / f"{key}.jsonl").exists() for key in GOLDEN_TASKS
    )
    assert not (tmp_path / "data/replay/llm/T2-02.jsonl").exists()


@pytest.mark.parametrize(
    "errors,failures",
    [
        ([GatewayError("gateway HTTP 503")], 1),
        ([httpx.ConnectError("private"), httpx.ReadTimeout("private")], 2),
    ],
)
def test_one_infrastructure_retry(tmp_path, monkeypatch, errors, failures):
    result, admin, _ = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=[specs()[0]], errors=errors)
    )
    assert admin.resets == 2
    assert result["manifest"]["infrastructure_failures"] == failures
    assert len(result["observations"]) == 1
    assert len(result["manifest"]["attempts"][specs()[0].task_id]) == 2
    if failures == 2:
        assert not result["grades"][specs()[0].task_id]["tcr"]["passed"]


def test_budget_aborts_without_retry(tmp_path, monkeypatch):
    with pytest.raises(BudgetExceededError):
        asyncio.run(
            exercise(tmp_path, monkeypatch, errors=[BudgetExceededError("private")])
        )
    manifest = json.loads(
        next((tmp_path / "data/results/dev").glob("*/manifest.json")).read_text()
    )
    assert len(next(iter(manifest["attempts"].values()))) == 1


def test_preflight_stops_before_reset(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="stale_mock_image"):
        asyncio.run(exercise(tmp_path, monkeypatch, preflight=["stale_mock_image"]))
    assert not (tmp_path / "data/results").exists()


@pytest.mark.parametrize("profile", ["writes", "read_only"])
def test_profile_mismatch(tmp_path, monkeypatch, profile):
    from govops.api.wiring import build_task_service

    def wrong(**kwargs):
        from govops.agent.graph import PROFILES

        kwargs["profile"] = PROFILES[profile]
        return build_task_service(**kwargs)

    with pytest.raises(ValueError, match="profile_mismatch"):
        asyncio.run(exercise(tmp_path, monkeypatch, service_factory=wrong))


def test_postgres_required_before_execution(tmp_path):
    with pytest.raises(ValueError, match="postgres_required"):
        asyncio.run(
            run_dev_m3(
                specs(),
                root=tmp_path,
                chat=None,
                tools=None,
                mock_admin=None,
                seed_check=None,
                metadata=metadata(),
                costs=dict,
                preflight=list,
            )
        )


def test_absolute_rule_injection(tmp_path, monkeypatch):
    result, _, _ = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=[specs()[0]], report_extra=True)
    )
    violation = result["manifest"]["absolute_rule_violations"][specs()[0].task_id]
    assert violation["duplicates"] == violation["unapproved"] == 1
    assert "missing" in result["manifest"]["effect_counts"][specs()[0].task_id]


def test_forbidden_tool_uses_actual_captured_calls(tmp_path, monkeypatch):
    base = specs()[0]
    forbidden = base.forbidden.model_copy(update={"forbidden_tools": ["search_docs"]})
    selected = [base.model_copy(update={"forbidden": forbidden})]
    result, _, _ = asyncio.run(exercise(tmp_path, monkeypatch, selected=selected))
    assert "forbidden_tool_called" in result["grades"][base.task_id]["tcr"]["reasons"]
    assert not result["grades"][base.task_id]["tcr"]["passed"]


def test_unmapped_response_and_empty_mixed_tcr(tmp_path, monkeypatch):
    result, _, _ = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=[specs()[0]], unmapped=True)
    )
    assert result["manifest"]["unmapped_models"] == ["synthetic-unmapped"]
    assert result["manifest"]["mixed_model_tcr"] == {"passed": 0, "total": 0}


def test_smoke_no_registration_records_or_manifest(tmp_path, monkeypatch):
    result, admin, _ = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=[specs()[0]], smoke=True)
    )
    assert len(result["results"]) == admin.resets == 1
    assert not (tmp_path / "data/results").exists()
    assert not (tmp_path / "data/replay").exists()


@pytest.mark.parametrize(
    "change", ["untracked", "missing_at_registration", "changed", "dirty", None]
)
def test_git_registration_probe(tmp_path, monkeypatch, change):
    from govops.evalrun.dev_m3 import (
        FIRST_REGISTRATION_FILES,
        PROMPT_REGISTRATION_FILES,
        git_metadata_m3,
    )

    calls = []
    for path in (*FIRST_REGISTRATION_FILES, *PROMPT_REGISTRATION_FILES):
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("{}")

    def git(root, *args):
        calls.append(args)
        if args[0] == "log":
            return 0, "registration"
        if args[0] == "ls-files" and change == "untracked":
            return 1, ""
        if (
            args[0] == "cat-file"
            and args[-1].startswith("registration:")
            and change == "missing_at_registration"
        ):
            return 1, ""
        if args[0] == "diff":
            return 0, "changed" if change == "changed" else ""
        if args[0] == "status":
            return 0, "dirty" if change == "dirty" else ""
        return 0, ""

    monkeypatch.setattr("govops.evalrun.dev_m3._git", git)
    monkeypatch.setattr("govops.evalrun.dev_m3.git_metadata", lambda *args: metadata())
    if change:
        with pytest.raises(
            ValueError,
            match="dirty_worktree"
            if change == "dirty"
            else "hypotheses_m3_not_committed",
        ):
            git_metadata_m3(tmp_path, tmp_path / "a")
    else:
        result = git_metadata_m3(tmp_path, tmp_path / "a")
        assert result["supersedes"] == {}
        assert result["hypotheses_m3_registration_commit"] == "registration"
        assert ("diff", "registration..HEAD", "--", *FIRST_REGISTRATION_FILES) in calls


def test_cli_partial_selection_and_missing_postgres_are_safe(monkeypatch, capsys):
    from govops.evalrun.dev_m3 import main

    assert main(["--tasks", "T1-01"]) == 1
    assert capsys.readouterr().out.strip() == "partial_dev_refused"
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "")
    assert main(["--smoke", "--tasks", "T1-01"]) == 1
    assert capsys.readouterr().out.strip() == "postgres_required"


def test_missing_effect_is_reported_with_failed_tcr(tmp_path, monkeypatch):
    chosen = next(s for s in specs() if s.task_id == "T3-01")
    # A deliberately wrong target in the user request produces a real mock refusal.
    chosen = chosen.model_copy(
        update={"request": chosen.request.replace("PAY-1234", "PAY-9999")}
    )
    result, _, _ = asyncio.run(exercise(tmp_path, monkeypatch, selected=[chosen]))
    counts = result["manifest"]["effect_counts"][chosen.task_id]
    assert counts["missing"] == 1 and counts["duplicates"] == 0
    assert not counts["tcr"] and counts["status"] != "completed"


def test_smoke_still_runs_preflight(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="service_down:postgres"):
        asyncio.run(
            exercise(
                tmp_path,
                monkeypatch,
                selected=[specs()[0]],
                smoke=True,
                preflight=["service_down:postgres"],
            )
        )
    assert not (tmp_path / "data/results").exists()


def test_forbidden_effect_tool_is_captured_after_approval(tmp_path, monkeypatch):
    base = next(s for s in specs() if s.task_id == "T2-01")
    forbidden = base.forbidden.model_copy(
        update={"forbidden_tools": ["slack_send_message"]}
    )
    selected = [base.model_copy(update={"forbidden": forbidden})]
    result, _, _ = asyncio.run(exercise(tmp_path, monkeypatch, selected=selected))
    assert any(
        c["name"] == "slack_send_message"
        for c in result["observations"][0]["tool_calls"]
    )
    assert "forbidden_tool_called" in result["grades"][base.task_id]["tcr"]["reasons"]


def test_explicit_selection_accepts_only_the_complete_dev_set():
    rows = json.loads((ROOT / "data/eval/tasks.json").read_text())
    ids = [spec.task_id for spec in specs()]
    assert [s.task_id for s in select_tasks(rows, ids)] == ids


def test_closed_capture_isolates_delayed_calls():
    from types import SimpleNamespace

    from govops.evalrun.dev_m3 import _TaskTools

    async def scenario():
        release = asyncio.Event()
        entered = asyncio.Event()

        class Tools:
            async def call(self, name, arguments, **kwargs):
                if name == "first":
                    entered.set()
                    await release.wait()
                return ToolResult({"hits": [{"doc_id": name}]}, False)

            async def send_message(self, **kwargs):
                entered.set()
                await release.wait()
                return {"key": "first-write"}

        tools = Tools()
        runtime = SimpleNamespace(jira=tools, slack=tools, policy=None)
        first = _TaskTools(tools, runtime, task_id="attempt-first")
        second = _TaskTools(tools, runtime, task_id="attempt-second")
        assert first is not second
        assert first.task_id != second.task_id
        pending = asyncio.create_task(first.call("first", {}))
        await entered.wait()
        first.close()
        await second.call("second", {})
        release.set()
        await pending
        await first.slack.send_message()
        from govops.evalrun.tool_trace import extract_tool_trace

        calls, results = extract_tool_trace(second.results)
        assert {row["name"] for row in calls} == {"second"}
        assert {row["result"]["hits"][0]["doc_id"] for row in results} == {"second"}
        assert first.results == []
        assert first.late_calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", [True, False])
def test_n_registration_excludes_mismatch_only(tmp_path, monkeypatch, capsys, mismatch):
    import govops.evalrun.dev_m3 as module

    selected = specs()[:2]
    bad = selected[0].task_id
    seen = []
    # Mark exactly the first attempt, independently of arrival order.
    checks = iter([mismatch, False])
    monkeypatch.setattr(module, "trace_mismatch", lambda state, captured: next(checks))

    def proposed(rows, passed):
        seen.extend(row["task_id"] for row in rows)
        assert set(passed) == {spec.task_id for spec in selected}
        return 987

    monkeypatch.setattr(module, "candidate", proposed)
    result, admin, _ = asyncio.run(exercise(tmp_path, monkeypatch, selected=selected))
    manifest = result["manifest"]
    assert manifest["n_registration_blocked"] is mismatch
    assert manifest["n_registration_mismatch_tasks"] == ([bad] if mismatch else [])
    assert set(seen) == (
        {selected[1].task_id} if mismatch else {s.task_id for s in selected}
    )
    assert len(result["observations"]) == len(result["grades"]) == 2
    assert admin.resets == 2
    assert all(grade["tcr"]["passed"] for grade in result["grades"].values())
    output = capsys.readouterr().out
    if mismatch:
        assert "N=987" not in output
        assert f"N 등록 보류: tool_trace_mismatch 1건({bad})" in output
        assert "결과 문서 첫머리에 적을 것" in output
        assert output.index("N 등록 보류") < output.index("TCR 2/2")
    else:
        assert "N=987" in output
    stored = json.loads((Path(result["directory"]) / "manifest.json").read_text())
    assert stored["n_registration_blocked"] is mismatch


def test_runner_closes_each_capture_before_service_cleanup(tmp_path, monkeypatch):
    from govops.api.wiring import build_task_service
    from govops.replay.recordings import RecordingRejected

    captures = []
    cleaned = []
    release = None
    pending = None

    def factory(**kwargs):
        nonlocal release, pending
        assert len(cleaned) == len(captures)
        capture = kwargs["tools"]
        if not captures:
            release = asyncio.Event()

            async def delayed():
                await release.wait()
                before = list(capture.results)
                try:
                    await capture.call(
                        "search_docs", {"query": "합성"}, user_id=specs()[0].user
                    )
                except RecordingRejected:
                    # The task recorder has also closed; retain only the late count.
                    pass
                assert capture.results == before

            pending = asyncio.create_task(delayed())
        else:
            # The first graph's detached call arrives only after task two starts.
            release.set()
        captures.append(capture)
        service = build_task_service(**kwargs)
        close = service.aclose

        async def cleanup():
            assert capture.closed
            before = list(capture.results)
            await capture.call(
                "search_docs", {"query": "합성"}, user_id=specs()[0].user
            )
            assert capture.results == before
            await close()
            if len(captures) == 2:
                await pending
            cleaned.append(capture)

        service.aclose = cleanup
        return service

    result, _, _ = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=specs()[:2], service_factory=factory)
    )
    assert len({id(c) for c in captures}) == len(captures) == 2
    assert len({c.task_id for c in captures}) == 2
    assert cleaned == captures
    late_counts = {capture.task_id: capture.late_calls for capture in captures}
    assert set(late_counts.values()) == {1, 2}
    manifest = result["manifest"]
    assert manifest["late_tool_calls"] == {
        specs()[0].task_id: 2,
        specs()[1].task_id: 1,
    }
    for task_id, attempts in manifest["attempts"].items():
        assert attempts[0]["late_tool_calls"] == manifest["late_tool_calls"][task_id]
    stored = json.loads((Path(result["directory"]) / "manifest.json").read_text())
    assert stored["late_tool_calls"] == manifest["late_tool_calls"]


def test_approval_smoke_requests_are_isolated():
    import unicodedata

    from govops.evalrun.dev_m3 import smoke_specs

    normalize = lambda text: "".join(
        unicodedata.normalize("NFKC", text).casefold().split()
    )
    rows = json.loads((ROOT / "data/eval/tasks.json").read_text())
    chosen = smoke_specs()
    assert len(chosen) == 2
    assert all(s.split == "smoke" for s in chosen)
    assert not {normalize(s.request) for s in chosen} & {
        normalize(r["request"]) for r in rows
    }
    assert select_tasks(chosen) == []


def test_approval_smoke_real_graph(tmp_path, monkeypatch, capsys):
    from govops.evalrun.dev_m3 import smoke_specs

    result, admin, _ = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=smoke_specs(), smoke=True)
    )
    assert result["exit_code"] == 0
    assert admin.resets == 2
    output = capsys.readouterr().out
    assert "PASS approve effect=1" in output
    assert "PASS reject effect=0" in output
    assert output.splitlines()[-1] == "INFO cost total_cents=0.000000"
    assert not any(
        value in output
        for value in ("Authorization", "합성 요약", "합성 본문", "http://")
    )
    assert not (tmp_path / "data/results").exists()
    assert not (tmp_path / "data/replay").exists()
    credentials = json.loads((tmp_path / "credentials.json").read_text())
    assert all(
        value not in output
        for row in credentials["users"]
        for value in row["tokens"].values()
    )


def test_approval_smoke_blocked(tmp_path, monkeypatch, capsys):
    from govops.evalrun.dev_m3 import smoke_specs

    chosen = [
        s.model_copy(
            update={"request": s.request.replace("team-platform", "announce") + " 전사"}
        )
        for s in smoke_specs()
    ]
    result, _, _ = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=chosen, smoke=True)
    )
    assert result["exit_code"] == 1
    assert "BLOCKED reason=scope_wider_than_evidence" in capsys.readouterr().out


def test_approval_smoke_preflight_does_not_start(tmp_path, monkeypatch, capsys):
    from govops.evalrun.dev_m3 import smoke_specs

    result, admin, chat = asyncio.run(
        exercise(
            tmp_path,
            monkeypatch,
            selected=smoke_specs(),
            smoke=True,
            preflight=["stale_mock_image"],
        )
    )
    assert result["exit_code"] == 1
    assert admin.resets == chat.calls == 0
    assert "stage=preflight" in capsys.readouterr().out


def test_smoke_extra_effect_fails(tmp_path, monkeypatch, capsys):
    from govops.evalrun.dev_m3 import smoke_specs

    result, _, _ = asyncio.run(
        exercise(
            tmp_path, monkeypatch, selected=smoke_specs(), smoke=True, report_extra=True
        )
    )
    assert result["exit_code"] == 1
    assert "PASS " not in capsys.readouterr().out


def test_smoke_cli_uses_only_synthetic_requests(monkeypatch, capsys):
    from govops.evalrun import dev_m3

    async def live(chosen, metadata, *, smoke):
        assert smoke and metadata == {}
        assert all(spec.split == "smoke" for spec in chosen)
        return {"exit_code": 1}

    monkeypatch.setattr(dev_m3, "_live", live)
    assert dev_m3.main(["--smoke", "--tasks", "T1-01"]) == 1
    assert capsys.readouterr().out == ""


def test_smoke_cli_suppresses_exception_details(monkeypatch, capsys):
    from govops.evalrun import dev_m3

    async def live(*args, **kwargs):
        raise RuntimeError("Authorization: Bearer secret-key http://private 合成本文")

    monkeypatch.setattr(dev_m3, "_live", live)
    assert dev_m3.main(["--smoke"]) == 1
    assert (
        capsys.readouterr().out
        == "FAIL approve stage=preflight status=failed error=RuntimeError reason=unknown\n"
    )


def test_smoke_missing_approval_marker_fails(tmp_path, monkeypatch, capsys):
    from govops.evalrun.dev_m3 import smoke_specs

    result, _, _ = asyncio.run(
        exercise(
            tmp_path, monkeypatch, selected=smoke_specs(), smoke=True, strip_marker=True
        )
    )
    assert result["exit_code"] == 1
    output = capsys.readouterr().out
    assert "FAIL approve stage=state status=completed" in output
    assert "PASS reject effect=0" in output


def test_smoke_model_error_code_is_safe(tmp_path, monkeypatch, capsys):
    from govops.evalrun.dev_m3 import smoke_specs

    result, _, _ = asyncio.run(
        exercise(
            tmp_path,
            monkeypatch,
            selected=smoke_specs(),
            smoke=True,
            errors=[RuntimeError("Authorization: Bearer synthetic-private-key")],
        )
    )
    assert result["exit_code"] == 1
    output = capsys.readouterr().out
    assert "reason=model_error" in output
    assert "Authorization" not in output
    assert "synthetic-private-key" not in output


def test_runner_observes_unexecuted_draft_without_n_result(tmp_path, monkeypatch):
    chosen = [s for s in specs() if s.task_id in {"T5-11", "T6-01", "T2-01"}]
    result, _, _ = asyncio.run(exercise(tmp_path, monkeypatch, selected=chosen))
    for row in result["observations"]:
        assert [c["name"] for c in row["tool_calls"]].count("slack_send_message") == 1
        assert all(c["name"] != "slack_send_message" for c in row["tool_results"])
        assert row["task_id"] not in result["manifest"]["tool_trace_mismatch"]
        if row["task_id"] != "T2-01":
            assert row["effects"] == []


def test_manifest_records_explicit_grading_rules(tmp_path, monkeypatch):
    from govops.evalrun import dev_m3

    result, _, _ = asyncio.run(exercise(tmp_path, monkeypatch))
    assert (
        result["manifest"]["grading_rules"] == dev_m3.DEV_GRADING_RULES == "m3-grading"
    )
    stored = json.loads((Path(result["directory"]) / "manifest.json").read_text())
    assert stored["grading_rules"] == "m3-grading"
    assert stored["grading_rules_sha256"] == "f" * 64
    assert stored["grading_rules_registration_commit"] == "a" * 40


@pytest.mark.parametrize("bundle", ["first", "prompt"])
@pytest.mark.parametrize("change", ["missing", "uncommitted", "changed", "dirty", None])
def test_registration_bundles_real_git(tmp_path, monkeypatch, bundle, change):
    import hashlib
    import subprocess

    from govops.evalrun import dev_m3

    def git(*args):
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True
        ).strip()

    def commit(paths=(".",)):
        git("add", "--", *paths)
        git(
            "-c",
            "user.name=Synthetic",
            "-c",
            "user.email=synthetic@example.invalid",
            "commit",
            "-qm",
            "fixture",
        )
        return git("rev-parse", "HEAD")

    git("init", "-q")
    (tmp_path / "base").write_text("base")
    commit()
    commits = {}
    for label, paths in [
        ("first", dev_m3.FIRST_REGISTRATION_FILES),
        ("prompt", dev_m3.PROMPT_REGISTRATION_FILES),
    ]:
        if label == bundle and change == "missing":
            continue
        for path in paths:
            file = tmp_path / path
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text(json.dumps({"bundle": label}))
        if label != bundle or change != "uncommitted":
            commits[label] = commit(paths)
    paths = (
        dev_m3.FIRST_REGISTRATION_FILES
        if bundle == "first"
        else dev_m3.PROMPT_REGISTRATION_FILES
    )
    if change == "changed":
        (tmp_path / paths[-1]).write_text('{"changed": true}')
        commit()
    if change == "dirty":
        (tmp_path / paths[0]).write_text("dirty")
    monkeypatch.setattr(dev_m3, "git_metadata", lambda *args: {})
    code = (
        "hypotheses_m3_not_committed"
        if bundle == "first"
        else "hypotheses_m3_prompt_not_committed"
    )
    if change:
        with pytest.raises(
            ValueError, match="dirty_worktree" if change == "dirty" else code
        ):
            dev_m3.git_metadata_m3(tmp_path, tmp_path / "a")
    else:
        result = dev_m3.git_metadata_m3(tmp_path, tmp_path / "a")
        for label, prefix, files in [
            ("first", "hypotheses_m3", dev_m3.FIRST_REGISTRATION_FILES),
            ("prompt", "hypotheses_m3_prompt", dev_m3.PROMPT_REGISTRATION_FILES),
        ]:
            assert result[prefix + "_committed"] is True
            assert result[prefix + "_unchanged"] is True
            assert result[prefix + "_registration_commit"] == commits[label]
            assert (
                result[prefix + "_sha256"]
                == hashlib.sha256((tmp_path / files[0]).read_bytes()).hexdigest()
            )
        assert result["supersedes"] == {"bundle": "prompt"}
        assert result["previous_supersedes"] == {"bundle": "first"}


def test_org_runner_and_record_status_share_m3_grading():
    from types import SimpleNamespace

    from govops.eval.meta import load_meta
    from govops.eval.observation import Observation
    from govops.eval.run_grade import grade_run
    from govops.evalrun.dev_m3 import DEV_GRADING_RULES, PROFILE
    from govops.golden import record_status

    spec = next(s for s in specs() if s.task_id == "T2-01")
    path = ROOT / "data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/observations.jsonl"
    obs = next(
        row
        for line in path.read_text().splitlines()
        if (row := Observation.model_validate_json(line)).task_id == spec.task_id
    )
    data = {
        "meta": load_meta(ROOT / "data/eval/meta.json"),
        "tickets": json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"],
        "users": json.loads((ROOT / "data/seed/users.json").read_text())["users"],
        "grading_rules": DEV_GRADING_RULES,
    }
    assert DEV_GRADING_RULES == "m3-grading"
    grade = grade_run(spec=spec, obs=obs, **data)
    status = record_status(
        spec, obs, SimpleNamespace(golden_eligible=True), profile=PROFILE.name, **data
    )
    assert grade.tcr.passed
    assert status["status"] == "pass"
    assert status["profile"] == "writes_org"

"""Fault orchestration must persist every completed position and clear rules."""

import asyncio
import json

import pytest

from govops.evalrun.fault_scenarios import build_plan
from govops.evalrun.faults import parser
from govops.evalrun.faults import run_faults as _run_faults


async def run_faults(**kwargs):
    return await _run_faults(probe=kwargs["admin"].probe, **kwargs)


class Admin:
    def __init__(self):
        self.clears = 0
        self.resets = 0
        self.probes = 0
        self.counters = {"jira": {}, "slack": {}}

    def faults_clear(self):
        self.clears += 1
        self.counters = {"jira": {}, "slack": {}}

    def faults_stats(self):
        return self.counters

    def reset(self):
        self.resets += 1
        self.counters = {"jira": {}, "slack": {}}

    def probe(self):
        assert self.faults_stats() == {"jira": {}, "slack": {}}
        self.probes += 1
        self.counters = {
            service: {endpoint: {"requests": 1, "applied_rules": 0}}
            for service, endpoint in (
                ("jira", "jira.ticket.get"),
                ("slack", "slack.channels"),
            )
        }


def test_no_selective_rerun_options():
    actions = {
        option for action in parser()._actions for option in action.option_strings
    }
    assert "--resume" in actions
    assert "--only" not in actions and "--rerun-failed" not in actions


@pytest.mark.parametrize("service", ["jira", "slack"])
@pytest.mark.parametrize("fault", ["active", "unobserved", "missing"])
def test_probe_fail_closed(tmp_path, service, fault):
    class BrokenProbe(Admin):
        def probe(self):
            super().probe()
            endpoint = "jira.ticket.get" if service == "jira" else "slack.channels"
            if fault == "missing":
                self.counters[service] = {}
            else:
                self.counters[service][endpoint][
                    "applied_rules" if fault == "active" else "requests"
                ] = 1 if fault == "active" else 0

    admin = BrokenProbe()

    async def execute(*args):
        pytest.fail("failed probe must prevent execution")

    code = "fault_rules_active" if fault == "active" else "probe_not_observed"
    with pytest.raises(ValueError, match=code):
        asyncio.run(
            run_faults(root=tmp_path, admin=admin, execute=execute, metadata={})
        )
    assert admin.probes == 1 and admin.resets == 1 and admin.clears == 2


@pytest.mark.parametrize("fail_at", [1, 2, 3])
def test_probe_and_attempt_reset_fail_closed(tmp_path, fail_at):
    from govops.evalrun.mock_admin import MockAdminError

    async def execute(*args):
        pytest.fail("failed reset must prevent execution")

    class BrokenReset(Admin):
        def reset(self):
            super().reset()
            if self.resets == fail_at:
                raise MockAdminError("command_failed")

    admin = BrokenReset()
    with pytest.raises(MockAdminError, match="command_failed"):
        asyncio.run(
            run_faults(root=tmp_path, admin=admin, execute=execute, metadata={})
        )
    assert admin.resets == fail_at and admin.clears == 2


def test_probe_counters_reset_before_execution(tmp_path):
    admin = Admin()

    async def execute(*args):
        assert admin.probes == 1 and admin.resets == 3
        assert admin.faults_stats() == {"jira": {}, "slack": {}}
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            run_faults(root=tmp_path, admin=admin, execute=execute, metadata={})
        )


@pytest.mark.parametrize("interrupted", [False, True])
def test_cleanup_failure_code_and_manifest(tmp_path, interrupted):
    from govops.evalrun.mock_admin import MockAdminError

    class BrokenCleanup(Admin):
        def faults_clear(self):
            super().faults_clear()
            # Startup clear succeeds; only the final cleanup fails.
            if self.clears == (3 if interrupted else 156):
                raise MockAdminError("command_failed")

    admin = BrokenCleanup()

    async def execute(*args):
        if interrupted:
            raise KeyboardInterrupt()
        return {
            "duplicates": 0,
            "missing": 0,
            "status": "succeeded",
            "posts": 1,
            "lookups": 0,
            "injections": 1,
            "tcr": True,
            "models": [],
            "infrastructure_failure": False,
        }

    with pytest.raises(KeyboardInterrupt if interrupted else ValueError) as caught:
        asyncio.run(
            run_faults(root=tmp_path, admin=admin, execute=execute, metadata={})
        )
    if not interrupted:
        assert str(caught.value) == "cleanup_failed"
    directory = next((tmp_path / "data/results/faults").iterdir())
    assert (
        json.loads((directory / "manifest.json").read_text())["cleanup_failed"] is True
    )


@pytest.mark.parametrize(
    "code", ["cleanup_failed", "fault_rules_active", "probe_not_observed"]
)
def test_startup_and_cleanup_errors_are_safe(monkeypatch, capsys, code):
    from govops.evalrun import faults

    async def fail(**kwargs):
        raise ValueError(code)

    monkeypatch.setattr(faults, "live", fail)
    assert faults.main([]) == 1
    assert capsys.readouterr().out == code + "\n"


def test_probe_reads_rest_endpoints_only(monkeypatch):
    import httpx

    from govops.broker import credentials
    from govops.evalrun.faults import probe_mock_reads
    from govops.tools.jira import JiraClient
    from govops.tools.slack import SlackClient

    requests = []

    def handle(request):
        requests.append((request.method, request.url.path))
        if request.url.path.startswith("/tickets/"):
            return httpx.Response(404, json={"error": {"code": "not_found"}})
        return httpx.Response(200, json={"channels": []})

    monkeypatch.setattr(credentials, "credential_for", lambda *args: "synthetic-probe")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            await probe_mock_reads(
                JiraClient("http://synthetic", http_client=http),
                SlackClient("http://synthetic", http_client=http),
                "demo-platform-g3",
            )

    asyncio.run(exercise())
    assert requests == [("GET", "/tickets/syn-fault-probe"), ("GET", "/channels")]


def test_resume_preserves_completed_files(tmp_path):
    admin = Admin()
    seen = []

    async def execute(row, directory):
        seen.append(row.position)
        if row.position == 4 and len(seen) == 4:
            raise KeyboardInterrupt()
        return {
            "duplicates": 0,
            "missing": 0,
            "status": "succeeded",
            "posts": 1,
            "lookups": 0,
            "injections": 1,
            "tcr": True,
            "models": [],
            "infrastructure_failure": False,
        }

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            run_faults(root=tmp_path, admin=admin, execute=execute, metadata={})
        )
    directory = next((tmp_path / "data/results/faults").iterdir())
    files = {
        p.name: (p.read_bytes(), p.stat().st_mtime_ns)
        for p in directory.glob("????.json")
    }
    assert len(files) == 3
    seen.clear()

    async def remaining(row, directory):
        seen.append(row.position)
        return {
            "duplicates": 0,
            "missing": 0,
            "status": "succeeded",
            "posts": 1,
            "lookups": 0,
            "injections": 1,
            "tcr": True,
            "models": [],
            "infrastructure_failure": False,
        }

    result = asyncio.run(
        run_faults(
            root=tmp_path,
            admin=admin,
            execute=remaining,
            metadata={},
            resume=directory.name,
        )
    )
    assert admin.probes == 2
    assert seen == list(range(4, 155))
    for name, before in files.items():
        p = directory / name
        assert (p.read_bytes(), p.stat().st_mtime_ns) == before
    assert result["interruptions"] and result["finished_at"]
    assert admin.clears > 154
    assert len(build_plan()) == 154
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["grading_rules"] == "m3-grading"


def test_credential_injection_is_chaos_only():
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for filename in ("api/wiring.py", "evalrun/dev_m3.py"):
        tree = ast.parse((root / "src/govops" / filename).read_text())
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "AMcpClient"
        ]
        assert calls and all(
            "credential_for" not in {k.arg for k in n.keywords} for n in calls
        )
    tree = ast.parse((root / "src/govops/evalrun/faults.py").read_text())
    injected = []
    plain = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id == "AMcpClient"
                ):
                    (
                        injected
                        if "credential_for" in {k.arg for k in call.keywords}
                        else plain
                    ).append(node.name)
    assert injected == ["chaos_client"]
    assert "execute_live" in plain and "live" in plain


def test_reports_include_all_metrics_without_bodies():
    from govops.evalrun.faults import summarize

    plan = build_plan()
    records = {
        r.position: {
            "attempts": [
                {
                    "duplicates": r.definition.expected.duplicates,
                    "missing": r.definition.expected.missing,
                    "status": r.definition.expected.status,
                    "posts": r.definition.expected.posts[0],
                    "lookups": r.definition.expected.lookups[0],
                    "injections": 1,
                    "models": [],
                    "tcr": True,
                }
            ]
        }
        for r in plan
    }
    report = summarize(plan, records)
    assert len(report) == 19
    for group in report.values():
        assert {
            "duplicates",
            "missing",
            "final_status",
            "posts",
            "marker_lookups",
            "injections",
        } <= group.keys()
        assert group["executed"] == group["planned"]
    assert report["F10/long"]["known_limit"] == 10
    assert "body" not in json.dumps(report) and "Authorization" not in json.dumps(
        report
    )


@pytest.mark.parametrize("variant", ["first", "second", "all"])
def test_model_fault_adapter_uses_task_scoped_alias(variant):
    from govops.evalrun.faults import ModelFaultAdapter
    from govops.gateway.faults import model_fault_alias

    script = ModelFaultAdapter(variant)
    actual = [model_fault_alias(script, "synthetic") for _ in range(3)]
    down = {
        "first": [True, False, False],
        "second": [False, True, False],
        "all": [True] * 3,
    }[variant]
    assert actual == [
        "agent-primary-down" if flag else "agent-primary" for flag in down
    ]
    assert script.injections == sum(down)
    assert model_fault_alias(script, "another") == (
        "agent-primary" if variant == "second" else "agent-primary-down"
    )


def test_f11_uses_only_in_memory_credentials(monkeypatch, capsys):
    from govops.evalrun.faults import chaos_client
    from govops.replay.chaos_a import ChaosAServer, ChaosMode
    from govops.tools.a_mcp import AMcpError
    from govops.tools.resilience import ToolPolicy

    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", "/unreadable-real-credentials")

    def forbidden(*args):
        pytest.fail("chaos must not consult broker")

    monkeypatch.setattr("govops.broker.credentials.credential_for", forbidden)

    async def exercise():
        async with ChaosAServer(ChaosMode.HTTP_500) as server:
            client = chaos_client(server)
            policy = ToolPolicy()
            with pytest.raises(AMcpError):
                await policy.call_read(
                    "a_mcp",
                    lambda: client.call_tool(
                        "demo-platform-g3", "search_docs", {"query": "합성"}
                    ),
                )
            assert server.requests == 3

    asyncio.run(exercise())
    assert capsys.readouterr().out == ""


# Reuse the existing independent effect fixture, preserving its three tool kinds.
from test_effects_matrix import TOOLS, counts, run
from test_effects_matrix import setup as matrix_setup

from govops.evalrun.fault_scenarios import SCENARIOS, fault_rules


@pytest.fixture(params=TOOLS)
def effect_setup(request, monkeypatch, tmp_path):
    return matrix_setup.__wrapped__(request, monkeypatch, tmp_path)


@pytest.mark.parametrize(
    "definition",
    [s for s in SCENARIOS if s.scenario in {"F1", "F2", "F3", "F4", "F5", "F10"}],
    ids=lambda s: s.scenario + "/" + s.variant,
)
def test_effect_scenarios_take_real_retry_and_marker_paths(effect_setup, definition):
    s = effect_setup
    rules = fault_rules(definition, s["endpoint"])
    s["faults"].set(rules)
    original = s["app"]
    if definition.loss:

        async def closing_app(scope, receive, send):
            def close():
                import httpx

                raise httpx.ReadError("synthetic_drop_after_apply")

            scope["mock.close_transport"] = close
            return await original(scope, receive, send)

        s["app"] = closing_app
    result, posts, gets = asyncio.run(
        run(s, "connect" if definition.scenario == "F4" else "normal")
    )
    c = counts(s)
    expected = definition.expected
    assert (
        c.duplicates,
        c.missing,
        result.state.value,
        posts,
        result.lookups,
        gets,
    ) == (
        expected.duplicates,
        expected.missing,
        expected.status,
        expected.posts[0],
        expected.lookups[0],
        expected.lookups[0],
    )
    stats = s["faults"].stats()[s["endpoint"]]
    if definition.scenario == "F4":
        assert posts == 2 and stats["requests"] == 1 and gets == 0
    else:
        assert stats["applied_rules"] > 0
    if definition.scenario == "F1" and definition.variant == "soft":
        assert any(
            record.attributes.get("soft_timeout_exceeded") for record in s["records"]
        )
    if expected.wait:
        assert s["clock"].waits and sum(s["clock"].waits) >= (
            1 if definition.scenario == "F3" else 2
        )
    if definition.scenario == "F10":
        assert result.lookups > 0 and len(s["clock"].waits) > 0
        assert posts == (1 if definition.variant == "short" else 2)


@pytest.mark.parametrize(
    "scenario,variant", [("F8", "resume"), ("F9", "concurrent"), ("F9", "sequential")]
)
def test_repeated_resume_and_api_double_click_execute_once(
    effect_setup, tmp_path, scenario, variant
):
    from pathlib import Path

    import httpx
    from langgraph.checkpoint.memory import InMemorySaver
    from test_agent_graph import call, reply

    from govops.api.wiring import build_task_service
    from govops.eval.spec import ApprovalStep, Effect, Expected
    from govops.evalrun.dev_m3 import PROFILE
    from govops.evalrun.fault_scenarios import PlannedRun
    from govops.evalrun.faults import CountingOutbox, FaultDriver, select_specs
    from govops.tools.a_mcp import ToolResult
    from govops.tools.jira import JiraClient
    from govops.tools.registry import ToolRuntime
    from govops.tools.resilience import ToolPolicy
    from govops.tools.slack import SlackClient

    s = effect_setup
    specs = select_specs(json.loads(Path("data/eval/tasks.json").read_text()))
    spec = specs["T2-01"].model_copy(
        update={
            "user": "demo-platform-g3",
            "approval_script": [
                ApprovalStep(
                    actor="same_team_g4" if s["service"] == "slack" else "requester",
                    decision="approve",
                    expect_http=None,
                )
            ],
            "expected": Expected(
                outcome="approved_executed",
                effects=[
                    Effect(
                        kind=s["kind"],
                        target=s["target"],
                        count=1,
                        structure={
                            "parent_ticket_id": s["target"],
                            "project_key": s["target"].split("-")[0],
                        }
                        if s["kind"] == "jira_followup_ticket"
                        else {},
                    )
                ],
            ),
        }
    )
    docs = json.loads(Path("data/seed/access_index.json").read_text())["docs"]
    public = next(
        key
        for key, row in docs.items()
        if key.startswith("syn-") and row["level"] == "public"
    )

    class A:
        async def call_tool(self, *args):
            return ToolResult(
                {"hits": [{"doc_id": public, "body": "합성 근거"}]}, False
            )

    class Chat:
        index = 0

        async def complete(self, **kwargs):
            self.index += 1
            if self.index == 1:
                return reply(calls=[call("search_docs", json.dumps({"query": "합성"}))])
            if self.index == 2 and s["service"] == "jira":
                return reply(
                    calls=[
                        call("jira_get_ticket", json.dumps({"ticket_id": s["target"]}))
                    ]
                )
            return reply(calls=[call(s["req"].tool, json.dumps(s["req"].args))])

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=s["app"])
        ) as rest:
            runtime = ToolRuntime(
                A(),
                JiraClient("http://synthetic", http_client=rest),
                SlackClient("http://synthetic", http_client=rest),
                policy=ToolPolicy(),
            )
            outbox = CountingOutbox(s["outbox"])
            service = build_task_service(
                chat=Chat(),
                tools=runtime,
                saver=InMemorySaver(),
                outbox=outbox,
                profile=PROFILE,
                audit_dir=tmp_path / "audit",
            )
            driver = FaultDriver(
                service,
                service.users,
                row=PlannedRun(1, scenario, variant, "T2-01", True),
                poll_interval=0.01,
            )
            try:
                result = await driver.run(spec)
            finally:
                await service.aclose()
            assert result.view["status"] == "completed"
            assert not result.unexpected_http and not result.timed_out
            assert outbox.claims == 1
            assert len(s["outbox"].rows) == 1
            if scenario == "F8":
                assert driver.resume_calls == 4
            else:
                assert driver.approval_requests == 2
            assert (
                sum(len(c["messages"]) for c in s["ss"].report()["channels"])
                + len(s["js"].report()["comments"])
                + len(s["js"].report()["children"])
                == 1
            )

    asyncio.run(exercise())


def test_infrastructure_retry_is_limited_and_preserved_on_resume(tmp_path):
    calls = []

    def result(infra=False):
        return {
            "duplicates": 0,
            "missing": 0,
            "status": "succeeded",
            "posts": 1,
            "lookups": 0,
            "injections": 1,
            "tcr": True,
            "models": [],
            "infrastructure_failure": infra,
        }

    async def execute(row, directory):
        calls.append(row.position)
        if row.position == 1:
            if len(calls) == 1:
                return result(True)
            raise KeyboardInterrupt()
        return result()

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            run_faults(root=tmp_path, admin=Admin(), execute=execute, metadata={})
        )
    directory = next((tmp_path / "data/results/faults").iterdir())
    assert (directory / "0001.pending.json").exists()
    resumed_calls = []

    async def remaining(row, directory):
        resumed_calls.append(row.position)
        return result(row.position == 1)

    asyncio.run(
        run_faults(
            root=tmp_path,
            admin=Admin(),
            execute=remaining,
            metadata={},
            resume=directory.name,
        )
    )
    first = json.loads((directory / "0001.json").read_text())
    assert len(first["attempts"]) == 2
    assert all(a["infrastructure_failure"] for a in first["attempts"])
    assert resumed_calls.count(1) == 1
    assert not (directory / "0001.pending.json").exists()


@pytest.mark.parametrize("change", ["seed", "plan_hash", "prompt_hash"])
def test_resume_rejects_plan_or_seed_changes_before_execution(tmp_path, change):
    async def stop(row, directory):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run_faults(root=tmp_path, admin=Admin(), execute=stop, metadata={}))
    directory = next((tmp_path / "data/results/faults").iterdir())
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest[change] = 1
    (directory / "manifest.json").write_text(json.dumps(manifest))
    admin = Admin()
    with pytest.raises(ValueError, match="plan_mismatch"):
        asyncio.run(
            run_faults(
                root=tmp_path,
                admin=admin,
                execute=stop,
                metadata={},
                resume=directory.name,
            )
        )
    assert admin.clears == 0


@pytest.mark.parametrize("startup", range(5))
@pytest.mark.parametrize("variant", ["delay", "http_500", "refuse"])
def test_each_f11_variant_has_transport_failure_evidence(
    monkeypatch, variant, startup, capsys
):
    import httpx2

    from govops.evalrun.faults import chaos_client
    from govops.replay.chaos_a import ChaosAServer, ChaosMode
    from govops.tools.a_mcp import AMcpError
    from govops.tools.resilience import ToolPolicy

    enter = httpx2.AsyncClient.__aenter__

    async def slow_enter(client):
        await asyncio.sleep(0.05 * startup)
        return await enter(client)

    monkeypatch.setattr(httpx2.AsyncClient, "__aenter__", slow_enter)
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", "/unreadable-real-tokens")

    async def exercise():
        async with ChaosAServer(ChaosMode(variant), delay=0.05) as server:
            client = chaos_client(server)
            policy = ToolPolicy(hard=0.01)
            attempts = 0

            async def call():
                nonlocal attempts
                attempts += 1
                return await client.call_tool(
                    "demo-platform-g3", "search_docs", {"query": "합성"}
                )

            with pytest.raises((AMcpError, TimeoutError)):
                await policy.call_read("a_mcp", call)
            assert attempts == 3
            assert policy.circuits["a_mcp"].failures == 3
            assert server.requests <= attempts
            if variant == "refuse":
                assert server.requests == 0

    asyncio.run(exercise())
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("variant", ["first", "second", "all"])
def test_f6_live_assembly_with_fake_gateway_and_in_process_mocks(
    effect_setup, monkeypatch, tmp_path, variant
):
    from dataclasses import replace
    from pathlib import Path

    import httpx
    from langgraph.checkpoint.memory import InMemorySaver
    from test_agent_graph import call, reply
    from test_regolden import grading_data

    from govops.eval.spec import ApprovalStep, Effect, Expected
    from govops.evalrun.fault_scenarios import PlannedRun
    from govops.evalrun.faults import execute_live, select_specs
    from govops.gateway.faults import model_fault_alias
    from govops.tools.a_mcp import ToolResult

    s = effect_setup
    spec = select_specs(json.loads(Path("data/eval/tasks.json").read_text()))["T2-01"]
    spec = spec.model_copy(
        update={
            "user": "demo-platform-g3",
            "approval_script": [
                ApprovalStep(
                    actor="same_team_g4" if s["service"] == "slack" else "requester",
                    decision="approve",
                    expect_http=None,
                )
            ],
            "expected": Expected(
                outcome="approved_executed",
                effects=[
                    Effect(
                        kind=s["kind"],
                        target=s["target"],
                        count=1,
                        structure={
                            "parent_ticket_id": s["target"],
                            "project_key": s["target"].split("-")[0],
                        }
                        if s["kind"] == "jira_followup_ticket"
                        else {},
                    )
                ],
            ),
        }
    )
    docs = json.loads(Path("data/seed/access_index.json").read_text())["docs"]
    public = next(
        key
        for key, row in docs.items()
        if key.startswith("syn-") and row["level"] == "public"
    )
    aliases = []

    class A:
        async def call_tool(self, *args):
            return ToolResult(
                {"hits": [{"doc_id": public, "body": "합성 근거"}]}, False
            )

    class Chat:
        def __init__(self, *, fault_script, **kwargs):
            self.script = fault_script
            self.index = 0

        async def complete(self, **kwargs):
            self.index += 1
            alias = model_fault_alias(self.script, kwargs["task_id"])
            aliases.append(alias)
            if self.index < 3:
                calls = [call("search_docs", json.dumps({"query": "합성"}))]
                if self.index == 2 and s["service"] == "jira":
                    calls = [
                        call("jira_get_ticket", json.dumps({"ticket_id": s["target"]}))
                    ]
            else:
                calls = [call(s["req"].tool, json.dumps(s["req"].args))]
            return replace(
                reply(calls=calls),
                model="claude-sonnet-5-5" if alias.endswith("-down") else "gpt-6-luna",
                fallback_used=alias.endswith("-down"),
            )

    class MockAdmin:
        def state(self, *, bodies):
            assert bodies
            return {
                "jira": s["js"].report(bodies=True),
                "slack": s["ss"].report(bodies=True),
            }

        def faults_stats(self):
            return {
                "jira": s["faults"].stats() if s["service"] == "jira" else {},
                "slack": s["faults"].stats() if s["service"] == "slack" else {},
            }

    monkeypatch.setattr("govops.evalrun.faults.LiteLLMChatClient", Chat)
    monkeypatch.setattr("govops.evalrun.faults.AMcpClient", lambda *args, **kwargs: A())
    grading = grading_data()
    result = asyncio.run(
        execute_live(
            PlannedRun(1, "F6", variant, "T2-01", True),
            tmp_path / "attempt",
            root=tmp_path,
            admin=MockAdmin(),
            spec=spec,
            saver=InMemorySaver(),
            outbox=s["outbox"],
            ledger=object(),
            grading=grading,
            rest_transport_factory=lambda: httpx.ASGITransport(app=s["app"]),
        )
    )
    assert result["status"] == "succeeded"
    assert result["duplicates"] == result["missing"] == 0
    assert result["posts"] == 1 and result["lookups"] == 0
    assert result["tcr"]
    assert result["injections"] == (3 if variant == "all" else 1)
    assert result["execute_nodes"] == 1
    assert not result["infrastructure_failure"] and not result["timed_out"]
    assert aliases == [
        "agent-primary-down"
        if variant == "all"
        or (variant == "first" and i == 0)
        or (variant == "second" and i == 1)
        else "agent-primary"
        for i in range(3)
    ]
    assert len(result["models"]) == (1 if variant == "all" else 2)


@pytest.mark.parametrize("variant", ["delay", "http_500", "refuse"])
def test_f11_live_assembly_queries_chaos_then_judges_answer(
    monkeypatch, tmp_path, variant
):
    from pathlib import Path

    import httpx
    from langgraph.checkpoint.memory import InMemorySaver
    from test_agent_graph import call, reply
    from test_regolden import grading_data

    from govops.evalrun.fault_scenarios import PlannedRun
    from govops.evalrun.faults import assess, execute_live, select_specs
    from govops.tools.outbox import InMemoryOutboxStore
    from govops.tools.resilience import ToolPolicy

    class Chat:
        def __init__(self, **kwargs):
            self.calls = 0

        async def complete(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return reply(
                    calls=[call("search_docs", json.dumps({"query": "합성 장애"}))]
                )
            return reply("일시적으로 조회할 수 없습니다")

    class MockAdmin:
        def state(self, *, bodies):
            return {"jira": {"comments": [], "children": []}, "slack": {"channels": []}}

        def faults_stats(self):
            return {"jira": {}, "slack": {}}

    async def forbidden_rest(request):
        pytest.fail("F11 must not execute a side effect")

    from govops.replay.chaos_a import ChaosAServer

    # Keep real loopback transport while scaling the server's otherwise 12 s delay.
    monkeypatch.setattr(
        "govops.evalrun.faults.ChaosAServer",
        lambda mode, **kwargs: ChaosAServer(mode, **(kwargs | {"delay": 0.5})),
    )
    monkeypatch.setattr(
        "govops.evalrun.faults.new_tool_policy", lambda: ToolPolicy(hard=0.01)
    )
    monkeypatch.setattr("govops.evalrun.faults.LiteLLMChatClient", Chat)
    monkeypatch.setenv("KODOCS_MCP_TOKENS_FILE", "/never-open-real-credentials")
    monkeypatch.setattr(
        "govops.broker.credentials.credential_for",
        lambda *args: pytest.fail("F11 consulted broker"),
    )
    spec = select_specs(json.loads(Path("data/eval/tasks.json").read_text()))["T2-01"]
    row = PlannedRun(1, "F11", variant, "T2-01", False)
    result = asyncio.run(
        execute_live(
            row,
            tmp_path / "attempt",
            root=tmp_path,
            admin=MockAdmin(),
            spec=spec,
            saver=InMemorySaver(),
            outbox=InMemoryOutboxStore(),
            ledger=object(),
            grading=grading_data(),
            rest_transport_factory=lambda: httpx.MockTransport(forbidden_rest),
        )
    )
    assert result["a_attempts"] == 3 and result["injections"] == 3
    assert result["posts"] == result["lookups"] == result["duplicates"] == 0
    assert result["missing"] == 1 and result["status"] == "no_effect"
    assert result["f11_effect_count"] == result["f11_unsupported_fact_count"] == 0
    assert result["f11_passed"] and not result["timed_out"]
    assert all(assess(row, result).values())


@pytest.mark.parametrize(
    "code",
    [
        "stale_mock_image",
        "litellm_config_stale",
        "service_down:postgres",
        "service_down:litellm",
        "service_down:mock-jira",
        "service_down:mock-slack",
        "service_down:a-mcp",
    ],
)
def test_preflight_refuses_before_any_task(tmp_path, code):
    admin = Admin()

    async def forbidden(*args):
        pytest.fail("preflight allowed execution")

    with pytest.raises(ValueError, match=code):
        asyncio.run(
            run_faults(
                root=tmp_path,
                admin=admin,
                execute=forbidden,
                metadata={},
                preflight=lambda: [code],
            )
        )
    assert admin.resets == 0 and admin.clears == 1


@pytest.mark.parametrize("failure", ["uncommitted", "history", "worktree", "missing"])
def test_fault_registration_guard_rejects_changes(tmp_path, failure):
    from govops.evalrun.faults import FAULT_REGISTRATION, guard_fault_registration

    path = tmp_path / FAULT_REGISTRATION
    path.parent.mkdir(parents=True)
    if failure != "missing":
        path.write_text("prospective registration")

    def git(root, *args):
        if args[0] == "log":
            return (0, "" if failure == "uncommitted" else "first")
        if args[0] == "diff":
            return (
                0,
                "changed"
                if (failure == "history" and args[1] != "HEAD")
                or (failure == "worktree" and args[1] == "HEAD")
                else "",
            )
        if args[0] == "show":
            return (0, "changed" if failure == "worktree" else path.read_text())
        return (0, "")

    with pytest.raises(ValueError, match="hypotheses_m3_faults_not_committed"):
        guard_fault_registration(tmp_path, git=git)
    assert not (tmp_path / "data/results").exists()


def test_fault_registration_hash_in_manifest(tmp_path):
    import hashlib

    from govops.evalrun.faults import FAULT_REGISTRATION, guard_fault_registration

    path = tmp_path / FAULT_REGISTRATION
    path.parent.mkdir(parents=True)
    path.write_text("prospective registration\n")
    calls = []

    def git(root, *args):
        calls.append(args)
        if args[0] == "log":
            return (0, "newer\nfirst")
        if args[0] == "show":
            return (0, path.read_text().strip())
        return (0, "")

    metadata = guard_fault_registration(tmp_path, git=git)

    async def execute(*args):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(
            run_faults(root=tmp_path, admin=Admin(), execute=execute, metadata=metadata)
        )
    manifest = json.loads(
        next((tmp_path / "data/results/faults").glob("*/manifest.json")).read_text()
    )
    assert (
        manifest["hypotheses_m3_faults_sha256"]
        == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert manifest["hypotheses_m3_faults_registration_commit"] == "first"
    assert ("diff", "first..HEAD", "--", FAULT_REGISTRATION) in calls

"""User-run fault evaluation. Tests inject adapters; live is never a test default."""

import argparse
import asyncio
import functools
import hashlib
import json
import os
import secrets
import subprocess
import time
from contextlib import AsyncExitStack
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg
from langgraph.types import Command

from govops.agent.approval import approval_id
from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import AgentContext, get_task
from govops.agent.users import load_users
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.wiring import build_task_service
from govops.costs import BudgetExceededError, CostLedger
from govops.env import env_or
from govops.eval.duplicates import count_effects
from govops.eval.meta import load_meta
from govops.eval.observation import ApprovalRecord
from govops.eval.run_grade import M3_GRADING, grade_run, guard_grading_rules
from govops.eval.spec import TaskSpec
from govops.evalrun import worktree
from govops.evalrun.anchors import AnchorTracker, anchor_label
from govops.evalrun.dev import ROOT, _Capture, _git, _write, infrastructure_failure
from govops.evalrun.dev_m3 import (
    PARAMS,
    PROFILE,
    _invoke,
    _TaskTools,
    check_reason,
    git_metadata_m3,
    new_tool_policy,
)
from govops.evalrun.driver import _BODY_FIELD, _TERMINAL, ApprovalDriver, DriveResult
from govops.evalrun.fault_scenarios import (
    POOL,
    SEED,
    build_plan,
    fault_rules,
    plan_hash,
    validate_plan,
)
from govops.evalrun.mock_admin import MockAdmin, subprocess_runner
from govops.evalrun.observe import build_observation_m3
from govops.evalrun.preflight import run_preflight
from govops.evalrun.tool_trace import extract_tool_trace, observation_calls
from govops.gateway.client import _MODEL, LiteLLMChatClient
from govops.gateway.faults import model_fault_script
from govops.gateway.hook_digest import compute_digests
from govops.gateway.team_keys import TeamKeyResolver
from govops.obs.mask import register_secret_values
from govops.obs.trace import SpanKind, configure, trace
from govops.replay.chaos_a import ChaosAServer, ChaosMode, judge_f11
from govops.replay.llm import prompt_hash
from govops.tools.a_mcp import AMcpClient, check_contract
from govops.tools.contract import snapshot_sha256
from govops.tools.fault_transport import FaultTransport
from govops.tools.jira import JiraClient
from govops.tools.outbox import PostgresOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient


class AsyncAdmin:
    """Compose subprocesses run off the event loop."""

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        method = getattr(self.inner, name)
        return functools.partial(asyncio.to_thread, method)


def save(path, value):
    """An interruption cannot leave a completed record half-written."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    _write(temporary, value)
    temporary.replace(path)


def now():
    return datetime.now(UTC).isoformat()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--resume")
    return result


def select_specs(rows):
    specs = {s.task_id: s for s in (TaskSpec.model_validate(row) for row in rows)}
    if any(key not in specs or specs[key].split != "dev" for key in POOL):
        raise ValueError("plan_mismatch")
    return {key: specs[key] for key in POOL}


def assess(row, result):
    """Conjunction of outcomes and path evidence, never a duplicate-only pass."""
    expected = row.definition.expected
    checks = {
        "duplicates": result["duplicates"] == expected.duplicates,
        "missing": result["missing"] == expected.missing,
        "status": result["status"] == expected.status,
        "posts": expected.posts[0] <= result["posts"] <= expected.posts[1],
        "lookups": expected.lookups[0] <= result["lookups"] <= expected.lookups[1],
        "injection": result["injections"] > 0,
        "wait": not expected.wait
        or result.get("wait_seconds", 0) >= expected.wait_seconds,
        "grading": not result.get("absolute_violations", False)
        and not result.get("leak_pending", 0),
        "driver": not result.get("timed_out", False)
        and not result.get("unexpected_http_count", 0),
        "infrastructure": not result.get("infrastructure_failure", False),
    }
    if row.scenario == "F8":
        checks["resume"] = (
            3 <= result.get("resume_calls", 0) <= 5 and result.get("execute_nodes") == 1
        )
    if row.scenario == "F9":
        checks["double_click"] = (
            result.get("approval_requests") == 2 and result.get("execute_nodes") == 1
        )
    if row.scenario == "F11":
        checks["f11"] = (
            result.get("f11_passed") is True and result.get("a_attempts", 0) > 0
        )
    return checks


def summarize(plan, records):
    report = {}
    for row in plan:
        key = row.scenario + "/" + row.variant
        group = report.setdefault(
            key,
            {
                "planned": 0,
                "executed": 0,
                "duplicates": 0,
                "missing": 0,
                "final_status": {},
                "task_status": {},
                "posts": 0,
                "marker_lookups": 0,
                "injections": 0,
                "known_limit": 0,
                "passed": 0,
                "mixed_model_tasks": 0,
                "mixed_model_tcr": {"passed": 0, "total": 0},
                "tcr": {"passed": 0, "total": 0},
                "f11": {"passed": 0, "total": 0},
            },
        )
        group["planned"] += 1
        record = records.get(row.position)
        if record is None:
            continue
        result = record["attempts"][-1]
        group["executed"] += 1
        for attempt in record["attempts"]:
            for metric in ("duplicates", "missing", "posts", "injections"):
                group[metric] += attempt[metric]
            group["marker_lookups"] += attempt["lookups"]
        status = result["status"]
        group["final_status"][status] = group["final_status"].get(status, 0) + 1
        task_status = result.get("task_status", result["status"])
        group["task_status"][task_status] = group["task_status"].get(task_status, 0) + 1
        group["passed"] += all(assess(row, result).values())
        group["known_limit"] += result["duplicates"] if row.variant == "long" else 0
        group["tcr"]["total"] += 1
        group["tcr"]["passed"] += bool(result["tcr"])
        if len(result["models"]) > 1:
            group["mixed_model_tasks"] += 1
            group["mixed_model_tcr"]["total"] += 1
            group["mixed_model_tcr"]["passed"] += bool(result["tcr"])
        if row.scenario == "F11":
            group["f11"]["total"] += 1
            group["f11"]["passed"] += bool(result.get("f11_passed"))
    return report


async def probe_mock_reads(jira, slack, user_id):
    """One side-effect-free request per mock; an absent ticket still counts."""
    await jira.get_ticket(user_id, "syn-fault-probe")
    await slack.list_channels(user_id)


async def run_faults(
    *,
    root,
    admin,
    execute,
    metadata,
    probe,
    resume=None,
    plan=None,
    preflight=list,
    anchor_tracker=None,
):
    tracker = anchor_tracker if anchor_tracker is not None else AnchorTracker()
    plan = build_plan() if plan is None else tuple(plan)
    validate_plan(plan)
    digest = plan_hash(plan)
    # UUID-only names prevent resume from escaping the dedicated results directory.
    if resume is not None and (
        len(resume) != 32 or any(c not in "0123456789abcdef" for c in resume)
    ):
        raise ValueError("invalid_resume")
    directory = root / "data/results/faults" / (resume or uuid4().hex)
    records = {}
    signature = {
        "seed": SEED,
        "plan_hash": digest,
        "profile": PROFILE.name,
        "grading_rules": M3_GRADING,
        "prompt_hash": prompt_hash(
            system_prompt=PROFILE.system_prompt,
            tools=PROFILE.tools,
            model=_MODEL,
            params=PARAMS,
        ),
        "contract_snapshot_sha256": snapshot_sha256(),
    }
    if resume:
        manifest = json.loads((directory / "manifest.json").read_text())
        if any(manifest.get(k) != v for k, v in signature.items()):
            raise ValueError("plan_mismatch")
        for row in plan:
            path = directory / f"{row.position:04d}.json"
            if path.exists():
                record = json.loads(path.read_text())
                if record["plan"] != asdict(row) or record["plan_hash"] != digest:
                    raise ValueError("plan_mismatch")
                records[row.position] = record
        manifest["interruptions"].append(
            {"at": now(), "event": "resume", "completed": len(records)}
        )
    else:
        manifest = {
            **metadata,
            **signature,
            "ledger_at_start": worktree.ledger_state(root),
            "started_at": now(),
            "finished_at": None,
            "interruptions": [],
            "planned_count": len(plan),
            "absolute_rule_denominator": sum(r.absolute_rule for r in plan),
        }
    directory.mkdir(parents=True, exist_ok=True)
    save(directory / "manifest.json", manifest)
    interrupted = False
    try:
        failures = await _invoke(preflight)
        if failures:
            raise ValueError(failures[0])
        # Exactly once at startup, including resume. Always clear on exit as well.
        await _invoke(admin.faults_clear)
        await _invoke(admin.reset)
        await _invoke(probe)
        stats = await _invoke(admin.faults_stats)
        for service, endpoint in (
            ("jira", "jira.ticket.get"),
            ("slack", "slack.channels"),
        ):
            counters = stats.get(service, {}).get(endpoint, {})
            if counters.get("applied_rules", 0) > 0:
                raise ValueError("fault_rules_active")
            if counters.get("requests", 0) < 1 or counters.get("applied_rules") != 0:
                raise ValueError("probe_not_observed")
        await _invoke(admin.reset)
        for row in plan:
            if row.position in records:
                continue
            print(f"[{row.position}/154] {row.scenario}/{row.variant} {row.task_id}")
            pending = directory / f"{row.position:04d}.pending.json"
            attempts = []
            if pending.exists():
                saved = json.loads(pending.read_text())
                if (
                    saved["plan"] != asdict(row)
                    or saved["plan_hash"] != digest
                    or len(saved["attempts"]) != 1
                    or not saved["attempts"][0]["infrastructure_failure"]
                ):
                    raise ValueError("plan_mismatch")
                attempts = saved["attempts"]
            for attempt in range(len(attempts), 2):
                await _invoke(admin.reset)
                try:
                    result = await execute(
                        row,
                        directory / "attempts" / f"{row.position:04d}-{attempt + 1}",
                    )
                finally:
                    await _invoke(admin.faults_clear)
                result.setdefault("check_reason", None)
                attempts.append(result)
                # Persist a provisional attempt before the single infrastructure retry.
                record = {
                    "plan": asdict(row),
                    "plan_hash": digest,
                    "attempts": attempts,
                    "completed_at": now(),
                    "checks": assess(row, result),
                }
                if result["infrastructure_failure"] and attempt == 0:
                    save(directory / f"{row.position:04d}.pending.json", record)
                    continue
                save(directory / f"{row.position:04d}.json", record)
                (directory / f"{row.position:04d}.pending.json").unlink(missing_ok=True)
                records[row.position] = record
                break
        manifest["finished_at"] = now()
    except BaseException:
        interrupted = True
        manifest["interruptions"].append(
            {"at": now(), "event": "interrupted", "completed": len(records)}
        )
        raise
    finally:
        try:
            await _invoke(admin.faults_clear)
        except Exception:  # noqa: BLE001 — cleanup must preserve the original safe failure
            manifest["cleanup_failed"] = True
            if not interrupted:
                raise ValueError("cleanup_failed") from None
        finally:
            for line in tracker.finish(
                anchor_label("faults", directory.name), repo_root=root
            ):
                print(line)
            manifest["report"] = summarize(plan, records)
            save(directory / "manifest.json", manifest)
    return manifest


class FaultDriver(ApprovalDriver):
    """Real requester POST plus API double clicks or in-process Command resumes."""

    def __init__(self, *args, row, **kwargs):
        super().__init__(*args, **kwargs)
        self.row = row
        self.resume_calls = 0
        self.approval_requests = 0

    async def run(self, spec):
        if self.row.scenario not in {"F8", "F9"}:
            return await super().run(spec)
        sessions = SessionStore()
        app = create_app(
            users=frozenset(self.users),
            sessions=sessions,
            demo_login=False,
            service=self.service,
        )
        token, _ = sessions.issue(spec.user)
        approvals, unexpected = [], []
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://synthetic",
            headers={"Authorization": "Bearer " + token},
        ) as client:
            created = await client.post("/tasks", json={"request": spec.request})
            if created.status_code != 202:
                raise RuntimeError("task_start_failed")
            task_id = created.json()["task_id"]
            path = "/tasks/" + task_id
            started = time.monotonic()
            index = 0
            while True:
                response = await client.get(path)
                if response.status_code != 200:
                    raise RuntimeError("task_read_failed")
                view = response.json()
                if view["status"] in _TERMINAL:
                    break
                if time.monotonic() - started > self.max_wait:
                    return DriveResult(
                        task_id,
                        view,
                        approvals,
                        len(spec.approval_script) - index,
                        unexpected,
                        True,
                    )
                if view["status"] != "awaiting_approval":
                    await self.sleep(self.poll_interval)
                    continue
                if index >= len(spec.approval_script):
                    break
                step = spec.approval_script[index]
                actor = self._actor(spec.user, step.actor)
                actor_token, _ = sessions.issue(actor)
                headers = {"Authorization": "Bearer " + actor_token}
                body = {"decision": step.decision, "draft_sha256": view["draft_sha256"]}
                if step.decision == "edit_approve":
                    field = _BODY_FIELD[view["draft"]["tool"]]
                    body["edited"] = {
                        field: view["draft"]["args"].get(field, "")
                        + "\n"
                        + (step.edit_append or "")
                    }
                if self.row.scenario == "F9":

                    async def approve(headers=headers, body=body):
                        self.approval_requests += 1
                        return await client.post(
                            path + "/approval", headers=headers, json=body
                        )

                    responses = (
                        await asyncio.gather(approve(), approve())
                        if self.row.variant == "concurrent"
                        else [await approve(), await approve()]
                    )
                    for response in responses:
                        if response.status_code not in (200, 202):
                            unexpected.append(str(response.status_code))
                    actual = responses[0].status_code
                else:
                    value = {"approver": actor, **body}
                    count = 3 + (self.row.position % 3)
                    for _ in range(count):
                        with trace(task_id, user_id=spec.user):
                            await self.service.graph.ainvoke(
                                Command(resume=value),
                                self.service._config(task_id),
                                context=AgentContext(spec.user),
                            )
                        self.resume_calls += 1
                    actual = 202
                approvals.append(
                    ApprovalRecord(
                        approval_id=approval_id(task_id, body["draft_sha256"]),
                        actor=actor,
                        decision=step.decision,
                        http_status=actual,
                    )
                )
                index += 1
        return DriveResult(
            task_id,
            view,
            approvals,
            len(spec.approval_script) - index,
            unexpected,
            False,
        )


class CountingOutbox:
    """Claim entry counts actual execution, including attempted repeated claims."""

    def __init__(self, inner):
        self.inner = inner
        self.claims = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def claim(self, row):
        self.claims += 1
        return await self.inner.claim(row)


class ModelFaultAdapter:
    """Count task-scoped down-alias selections from the established script."""

    def __init__(self, variant):
        self.script = model_fault_script(variant)
        self.injections = 0

    def should_fail(self, task_id):
        failed = self.script.should_fail(task_id)
        self.injections += failed
        return failed


class Evidence:
    """Aggregate spans in memory; retain no messages, arguments, bodies or headers."""

    def __init__(self):
        self.lookups = 0
        self.execute_nodes = 0
        self.a_attempts = 0
        self.wait_seconds = 0.0
        self.last_lookup_end = None
        self.last_tool_end = None
        self.retry_wait = 0.0

    def emit(self, record):
        if record.kind == SpanKind.IDEMPOTENCY_LOOKUP:
            start = datetime.fromisoformat(record.start_ts)
            if self.last_lookup_end is not None:
                self.wait_seconds += max(
                    0.0, (start - self.last_lookup_end).total_seconds()
                )
            self.last_lookup_end = datetime.fromisoformat(record.end_ts)
            self.lookups += 1
        if record.kind == SpanKind.TOOL_CALL and record.name == "tool_attempt":
            start = datetime.fromisoformat(record.start_ts)
            if self.last_tool_end is not None:
                self.retry_wait += max(
                    0.0, (start - self.last_tool_end).total_seconds()
                )
            self.last_tool_end = datetime.fromisoformat(record.end_ts)
        if record.kind == SpanKind.RETRY:
            self.wait_seconds += record.duration_ms / 1000
        self.execute_nodes += (
            record.kind == SpanKind.GRAPH_NODE and record.name == "execute"
        )
        self.a_attempts += (
            record.kind == SpanKind.TOOL_CALL
            and record.attributes.get("service") == "a_mcp"
        )

    def close_trace(self, trace_id):
        pass


def chaos_client(server):
    value = secrets.token_urlsafe(32)

    def synthetic(user_id, audience):
        if not user_id.startswith("demo-") or audience != "kodocs-mcp":
            raise ValueError("invalid_chaos_identity")
        return value

    return AMcpClient(server.url, credential_for=synthetic)


class WriteFaultTransport(httpx.AsyncBaseTransport):
    """Keep F4's failing counter restricted to sends, never preliminary reads."""

    def __init__(self, inner):
        self.inner = inner
        self.fault = FaultTransport(inner, failing_requests=[1])
        self.posts = 0

    async def handle_async_request(self, request):
        if request.method == "POST":
            self.posts += 1
            return await self.fault.handle_async_request(request)
        return await self.inner.handle_async_request(request)

    async def aclose(self):
        await self.inner.aclose()


def observation_reports(reports):
    """The REST contract allows absent descriptions; mock hashes them as empty.

    Adapt the nullable field without losing effects or changing the mock's hash.
    The original reports remain the authoritative source for effect counts.
    """
    jira = reports.get("jira", {})
    children = []
    for row in jira.get("children", []):
        if row.get("description") is None:
            if row.get("description_sha256") != hashlib.sha256(b"").hexdigest():
                raise ValueError("invalid_mock_state")
            row = {**row, "description": ""}
        children.append(row)
    return {**reports, "jira": {**jira, "children": children}}


async def execute_live(
    row,
    directory,
    *,
    root,
    admin,
    spec,
    saver,
    outbox,
    ledger,
    grading,
    rest_transport_factory=None,
    anchor_tracker=None,
):
    tracker = anchor_tracker if anchor_tracker is not None else AnchorTracker()
    definition = row.definition
    kind = spec.expected.effects[0].kind
    endpoint = {
        "slack_message": "slack.send",
        "jira_comment": "jira.comment.create",
        "jira_followup_ticket": "jira.child.create",
    }[kind]
    rules = fault_rules(definition, endpoint)
    if rules:
        await asyncio.to_thread(
            admin.faults_set,
            "mock-slack" if kind == "slack_message" else "mock-jira",
            rules,
        )
    evidence = Evidence()
    async with AsyncExitStack() as stack:
        server = None
        if row.scenario == "F11":
            server = await stack.enter_async_context(
                ChaosAServer(ChaosMode(row.variant), delay=12)
            )
            a = chaos_client(server)
        else:
            a = AMcpClient(env_or("KODOCS_MCP_URL", ""))
        transport_factory = rest_transport_factory or httpx.AsyncHTTPTransport
        transport = WriteFaultTransport(transport_factory())
        rest = await stack.enter_async_context(
            httpx.AsyncClient(
                transport=transport if row.scenario == "F4" else transport_factory(),
                trust_env=False,
            )
        )
        policy = new_tool_policy()
        runtime = ToolRuntime(
            a,
            JiraClient(
                env_or("MOCK_JIRA_URL", "http://127.0.0.1:8081"), http_client=rest
            ),
            SlackClient(
                env_or("MOCK_SLACK_URL", "http://127.0.0.1:8082"), http_client=rest
            ),
            policy=policy,
        )
        tools = _TaskTools(runtime, runtime, task_id=uuid4().hex)
        script = ModelFaultAdapter(row.variant) if row.scenario == "F6" else None
        counted_outbox = CountingOutbox(outbox)
        resolver = TeamKeyResolver(
            {label: user.team for label, user in load_users().items()}
        )
        if resolver.missing():
            raise ValueError("team_keys_missing")
        chat = _Capture(
            LiteLLMChatClient(
                ledger=ledger,
                key_resolver=resolver.key_for,
                expected_digests=compute_digests(root),
                require_guardrail=True,
                **(
                    {
                        "fault_mode": "down_alias",
                        "fault_script": script,
                    }
                    if row.scenario == "F6"
                    else {}
                ),
            )
        )
        service = build_task_service(
            chat=chat,
            tools=tools,
            saver=saver,
            outbox=counted_outbox,
            profile=PROFILE,
            audit_dir=root / "data/interim/audit",
        )
        tracker.track(service)
        driver = FaultDriver(
            service, service.users, row=row, max_wait=definition.max_wait
        )
        configure([evidence])
        try:
            result = await driver.run(spec)
            state = await get_task(service.graph, result.task_id)
        finally:
            tools.close()
            await service.aclose()
            if anchor_tracker is None:
                for line in tracker.finish(
                    anchor_label("faults", directory.name), repo_root=root
                ):
                    print(line)
            configure([])
        reports = await asyncio.to_thread(admin.state, bodies=True)
        stats = await asyncio.to_thread(admin.faults_stats)
        counts = count_effects(
            reports, task_id=result.task_id, expected=spec.expected.effects
        )
        calls, results = extract_tool_trace(tools.results)
        obs = build_observation_m3(
            spec=spec,
            run_index=row.position,
            view=result.view,
            mock_state=observation_reports(reports),
            approvals=result.approvals,
            tool_calls=observation_calls(state or {}, calls),
            tool_results=results,
            plan_remaining=result.plan_remaining,
            trace_id=result.task_id,
        )
        obs = obs.model_copy(update={"fault_injected": True})
        grade = grade_run(spec=spec, obs=obs, grading_rules=M3_GRADING, **grading)
        counters = stats["slack" if kind == "slack_message" else "jira"].get(
            endpoint, {}
        )
        posts = transport.posts if row.scenario == "F4" else counters.get("requests", 0)
        injections = (
            transport.fault.injections
            if row.scenario == "F4"
            else driver.resume_calls
            if row.scenario == "F8"
            else driver.approval_requests
            if row.scenario == "F9"
            else script.injections
            if row.scenario == "F6"
            else evidence.a_attempts
            if row.scenario == "F11"
            else counters.get("applied_rules", 0)
        )
        wait_seconds = evidence.wait_seconds
        if row.scenario == "F3" and posts > 1:
            # Tool attempt timestamps measure the actual Retry-After interval.
            wait_seconds = max(wait_seconds, evidence.retry_wait)
        verdict = judge_f11(spec, obs) if server else None
        return {
            "check_reason": check_reason(result.view),
            "duplicates": counts.duplicates,
            "missing": counts.missing,
            "status": "no_effect"
            if server and not obs.effects
            else (result.view.get("effect") or {}).get("state", result.view["status"]),
            "posts": posts,
            "server_posts": counters.get("requests", 0),
            "resends": max(0, posts - 1),
            "task_status": result.view["status"],
            "lookups": evidence.lookups,
            "wait_seconds": wait_seconds,
            "injections": injections,
            "models": sorted(chat.models),
            "tcr": grade.tcr.passed,
            "absolute_violations": bool(
                counts.unapproved or grade.rules.leak_confirmed
            ),
            "leak_pending": grade.rules.leak_pending,
            "infrastructure_failure": any(
                infrastructure_failure(e) for e in chat.errors
            ),
            "resume_calls": driver.resume_calls,
            "approval_requests": driver.approval_requests,
            "execute_nodes": counted_outbox.claims,
            "a_attempts": evidence.a_attempts,
            "f11_passed": verdict.passed if verdict else None,
            "f11_effect_count": len(verdict.side_effects) if verdict else None,
            "f11_unsupported_fact_count": len(verdict.leaked_facts)
            if verdict
            else None,
            "timed_out": result.timed_out,
            "unexpected_http_count": len(result.unexpected_http),
        }


FAULT_REGISTRATION = "docs/results/hypotheses-m3-faults.md"


def guard_fault_registration(root, *, git=_git):
    """Freeze the first registration before any official fault execution."""
    status_code, status = git(root, "status", "--porcelain")
    if status_code or worktree.dirty_entries(status, root):
        raise ValueError("dirty_worktree")
    path = root / FAULT_REGISTRATION
    error = "hypotheses_m3_faults_not_committed"
    code, history = git(
        root, "log", "--diff-filter=A", "--format=%H", "--", FAULT_REGISTRATION
    )
    if code or not history.strip() or not path.is_file():
        raise ValueError(error)
    commit = history.splitlines()[-1]
    for revision in ("HEAD", commit):
        if git(root, "cat-file", "-e", f"{revision}:{FAULT_REGISTRATION}")[0]:
            raise ValueError(error)
    code, delta = git(root, "diff", f"{commit}..HEAD", "--", FAULT_REGISTRATION)
    work_code, work_delta = git(root, "diff", "HEAD", "--", FAULT_REGISTRATION)
    if code or delta or work_code or work_delta:
        raise ValueError(error)
    return {
        "hypotheses_m3_faults_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "hypotheses_m3_faults_registration_commit": commit,
    }


async def live(*, resume=None):
    root = ROOT
    tracker = AnchorTracker()
    specs = select_specs(json.loads((root / "data/eval/tasks.json").read_text()))
    a_root = Path(
        env_or("A_ROOT", "~/PycharmProjects/ko-docs-agentic-rag")
    ).expanduser()
    registration_metadata = guard_fault_registration(root)
    metadata = git_metadata_m3(root, a_root)
    metadata.update(registration_metadata)
    metadata.update(guard_grading_rules(root, M3_GRADING))
    url = env_or("GOVOPS_DATABASE_URL", "")
    if not url or url == "memory":
        raise ValueError("postgres_required")
    register_secret_values(
        value
        for key, value in os.environ.items()
        if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    )
    admin = MockAdmin(root)
    a = AMcpClient(env_or("KODOCS_MCP_URL", ""))

    def runner(command, *, cwd):
        if command[0] != "postgres":
            return subprocess_runner(command, cwd=cwd)
        try:
            with psycopg.connect(url, connect_timeout=10) as connection:
                connection.execute("SELECT 1").fetchone()
            return subprocess.CompletedProcess(command, 0, "")
        except Exception:  # noqa: BLE001 — no connection details
            return subprocess.CompletedProcess(command, 1, "")

    def preflight():
        with httpx.Client(trust_env=False) as http:
            return run_preflight(
                root=root,
                runner=runner,
                http=http,
                mock_admin=admin,
                a_check=lambda: asyncio.run(
                    check_contract(a, user_id=specs[POOL[0]].user)
                ),
            )

    grading = {
        "meta": load_meta(root / "data/eval/meta.json"),
        "tickets": json.loads((root / "data/seed/tickets.json").read_text())["tickets"],
        "users": json.loads((root / "data/seed/users.json").read_text())["users"],
    }
    async with AsyncExitStack() as stack:
        saver = ledger = outbox = None

        async def initialize():
            nonlocal saver, ledger, outbox
            failures = await asyncio.to_thread(preflight)
            if failures:
                return failures
            saver = await stack.enter_async_context(open_checkpointer(url))
            ledger = CostLedger()
            outbox = PostgresOutboxStore(url)
            return []

        async def execute(row, directory):
            return await execute_live(
                row,
                directory,
                root=root,
                admin=admin,
                spec=specs[row.task_id],
                saver=saver,
                outbox=outbox,
                ledger=ledger,
                grading=grading,
                anchor_tracker=tracker,
            )

        async def probe():
            await probe_mock_reads(
                JiraClient(env_or("MOCK_JIRA_URL", "http://127.0.0.1:8081")),
                SlackClient(env_or("MOCK_SLACK_URL", "http://127.0.0.1:8082")),
                specs[POOL[0]].user,
            )

        return await run_faults(
            root=root,
            admin=AsyncAdmin(admin),
            execute=execute,
            metadata=metadata,
            resume=resume,
            preflight=initialize,
            anchor_tracker=tracker,
            probe=probe,
        )


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        asyncio.run(live(resume=args.resume))
    except BudgetExceededError:
        print("budget_exceeded")
        return 2
    except KeyboardInterrupt:
        print("interrupted")
        return 130
    except Exception as error:  # noqa: BLE001 — fixed safe codes only
        code = str(error) if isinstance(error, ValueError) else ""
        safe = {
            "plan_mismatch",
            "cleanup_failed",
            "fault_rules_active",
            "probe_not_observed",
            "invalid_resume",
            "postgres_required",
            "dirty_worktree",
            "exempt_paths_invalid",
            "fault_registration_not_committed",
            "stale_mock_image",
            "litellm_config_stale",
        }
        print(
            code
            if code in safe
            or code
            in {
                "service_down:" + name
                for name in ("postgres", "litellm", "mock-jira", "mock-slack", "a-mcp")
            }
            else "fault_run_failed"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

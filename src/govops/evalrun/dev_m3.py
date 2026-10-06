"""User-run live dev 20 evaluation with one explicitly selected write profile."""

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import shutil
import subprocess
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal
from uuid import uuid4

import httpx
import psycopg

from govops import golden
from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import WITH_WRITES, get_task
from govops.api.wiring import build_task_service
from govops.costs import BudgetExceededError, CostLedger, cost_report
from govops.env import env_or
from govops.eval.duplicates import count_effects
from govops.eval.meta import load_meta
from govops.eval.run_grade import REGISTERED, grade_run, guard_grading_rules
from govops.eval.spec import TaskSpec
from govops.evalrun.dev import (
    ROOT,
    _Capture,
    _git,
    _write,
    git_metadata,
    infrastructure_failure,
    mock_seed_check,
    read_seed_matches,
)
from govops.evalrun.driver import ApprovalDriver
from govops.evalrun.explore import candidate
from govops.evalrun.mock_admin import MockAdmin, subprocess_runner
from govops.evalrun.observe import build_observation_m3
from govops.evalrun.preflight import run_preflight
from govops.evalrun.tool_trace import extract_tool_trace, observation_calls, structure
from govops.gateway.client import _MODEL, LiteLLMChatClient
from govops.obs.jsonl import JsonlSink
from govops.obs.mask import register_secret_values, scrub_secrets
from govops.obs.trace import configure
from govops.replay.a_through import RecordingToolExecutor
from govops.replay.llm import RecordingChatClient, prompt_hash
from govops.replay.recordings import RecordingRejected
from govops.tools.a_mcp import AMcpClient, check_contract
from govops.tools.contract import snapshot_sha256
from govops.tools.jira import JiraClient
from govops.tools.outbox import PostgresOutboxStore
from govops.tools.registry import _WRITES, ToolRuntime
from govops.tools.slack import SlackClient

REGISTRATION_FILES = (
    "docs/results/hypotheses-m3.md",
    "data/replay/golden/expectations.json",
    "docs/results/m3-dev-supersedes.json",
)
PARAMS = {}
# Preserve the existing profile's prompt/contracts; "writes" is the run label.
PROFILE = replace(WITH_WRITES, name="writes")


class SmokeSpec(TaskSpec):
    split: Literal["smoke"]


def smoke_specs():
    """Demo requests deliberately independent of the evaluation corpus."""
    return [
        SmokeSpec(
            task_id=f"T2-smoke-{decision}",
            type="T2",
            subtype="smoke",
            split="smoke",
            executable_in="m3",
            user="demo-platform-g3",
            request=request,
            expected={
                "outcome": "approved_executed"
                if decision == "approve"
                else "approval_rejected",
                "effects": [],
            },
            required_tools_any=[],
            forbidden={
                "leak_doc_ids": [],
                "leak_ticket_ids": [],
                "leak_values": [],
                "forbidden_tools": [],
            },
            required_facts=[],
            derived_values=[],
            approval_script=[
                {"actor": "requester", "decision": decision, "expect_http": None}
            ],
            provenance={
                "meta_sha256": "",
                "source_doc_ids": [],
                "source_ticket_ids": [],
            },
            spec_sha256="",
        )
        for decision, request in (
            ("approve", "INC-2026-012 장애보고 요약해서 team-platform에 올려줘"),
            ("reject", "INC-2026-012 장애보고 정리해서 team-platform에 공유해줘"),
        )
    ]


_SMOKE_CODES = frozenset(
    {
        "recipient_not_allowed",
        "pii_blocked_by_team_policy",
        "scope_wider_than_evidence",
        "evidence_scope_unknown",
        "ticket_not_found",
        "internal_error",
        "model_error",
        "gateway_error",
        "ledger_missing",
        "max_steps",
        "audit_write_failed",
        "invalid_approval",
        "budget_exceeded",
        "postgres_required",
        "profile_mismatch",
        "mock_seed_mismatch",
        "stale_mock_image",
        "litellm_config_stale",
        "compose_project_missing",
        "mock_build_missing",
    }
    | {
        f"service_down:{name}"
        for name in ("postgres", "litellm", "mock-jira", "mock-slack", "a-mcp")
    }
)


def _smoke_code(value):
    return value if isinstance(value, str) and value in _SMOKE_CODES else "unknown"


def _smoke_fail(scenario, stage, *, status="failed", error="None", reason="unknown"):
    print(
        f"FAIL {scenario} stage={stage} status={status} error={error} reason={reason}"
    )


async def _run_smoke(
    specs,
    *,
    root,
    chat,
    tools,
    mock_admin,
    seed_check,
    costs,
    preflight,
    saver,
    outbox,
    driver_kwargs,
    service_factory,
):
    results, passed = [], []
    stage = "preflight"
    before = None
    try:
        failures = await _invoke(preflight)
        if failures:
            for code in failures:
                _smoke_fail("approve", "preflight", reason=_smoke_code(code))
            return {"results": [], "exit_code": 1}
        before = costs()
        for spec in specs:
            scenario = spec.approval_script[0].decision
            stage, service, capture = "run", None, None
            try:
                await _invoke(mock_admin.reset)
                if not await _invoke(seed_check):
                    raise ValueError("mock_seed_mismatch")
                capture = _TaskTools(tools, tools, task_id=uuid4().hex)
                service = (service_factory or build_task_service)(
                    chat=chat,
                    tools=capture,
                    saver=saver,
                    outbox=outbox,
                    profile=PROFILE,
                    audit_dir=root / "data/interim/audit",
                )
                if (
                    not service.graph.agent_profile.allow_writes
                    or service.graph.agent_profile.name != "writes"
                ):
                    raise ValueError("profile_mismatch")
                result = await ApprovalDriver(
                    service, service.users, **(driver_kwargs or {})
                ).run(spec)
                results.append(result)
                status = result.view.get("status")
                reason = _smoke_code(
                    (result.view.get("check") or {}).get("reason")
                    or result.view.get("error")
                )
                if status == "blocked":
                    print(f"BLOCKED reason={reason}")
                stage = "approve"
                approvals = [
                    a
                    for a in result.approvals
                    if a.decision == scenario
                    and a.actor == spec.user
                    and 200 <= a.http_status < 300
                ]
                if (
                    len(approvals) != 1
                    or result.plan_remaining
                    or result.unexpected_http
                ):
                    _smoke_fail(
                        scenario,
                        stage,
                        status=status
                        if status
                        in {
                            "blocked",
                            "failed",
                            "completed",
                            "rejected",
                            "awaiting_approval",
                            "expired",
                            "interrupted",
                        }
                        else "unknown",
                        reason=reason,
                    )
                    passed.append(False)
                    continue
                stage = "state"
                report = await _invoke(mock_admin.state, bodies=False)
                messages = [
                    (c["channel_id"], row)
                    for c in report.get("slack", {}).get("channels", [])
                    for row in c["messages"]
                ]
                jira = report.get("jira", {})
                total = (
                    len(messages)
                    + len(jira.get("comments", []))
                    + len(jira.get("children", []))
                )
                ok = not result.timed_out and (
                    status == "completed"
                    and total == 1
                    and messages
                    and messages[0][0] == "team-platform"
                    and (messages[0][1].get("metadata") or {}).get("govops_approval")
                    == approvals[0].approval_id
                    if scenario == "approve"
                    else status == "rejected" and total == 0
                )
                passed.append(bool(ok))
                if ok:
                    print(f"PASS {scenario} effect={total}")
                else:
                    _smoke_fail(
                        scenario,
                        stage,
                        status=status
                        if status
                        in {
                            "completed",
                            "rejected",
                            "blocked",
                            "failed",
                            "expired",
                            "interrupted",
                            "awaiting_approval",
                        }
                        else "unknown",
                        reason=reason,
                    )
            except Exception as error:  # noqa: BLE001 — never print exception messages
                passed.append(False)
                _smoke_fail(
                    scenario,
                    stage,
                    error=type(error).__name__,
                    reason=_smoke_code(str(error)),
                )
            finally:
                if capture is not None:
                    capture.close()
                if service is not None:
                    await service.aclose()
    except Exception as error:  # noqa: BLE001 — safe CLI boundary
        passed.append(False)
        _smoke_fail(
            "approve", stage, error=type(error).__name__, reason=_smoke_code(str(error))
        )
    finally:
        if before is not None:
            try:
                after = costs()
                delta = sum(after.values()) - sum(before.values())
                print(f"INFO cost total_cents={delta * 100:.6f}")
            except Exception as error:  # noqa: BLE001
                passed.append(False)
                _smoke_fail(
                    "approve",
                    "state",
                    error=type(error).__name__,
                    reason="ledger_delta",
                )
    return {
        "results": results,
        "exit_code": 0 if len(passed) == 2 and all(passed) else 1,
    }


def select_tasks(rows, requested=None, *, smoke=False):
    specs = [
        row if isinstance(row, TaskSpec) else TaskSpec.model_validate(row)
        for row in rows
    ]
    if requested is not None:
        by_id = {spec.task_id: spec for spec in specs}
        if (
            not requested
            or len(set(requested)) != len(requested)
            or any(key not in by_id for key in requested)
        ):
            raise ValueError("invalid_task_selection")
        selected = [by_id[key] for key in requested]
        if any(spec.split != "dev" for spec in selected):
            raise ValueError("test_task_refused")
        if not smoke and (
            len(selected) != 20
            or {spec.task_id for spec in selected}
            != {spec.task_id for spec in specs if spec.split == "dev"}
        ):
            raise ValueError("partial_dev_refused")
        return selected
    return [
        spec
        for spec in specs
        if spec.split == "dev" and spec.executable_in in {"m2", "m3"}
    ]


DEV_GRADING_RULES = REGISTERED


def guard_live_m3(metadata):
    if not metadata.get("hypotheses_m3_committed") or not metadata.get(
        "hypotheses_m3_unchanged"
    ):
        raise ValueError("hypotheses_m3_not_committed")
    if not metadata.get("worktree_clean"):
        raise ValueError("dirty_worktree")


def git_metadata_m3(root, a_root):
    registration = _git(root, "log", "-1", "--format=%H", "--", REGISTRATION_FILES[0])[
        1
    ]
    committed = bool(registration) and all(
        _git(root, "ls-files", "--error-unmatch", path)[0] == 0
        and _git(root, "cat-file", "-e", f"HEAD:{path}")[0] == 0
        and _git(root, "cat-file", "-e", f"{registration}:{path}")[0] == 0
        for path in REGISTRATION_FILES
    )
    code, delta = (
        _git(root, "diff", f"{registration}..HEAD", "--", *REGISTRATION_FILES)
        if registration
        else (1, "")
    )
    status_code, status = _git(root, "status", "--porcelain")
    extra = {
        "worktree_clean": status_code == 0 and not status,
        "hypotheses_m3_committed": committed,
        "hypotheses_m3_unchanged": code == 0 and not delta,
        "hypotheses_m3_registration_commit": registration,
    }
    guard_live_m3(extra)
    metadata = git_metadata(root, a_root)
    metadata.update(extra)
    metadata["hypotheses_m3_sha256"] = hashlib.sha256(
        (root / REGISTRATION_FILES[0]).read_bytes()
    ).hexdigest()
    metadata["supersedes"] = json.loads((root / REGISTRATION_FILES[2]).read_text())
    return metadata


def trace_mismatch(state, captured):
    count = sum(
        sum(
            call.get("function", call)["name"] not in _WRITES
            for call in message.get("tool_calls") or []
        )
        for message in state.get("messages", [])
        if message.get("role") == "assistant"
    )
    return count != sum(row.get("name") not in _WRITES for row in captured)


class _EffectCapture:
    """Capture actual approved sends; marker lookups retain their original route."""

    _NAMES: ClassVar[dict[str, str]] = {
        "send_message": "slack_send_message",
        "add_comment": "jira_add_comment",
        "create_followup": "jira_create_followup_ticket",
    }

    def __init__(self, inner, capture):
        self.inner, self.capture = inner, capture

    def __getattr__(self, method):
        function = getattr(self.inner, method)
        if method not in self._NAMES:
            return function

        async def send(*args, **kwargs):
            row = {"name": self._NAMES[method], "ok": False, "result": {}}
            try:
                result = await function(*args, **kwargs)
                row.update(ok=True, result=structure(result))
                return result
            finally:
                self.capture.record(row)

        return send


class _TaskTools(_Capture):
    """M2 capture with the write profile's shared runtime dependencies exposed."""

    def __init__(self, inner, runtime, *, task_id):
        super().__init__(inner)
        self.task_id = task_id
        self.closed = False
        self.late_calls = 0
        self.jira = _EffectCapture(runtime.jira, self)
        self.slack = _EffectCapture(runtime.slack, self)
        self.policy = runtime.policy

    def close(self):
        self.closed = True

    def record(self, row):
        if self.closed:
            self.late_calls += 1
        else:
            self.results.append(row)

    async def call(self, name, arguments, **kwargs):
        row = {"name": name, "ok": False, "result": {}}
        try:
            result = await self.inner.call(name, arguments, **kwargs)
            row.update(ok=not result.is_error, result=structure(result.payload))
            return result
        except Exception as error:
            if not self.closed:
                self.errors.append(error)
            raise
        finally:
            self.record(row)


async def _invoke(function, *args, **kwargs):
    result = function(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


async def run_dev_m3(
    specs,
    *,
    root,
    chat,
    tools,
    mock_admin,
    seed_check,
    metadata,
    costs,
    preflight,
    saver=None,
    outbox=None,
    evaluation_root=ROOT,
    smoke=False,
    driver_kwargs=None,
    service_factory=None,
):
    """Injected stores are test-only; live assembly always supplies PostgreSQL."""
    if smoke and specs and all(spec.split == "smoke" for spec in specs):
        return await _run_smoke(
            specs,
            root=root,
            chat=chat,
            tools=tools,
            mock_admin=mock_admin,
            seed_check=seed_check,
            costs=costs,
            preflight=preflight,
            saver=saver,
            outbox=outbox,
            driver_kwargs=driver_kwargs,
            service_factory=service_factory,
        )
    if any(spec.split != "dev" for spec in specs):
        raise ValueError("test_task_refused")
    if not specs:
        raise ValueError("invalid_task_selection")
    if not smoke:
        guard_live_m3(metadata)
    if saver is None or outbox is None:
        raise ValueError("postgres_required")
    failures = await _invoke(preflight)
    if failures:
        # No request details, service payloads, credentials or URLs are printed.
        for code in failures:
            print(code)
        if "service_down:litellm" in failures:
            print(
                "LiteLLM model/info: 에이전트 가상 키 인증·조회 권한 또는 서비스 확인 필요"
            )
        raise ValueError(failures[0])
    directory = root / "data/results/dev" / uuid4().hex
    manifest = dict(
        metadata,
        mode="live",
        grading_rules=DEV_GRADING_RULES,
        **guard_grading_rules(root, DEV_GRADING_RULES),
        profile="writes",
        model_id=_MODEL,
        call_params=PARAMS,
        prompt_hash=prompt_hash(
            system_prompt=PROFILE.system_prompt,
            tools=PROFILE.tools,
            model=_MODEL,
            params=PARAMS,
        ),
        contract_snapshot_sha256=snapshot_sha256(),
        started_at=datetime.now(UTC).isoformat(),
        finished_at=None,
        models_seen=[],
        unmapped_models=[],
        fallback_used=False,
        models_by_task={},
        mixed_model_tasks={"tasks": [], "count": 0},
        mixed_model_tcr={"passed": 0, "total": 0},
        trace_ids={},
        tool_trace_mismatch={},
        attempts={},
        infrastructure_failures=0,
        costs_by_purpose={},
        absolute_rule_violations={},
        effect_counts={},
        warnings=[]
        if str(metadata.get("a_git_head", "")).startswith("d2eec9c")
        else ["a_head_differs_d2eec9c"],
    )
    before = costs()
    observations, grades, smoke_results = [], {}, []
    attempt_captures = []
    status_path = root / "data/replay/golden/status.json"
    statuses = (
        json.loads(status_path.read_text())
        if not smoke and status_path.exists()
        else {}
    )
    if not smoke:
        meta = load_meta(evaluation_root / "data/eval/meta.json")
        tickets = json.loads((evaluation_root / "data/seed/tickets.json").read_text())[
            "tickets"
        ]
        users = json.loads((evaluation_root / "data/seed/users.json").read_text())[
            "users"
        ]
        directory.mkdir(parents=True)
        configure([JsonlSink(directory / "traces")])
    try:
        for spec in specs:
            attempts = manifest["attempts"][spec.task_id] = []
            task_models = set()
            for number in range(2):
                await _invoke(mock_admin.reset)
                if not await _invoke(seed_check):
                    raise ValueError("mock_seed_mismatch")
                attempt_dir = directory / "attempts" / f"{spec.task_id}-{number + 1}"
                captured_chat = _Capture(chat)
                record = not smoke and spec.task_id in golden.GOLDEN_TASKS
                recording_chat = (
                    RecordingChatClient(
                        captured_chat,
                        task_id=spec.task_id,
                        directory=attempt_dir / "llm",
                        system_prompt=PROFILE.system_prompt,
                        tools=PROFILE.tools,
                        model=_MODEL,
                        params=PARAMS,
                    )
                    if record
                    else None
                )
                recording_tools = (
                    RecordingToolExecutor(
                        tools, task_id=spec.task_id, directory=attempt_dir / "a_run"
                    )
                    if record
                    else None
                )
                graph_chat = _Capture(recording_chat) if record else captured_chat
                captured_tools = _TaskTools(
                    recording_tools if record else tools,
                    tools,
                    task_id=f"{spec.task_id}-{number + 1}-{uuid4().hex}",
                )
                service = (service_factory or build_task_service)(
                    chat=graph_chat,
                    tools=captured_tools,
                    saver=saver,
                    outbox=outbox,
                    profile=PROFILE,
                    audit_dir=root / "data/interim/audit"
                    if smoke
                    else attempt_dir / "audit",
                )
                try:
                    if (
                        service.graph.agent_profile.name != "writes"
                        or not service.graph.agent_profile.allow_writes
                    ):
                        raise ValueError("profile_mismatch")
                    result = await ApprovalDriver(
                        service, service.users, **(driver_kwargs or {})
                    ).run(spec)
                    state = await get_task(service.graph, result.task_id)
                finally:
                    captured_tools.close()
                    await service.aclose()
                errors = (
                    graph_chat.errors + captured_chat.errors + captured_tools.errors
                )
                budget = any(isinstance(error, BudgetExceededError) for error in errors)
                infra = any(infrastructure_failure(error) for error in errors)
                rejected = any(isinstance(error, RecordingRejected) for error in errors)
                llm_path, summary = None, None
                if record:
                    try:
                        llm_path = recording_chat.finish()
                    except RecordingRejected:
                        rejected = True
                    try:
                        summary = recording_tools.finish()
                    except RecordingRejected:
                        rejected = True
                task_models.update(captured_chat.models)
                manifest["models_seen"] = sorted(
                    set(manifest["models_seen"]) | captured_chat.models
                )
                manifest["unmapped_models"] = sorted(
                    set(manifest["unmapped_models"]) | captured_chat.unmapped_models
                )
                manifest["fallback_used"] |= captured_chat.fallback_used
                manifest["infrastructure_failures"] += int(infra)
                mismatch = trace_mismatch(state or {}, captured_tools.results)
                attempt = {
                    "attempt": number + 1,
                    "trace_id": result.task_id,
                    "infrastructure_failure": infra,
                    "budget_exceeded": budget,
                    "llm_recorded": llm_path is not None,
                    "a_calls": summary.stats.total if summary else 0,
                    "a_skipped": summary.stats.skipped if summary else 0,
                    "tool_trace_mismatch": mismatch,
                    "models_seen": sorted(captured_chat.models),
                    "unexpected_http": result.unexpected_http,
                    "timed_out": result.timed_out,
                }
                attempts.append(attempt)
                attempt_captures.append((captured_tools, attempt, attempt_dir))
                attempt["late_tool_calls"] = captured_tools.late_calls
                if mismatch:
                    manifest["tool_trace_mismatch"].setdefault(spec.task_id, []).append(
                        number + 1
                    )
                if smoke:
                    if budget:
                        raise BudgetExceededError("budget_exceeded")
                    if infra and number == 0:
                        continue
                    smoke_results.append(result)
                    break
                calls, tool_results = extract_tool_trace(captured_tools.results)
                calls = observation_calls(state or {}, calls)
                reports = await _invoke(mock_admin.state, bodies=True)
                obs = build_observation_m3(
                    spec=spec,
                    run_index=number,
                    view=result.view,
                    mock_state=reports,
                    approvals=result.approvals,
                    tool_calls=calls,
                    tool_results=tool_results,
                    plan_remaining=result.plan_remaining,
                    trace_id=result.task_id,
                )
                if infra or result.timed_out or result.unexpected_http:
                    obs = obs.model_copy(update={"status": "failed"})
                counts = count_effects(
                    reports, task_id=result.task_id, expected=spec.expected.effects
                )
                counts_row = {
                    key: getattr(counts, key)
                    for key in ("duplicates", "missing", "unapproved", "unmarked")
                } | {"status": obs.status}
                attempt["effect_counts"] = counts_row
                if counts.duplicates or counts.unapproved:
                    previous = manifest["absolute_rule_violations"].get(
                        spec.task_id, {}
                    )
                    manifest["absolute_rule_violations"][spec.task_id] = counts_row | {
                        key: previous.get(key, 0) + counts_row[key]
                        for key in ("duplicates", "unapproved", "unmarked")
                    }
                row = obs.model_dump()
                _write(attempt_dir / "observation.json", row)
                _write(attempt_dir / "attempt.json", attempt)
                if budget:
                    raise BudgetExceededError("예산 중단: ADR-013 축소 순서를 따른다")
                if infra and number == 0:
                    continue
                observations.append(row)
                grade = grade_run(
                    spec=spec,
                    obs=obs,
                    meta=meta,
                    tickets=tickets,
                    users=users,
                    grading_rules=DEV_GRADING_RULES,
                )
                grades[spec.task_id] = asdict(grade)
                counts_row = counts_row | {"tcr": grade.tcr.passed}
                manifest["effect_counts"][spec.task_id] = counts_row
                manifest["models_by_task"][spec.task_id] = sorted(task_models)
                manifest["trace_ids"][spec.task_id] = result.task_id
                if record:
                    reasons = []
                    if summary is None or not summary.golden_eligible:
                        reasons.append(
                            "non_synthetic_a_call"
                            if summary
                            else "a_recording_rejected"
                        )
                    if rejected:
                        reasons.append("llm_recording_rejected")
                    if infra:
                        reasons.append("infrastructure_failure")
                    eligible = not reasons
                    failed = (
                        golden.tcr_status(
                            spec,
                            obs,
                            meta=meta,
                            tickets=tickets,
                            users=users,
                            grading_rules=DEV_GRADING_RULES,
                        )
                        == "recorded_fail"
                    )
                    status = {
                        "profile": "writes",
                        "eligible": eligible,
                        "golden_eligible": eligible,
                        "excluded_reason": reasons[0] if reasons else None,
                        "reason_codes": reasons,
                        "recorded_fail": failed,
                        "attempts": len(attempts),
                        "status": ("recorded_fail" if failed else "pass")
                        if eligible
                        else "excluded",
                    }
                    if eligible:
                        status["observation_digest"] = golden.digest_for(spec, obs)
                    statuses[spec.task_id] = status
                    for source, destination in (
                        (llm_path, root / "data/replay/llm"),
                        (summary.path if summary else None, root / "data/replay/a_run"),
                    ):
                        if source is not None:
                            destination.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(
                                source, destination / f"{spec.task_id}.jsonl"
                            )
                        else:
                            (destination / f"{spec.task_id}.jsonl").unlink(
                                missing_ok=True
                            )
                    _write(status_path, statuses)
                print(
                    f"{spec.task_id}: TCR {'통과' if grade.tcr.passed else '실패'}; 누락 {counts.missing}; 중복 {counts.duplicates}; 미승인 {counts.unapproved}"
                )
                break
    finally:
        if not smoke:
            after = costs()
            manifest["costs_by_purpose"] = {
                key: after.get(key, 0) - before.get(key, 0)
                for key in after.keys() | before.keys()
            }
            mixed = sorted(
                key
                for key, models in manifest["models_by_task"].items()
                if len(models) > 1
            )
            manifest["mixed_model_tasks"] = {"tasks": mixed, "count": len(mixed)}
            manifest["mixed_model_tcr"] = {
                "passed": sum(grades[key]["tcr"]["passed"] for key in mixed),
                "total": len(mixed),
            }
            mismatch_tasks = sorted(manifest["tool_trace_mismatch"])
            manifest["n_registration_blocked"] = bool(mismatch_tasks)
            manifest["n_registration_mismatch_tasks"] = mismatch_tasks
            for capture, attempt, attempt_dir in attempt_captures:
                attempt["late_tool_calls"] = capture.late_calls
                _write(attempt_dir / "attempt.json", attempt)
            manifest["late_tool_calls"] = {
                key: sum(attempt["late_tool_calls"] for attempt in attempts)
                for key, attempts in manifest["attempts"].items()
            }
            manifest["finished_at"] = datetime.now(UTC).isoformat()
            _write(directory / "manifest.json", manifest)
            _write(directory / "grades.json", grades)
            (directory / "observations.jsonl").write_text(
                "".join(
                    scrub_secrets(json.dumps(row, ensure_ascii=False)) + "\n"
                    for row in observations
                )
            )
            configure([])
    if smoke:
        print(f"smoke task 수: {len(smoke_results)}")
        return {"results": smoke_results}
    n = candidate(
        [row for row in observations if row["task_id"] not in mismatch_tasks],
        {key: grade["tcr"]["passed"] for key, grade in grades.items()},
    )
    notice = (
        f"N 등록 보류: tool_trace_mismatch {len(mismatch_tasks)}건({', '.join(mismatch_tasks)})"
        " — 원인을 확인하기 전에는 N을 등록하지 않는다; 결과 문서 첫머리에 적을 것"
        if mismatch_tasks
        else f"N={n}: 잠정치이며 등록값이 아님"
    )
    if mismatch_tasks:
        print(notice)
    print(
        f"TCR {sum(g['tcr']['passed'] for g in grades.values())}/{len(grades)}; 인프라 실패 {manifest['infrastructure_failures']}; 절대 규칙 위반 task {len(manifest['absolute_rule_violations'])}"
    )
    print(notice)
    return {
        "directory": str(directory),
        "manifest": manifest,
        "grades": grades,
        "observations": observations,
    }


async def _live(specs, metadata, *, smoke=False):
    url = env_or("GOVOPS_DATABASE_URL", "")
    if not url or url == "memory":
        raise ValueError("postgres_required")
    register_secret_values(
        value
        for key, value in os.environ.items()
        if any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    )
    admin = MockAdmin(ROOT)
    a_client = AMcpClient(env_or("KODOCS_MCP_URL", ""))

    def runner(command, *, cwd):
        if command[0] != "postgres":
            return subprocess_runner(command, cwd=cwd)
        try:
            with psycopg.connect(url, connect_timeout=10) as connection:
                connection.execute("SELECT 1").fetchone()
            return subprocess.CompletedProcess(command, 0, "")
        except Exception:  # noqa: BLE001 — DSN/DB exception must never escape
            return subprocess.CompletedProcess(command, 1, "")

    def preflight():
        with httpx.Client(trust_env=False) as http:
            return run_preflight(
                root=ROOT,
                runner=runner,
                http=http,
                env=env_or,
                mock_admin=admin,
                a_check=lambda: asyncio.run(
                    check_contract(a_client, user_id=specs[0].user)
                ),
            )

    # Run preflight before opening PostgreSQL or creating a ledger/client.
    failures = await asyncio.to_thread(preflight)
    if failures:
        for code in failures:
            if smoke:
                _smoke_fail("approve", "preflight", reason=_smoke_code(code))
            else:
                print(code)
        if smoke:
            return {"results": [], "exit_code": 1}
        if "service_down:litellm" in failures:
            print(
                "LiteLLM model/info: 에이전트 가상 키 인증·조회 권한 또는 서비스 확인 필요"
            )
        raise ValueError(failures[0])
    ledger = CostLedger()

    def costs():
        return {
            report.purpose: report.settled_usd for report in cost_report(ledger.root)
        }

    async with (
        httpx.AsyncClient(trust_env=False) as rest,
        open_checkpointer(url) as saver,
    ):
        tools = ToolRuntime(
            a_client,
            JiraClient(
                env_or("MOCK_JIRA_URL", "http://127.0.0.1:8081"), http_client=rest
            ),
            SlackClient(
                env_or("MOCK_SLACK_URL", "http://127.0.0.1:8082"), http_client=rest
            ),
        )

        async def seed_check():
            if not await asyncio.to_thread(mock_seed_check, ROOT):
                return False
            tickets = json.loads((ROOT / "data/seed/tickets.json").read_text())[
                "tickets"
            ]
            users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
            return await read_seed_matches(tools.jira, tickets, users)

        return await run_dev_m3(
            specs,
            root=ROOT,
            chat=LiteLLMChatClient(ledger=ledger),
            tools=tools,
            mock_admin=admin,
            seed_check=seed_check,
            metadata=metadata,
            costs=costs,
            saver=saver,
            outbox=PostgresOutboxStore(url),
            preflight=lambda: failures,
            smoke=smoke,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    try:
        specs = (
            smoke_specs()
            if args.smoke
            else select_tasks(
                json.loads((ROOT / "data/eval/tasks.json").read_text()),
                args.tasks.split(",") if args.tasks is not None else None,
                smoke=args.smoke,
            )
        )
        if not args.smoke and len(specs) != 20:
            raise ValueError("dev_task_count_mismatch")
        metadata = (
            {}
            if args.smoke
            else git_metadata_m3(
                ROOT,
                Path(
                    env_or("A_ROOT", "~/PycharmProjects/ko-docs-agentic-rag")
                ).expanduser(),
            )
        )
        result = asyncio.run(_live(specs, metadata, smoke=args.smoke))
        if args.smoke:
            return result.get("exit_code", 1)
    except BudgetExceededError:
        if args.smoke:
            _smoke_fail(
                "approve", "run", error="BudgetExceededError", reason="budget_exceeded"
            )
            return 1
        print("예산 중단: ADR-013 축소 순서를 따른다")
        return 2
    except Exception as error:  # noqa: BLE001 — safe CLI boundary
        if args.smoke and args.tasks is None:
            _smoke_fail(
                "approve",
                "preflight",
                error=type(error).__name__,
                reason=_smoke_code(str(error)),
            )
            return 1
        safe = {
            "postgres_required",
            "profile_mismatch",
            "hypotheses_m3_not_committed",
            "dirty_worktree",
            "test_task_refused",
            "partial_dev_refused",
            "dev_task_count_mismatch",
            "invalid_task_selection",
            "mock_seed_mismatch",
            "stale_mock_image",
            "litellm_config_stale",
        }
        code = str(error) if isinstance(error, ValueError) else ""
        print(
            code
            if code in safe
            or code
            in {
                f"service_down:{name}"
                for name in ("postgres", "litellm", "mock-jira", "mock-slack", "a-mcp")
            }
            else "dev 실행 실패: 설정·계약·시드·기록을 확인하세요"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

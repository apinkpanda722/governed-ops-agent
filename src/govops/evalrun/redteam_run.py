"""User-run sequential live redteam evaluation; artifacts contain no bodies."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg

from govops.agent.approval import ANNOUNCE_APPROVER_MIN_GRADE
from govops.agent.checkpointer import open_checkpointer
from govops.agent.users import load_users
from govops.api import wiring
from govops.costs import BudgetExceededError, CostLedger, cost_report
from govops.env import env_or
from govops.eval.duplicates import count_effects
from govops.eval.redteam_judge import (
    RAW_SCHEMA,
    v2_flags_from_audit,
    v2_span_ms_from_trace,
    validate_raw,
)
from govops.eval.spec import ApprovalStep
from govops.evalrun import dev_m3, overlay_env, run_guards
from govops.evalrun.dev import (
    ROOT,
    _git,
    _write,
    infrastructure_failure,
    resolve_index_name,
)
from govops.evalrun.driver import ApprovalDriver
from govops.evalrun.mock_admin import MockAdmin, subprocess_runner
from govops.evalrun.preflight import run_preflight
from govops.evalrun.tool_trace import extract_tool_trace
from govops.gateway.client import LiteLLMChatClient
from govops.gateway.hook_digest import compute_digests
from govops.gateway.team_keys import TeamKeyResolver
from govops.obs.jsonl import JsonlSink as _JsonlSink
from govops.obs.mask import register_secret_values
from govops.obs.trace import configure
from govops.tools.a_mcp import AMcpClient, check_contract
from govops.tools.contract import snapshot_sha256
from govops.tools.jira import JiraClient
from govops.tools.outbox import PostgresOutboxStore
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient

_HASH_FILES = {
    "set_sha256": "data/redteam/set.json",
    "overlay_sha256": overlay_env.OVERLAY_RELATIVE,
    "confirmed_sha256": "data/redteam/review/confirmed.json",
    "allocation_sha256": "data/redteam/allocation.json",
    "registration_sha256": run_guards.REDTEAM_RUN_REGISTRATION,
}
_SAFE_CODES = (
    run_guards.GUARD_CODES
    | {
        "invalid_arguments",
        "partial_set_refused",
        "requester_unknown",
        "invalid_set",
        "overlay_mismatch",
        "baseline_not_clean",
        "postgres_required",
        "team_keys_missing",
        "budget_exceeded",
        "stale_mock_image",
        "litellm_config_stale",
        "guardrail_hook_stale",
        "compose_project_missing",
        "mock_build_missing",
        "a_metadata_missing",
    }
    | {
        "service_down:" + name
        for name in ("postgres", "litellm", "mock-jira", "mock-slack", "a-mcp")
    }
)


class JsonlSink(_JsonlSink):
    """Keep span timings and structural attributes, removing all content fields."""

    def emit(self, record):
        content_keys = {
            "input.request",
            "output.answer",
            "output.draft",
            "tool.args",
            "tool.result",
        }

        def without_bodies(value):
            if isinstance(value, dict):
                return {
                    key: without_bodies(child)
                    for key, child in value.items()
                    if key not in content_keys
                }
            if isinstance(value, list):
                return [without_bodies(child) for child in value]
            return value

        super().emit(replace(record, attributes=without_bodies(record.attributes)))


@dataclass(frozen=True)
class _ItemAdapter:
    user: str
    request: str
    approval_script: tuple[ApprovalStep, ...]


def item_adapter(item, users):
    owner = users[item["requester"]]
    script = [ApprovalStep(actor="requester", decision="reject", expect_http=None)]
    if any(
        user.team == owner.team
        and user.grade >= ANNOUNCE_APPROVER_MIN_GRADE
        and user.label != owner.label
        for user in users.values()
    ):
        script.append(
            ApprovalStep(
                actor="same_team_eligible", decision="reject", expect_http=None
            )
        )
    return _ItemAdapter(owner.label, item["request"], tuple(script))


def load_items(root, users):
    try:
        rows = json.loads((root / "data/redteam/set.json").read_bytes())
    except (OSError, ValueError):
        raise ValueError("invalid_set") from None
    if not isinstance(rows, list):
        raise ValueError("invalid_set")  # noqa: TRY004 — safe CLI data error
    if len(rows) != 120:
        raise ValueError("partial_set_refused")
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid_set")
    if any(row.get("requester") not in users for row in rows):
        raise ValueError("requester_unknown")
    ids = [row.get("id") for row in rows]
    if (
        any(
            not isinstance(key, str) or re.fullmatch(r"RT-[DIPX]-[0-9]{2}", key) is None
            for key in ids
        )
        or len(set(ids)) != 120
    ):
        raise ValueError("invalid_set")
    return rows


def _ticket_ids(value):
    ids = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "ticket_id" and isinstance(child, str):
                ids.add(child)
            elif isinstance(child, (dict, list)):
                ids.update(_ticket_ids(child))
    elif isinstance(value, list):
        for child in value:
            ids.update(_ticket_ids(child))
    return ids


def _without_baseline(reports, baseline):
    """Copy of the mock report without the seed comments present at attempt start."""
    jira = reports.get("jira", {})
    return {
        **reports,
        "jira": {
            **jira,
            "comments": [
                c
                for c in jira.get("comments", [])
                if c.get("comment_id") not in baseline
            ],
        },
    }


async def _baseline(mock_admin, root):
    """Seed comment IDs of the freshly reset state, checked against the overlay file."""
    fresh = await dev_m3._invoke(mock_admin.state, bodies=True)
    ids = {
        c["comment_id"]
        for c in fresh.get("jira", {}).get("comments", [])
        if isinstance(c.get("comment_id"), str)
    }
    try:
        expected = {c["comment_id"] for c in overlay_env.overlay_seed_comments(root)}
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("baseline_not_clean") from None
    counts = count_effects(
        _without_baseline(fresh, ids), task_id="baseline", expected=[]
    )
    if (
        ids != expected
        or counts.unapproved
        or counts.unmarked
        or any(actual for actual, _ in counts.per_key.values())
    ):
        raise ValueError("baseline_not_clean")
    return ids


def _raw(
    item,
    spec,
    result,
    captured,
    reports,
    *,
    directory,
    attempt_dir,
    config,
    run_label,
    infra,
    baseline=frozenset(),
):
    _, tool_results = extract_tool_trace(captured.results)
    counts = count_effects(
        _without_baseline(reports, baseline), task_id=result.task_id, expected=[]
    )
    view = result.view
    guardrail, check = view.get("guardrail") or {}, view.get("check") or {}
    trace_path = directory / "traces" / f"{result.task_id}.jsonl"
    row = {
        "schema": RAW_SCHEMA,
        "item_id": item["id"],
        "run_label": run_label,
        "config": config,
        **{
            key: item[key]
            for key in (
                "category",
                "variant",
                "delivery",
                "expected_layer",
                "fallback_layers",
                "requester",
                "payload_ref",
            )
        },
        "view_status": view["status"],
        "block_code": (view.get("block") or {}).get("code"),
        "check_status": check.get("status"),
        "check_reason": dev_m3.check_reason(view),
        "quarantined": guardrail.get("quarantined", 0),
        "quarantined_at_least": guardrail.get("quarantined_at_least", False),
        "masked": guardrail.get("masked", 0),
        "approvals": [
            {"decision": a.decision, "http_status": a.http_status}
            for a in result.approvals
        ],
        "approver_available": len(spec.approval_script) == 2,
        "plan_remaining": result.plan_remaining,
        "effect_count": sum(actual for actual, _ in counts.per_key.values()),
        "duplicates": counts.duplicates,
        "unapproved": counts.unapproved,
        "tool_result_ticket_ids": sorted(
            set().union(*(_ticket_ids(row["result"]) for row in tool_results))
        ),
        "timed_out": result.timed_out,
        "unexpected_http": result.unexpected_http,
        "infrastructure_failure": infra,
        "trace_id": result.task_id,
        **v2_flags_from_audit(attempt_dir / "audit"),
        "v2_span_ms": v2_span_ms_from_trace(trace_path)
        if trace_path.exists()
        else None,
    }
    validate_raw(row)
    return row


async def run_redteam(
    items,
    *,
    root,
    users,
    config,
    run_label,
    chat,
    tools,
    mock_admin,
    seed_check,
    costs,
    metadata,
    saver=None,
    outbox=None,
    driver_kwargs=None,
    service_factory=None,
):
    """Injected execution seam; only the CLI loads the complete registered set."""
    directory = root / "data/results/redteam" / uuid4().hex
    switch = env_or("GOVOPS_GUARDRAIL_V2", "off")
    before = costs()
    manifest = {
        **metadata,
        "run_label": run_label,
        "config": config,
        "switch": switch,
        "m6_run": run_label,
        **{
            key: hashlib.sha256((root / path).read_bytes()).hexdigest()
            for key, path in _HASH_FILES.items()
        },
        "guardrail_v2": {
            "switch": switch,
            "cfg_sha256": chat.config_sha256() if switch == "on" else None,
            "status_counts": {},
            "runner_concurrency": 1,
        },
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "item_count": 0,
        "infrastructure_failures": 0,
        "supersedes": None,
        "costs_by_purpose": {},
    }
    configure([JsonlSink(directory / "traces")])
    try:
        for item in items:
            spec = item_adapter(item, users)
            for number in (1, 2):
                await dev_m3._invoke(mock_admin.reset)
                if not await dev_m3._invoke(seed_check):
                    raise ValueError("overlay_mismatch")
                baseline = await _baseline(mock_admin, root)
                attempt_dir = directory / "attempts" / item["id"] / str(number)
                captured_chat = dev_m3._GuardrailCapture(chat)
                if switch == "on" and callable(getattr(chat, "classify", None)):
                    captured_chat.classify = chat.classify
                captured_tools = dev_m3._TaskTools(tools, tools, task_id=uuid4().hex)
                service = None
                try:
                    kwargs = {
                        "chat": captured_chat,
                        "tools": captured_tools,
                        "saver": saver,
                        "outbox": outbox,
                        "profile": dev_m3.PROFILE,
                        "audit_dir": attempt_dir / "audit",
                    }
                    service = (
                        service_factory(**kwargs)
                        if service_factory
                        else wiring.build_task_service(**kwargs)
                    )
                    result = await ApprovalDriver(
                        service, service.users, **(driver_kwargs or {})
                    ).run(spec)
                finally:
                    captured_tools.close()
                    if service is not None:
                        await service.aclose()
                errors = captured_chat.errors + captured_tools.errors
                infra = any(infrastructure_failure(error) for error in errors)
                manifest["infrastructure_failures"] += int(infra)
                reports = await dev_m3._invoke(mock_admin.state, bodies=True)
                raw = _raw(
                    item,
                    spec,
                    result,
                    captured_tools,
                    reports,
                    directory=directory,
                    attempt_dir=attempt_dir,
                    config=config,
                    run_label=run_label,
                    infra=infra,
                    baseline=baseline,
                )
                _write(attempt_dir / "raw.json", raw)
                if any(isinstance(error, BudgetExceededError) for error in errors):
                    raise BudgetExceededError("budget_exceeded")
                if infra and number == 1:
                    continue
                _write(directory / "items" / f"{item['id']}.json", raw)
                manifest["item_count"] += 1
                break
    finally:
        try:
            after = costs()
            manifest["costs_by_purpose"] = {
                key: after.get(key, 0) - before.get(key, 0)
                for key in before.keys() | after.keys()
            }
            counts = chat.status_counts() if switch == "on" else {}
            manifest["guardrail_v2"]["status_counts"] = counts
            manifest["v2_validity"] = run_guards.v2_validity(switch, counts)
            manifest["validity"] = run_guards.validity_label(manifest["v2_validity"])
            manifest["finished_at"] = datetime.now(UTC).isoformat()
            _write(directory / "manifest.json", manifest)
        finally:
            configure([])
    return {"directory": str(directory), "manifest": manifest}


async def _live(items, users, *, config, run_label, metadata):
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
        except Exception:  # noqa: BLE001 — DSN never escapes
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
                    check_contract(a_client, user_id=items[0]["requester"])
                ),
            )

    failures = await asyncio.to_thread(preflight)
    if failures:
        for code in failures:
            print(code if code in _SAFE_CODES else "redteam_run_failed")
        raise ValueError(failures[0])
    async with httpx.AsyncClient(trust_env=False) as rest:
        tools = ToolRuntime(
            a_client,
            JiraClient(
                env_or("MOCK_JIRA_URL", "http://127.0.0.1:8081"), http_client=rest
            ),
            SlackClient(
                env_or("MOCK_SLACK_URL", "http://127.0.0.1:8082"), http_client=rest
            ),
            policy=dev_m3.new_tool_policy(),
        )

        async def seed_check():
            def seed_counts_match():
                return overlay_env.overlay_seed_check(ROOT)

            if not await asyncio.to_thread(seed_counts_match):
                return False
            base = json.loads((ROOT / "data/seed/tickets.json").read_bytes())["tickets"]
            overlay = json.loads((ROOT / overlay_env.OVERLAY_RELATIVE).read_bytes())[
                "tickets"
            ]
            seed_users = json.loads((ROOT / "data/seed/users.json").read_bytes())[
                "users"
            ]
            return await overlay_env.read_overlay_matches(
                tools.jira, base, overlay, seed_users
            )

        if not await seed_check():
            raise ValueError("overlay_mismatch")
        resolver = TeamKeyResolver({label: user.team for label, user in users.items()})
        if resolver.missing():
            raise ValueError("team_keys_missing")
        ledger = CostLedger()

        def costs():
            return {
                report.purpose: report.settled_usd
                for report in cost_report(ledger.root)
            }

        async with open_checkpointer(url) as saver:
            return await run_redteam(
                items,
                root=ROOT,
                users=users,
                config=config,
                run_label=run_label,
                metadata=metadata,
                tools=tools,
                mock_admin=admin,
                seed_check=seed_check,
                costs=costs,
                saver=saver,
                outbox=PostgresOutboxStore(url),
                chat=LiteLLMChatClient(
                    ledger=ledger,
                    key_resolver=resolver.key_for,
                    expected_digests=compute_digests(ROOT),
                    require_guardrail=True,
                ),
            )


def _metadata(root):
    # Same A HEAD/index probes as dev.git_metadata, without unrelated dev guards.
    a_root = Path(
        env_or("A_ROOT", "~/PycharmProjects/ko-docs-agentic-rag")
    ).expanduser()
    head = _git(a_root, "rev-parse", "HEAD")[1]
    index = resolve_index_name(a_root, env_or("KODOCS_INDEX_NAME", ""))
    if not head or not index["index_name"]:
        raise ValueError("a_metadata_missing")
    return {
        "a_git_head": head,
        "commit_hash": _git(root, "rev-parse", "HEAD")[1],
        "contract_snapshot_sha256": snapshot_sha256(),
        **index,
        "warnings": [] if head.startswith("d2eec9c") else ["a_head_differs_d2eec9c"],
    }


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def main(argv=None):
    parser = _Parser(description=__doc__)
    parser.add_argument("--config", choices=run_guards.RUN_CONFIGS, required=True)
    parser.add_argument("--run", choices=("R1a", "R1b", "R2"), required=True)
    try:
        args = parser.parse_args(argv)
        run_guards.guard_switch(args.config, env_or("GOVOPS_GUARDRAIL_V2", "off"))
        run_guards.guard_redteam_registration(ROOT)
        run_guards.guard_redteam_set(ROOT)
        run_guards.guard_clean_worktree(ROOT)
        run_guards.guard_fresh_status_counts(LiteLLMChatClient.status_counts())
        users = load_users()
        items = load_items(ROOT, users)
        asyncio.run(
            _live(
                items,
                users,
                config=args.config,
                run_label=args.run,
                metadata=_metadata(ROOT),
            )
        )
    except Exception as error:  # noqa: BLE001 — never print request-bearing errors
        code = (
            "budget_exceeded"
            if isinstance(error, BudgetExceededError)
            else str(error)
            if isinstance(error, ValueError)
            else ""
        )
        print(code if code in _SAFE_CODES else "redteam_run_failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

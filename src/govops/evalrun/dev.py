"""One live M2 dev run with simultaneous credential-free record-through."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from langgraph.checkpoint.memory import InMemorySaver

from govops.agent.checkpointer import open_checkpointer
from govops.agent.graph import build_graph, run_task
from govops.agent.prompts import SYSTEM_PROMPT
from govops.costs import BudgetExceededError, CostLedger, cost_report
from govops.env import env_or
from govops.eval.meta import load_meta
from govops.eval.run_grade import build_observation, grade_run
from govops.eval.spec import TaskSpec
from govops.evalrun.explore import NOTICE, candidate
from govops.evalrun.tool_trace import structure as _structure
from govops.gateway.client import _MODEL, GatewayError, LiteLLMChatClient
from govops.golden import digest_for, tcr_status
from govops.obs.jsonl import JsonlSink
from govops.obs.mask import register_secret_values, scrub_secrets
from govops.obs.trace import trace
from govops.replay.a_through import RecordingToolExecutor
from govops.replay.llm import RecordingChatClient, prompt_hash
from govops.replay.recordings import RecordingRejected
from govops.tools.a_mcp import AMcpClient, check_contract
from govops.tools.contract import snapshot_sha256
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolRuntime, agent_tool_definitions
from govops.tools.slack import SlackClient

ROOT = Path(__file__).resolve().parents[3]
PARAMS = {}


def select_tasks(rows, requested=None):
    specs = [TaskSpec.model_validate(row) for row in rows]
    if requested is None:
        return [s for s in specs if s.split == "dev" and s.executable_in == "m2"]
    if not requested or len(set(requested)) != len(requested):
        raise ValueError("invalid_task_selection")
    by_id = {s.task_id: s for s in specs}
    selected = []
    for key in requested:
        if key not in by_id:
            raise ValueError("unknown_task")
        spec = by_id[key]
        if spec.split != "dev":
            raise ValueError("test_task_refused")
        if spec.executable_in != "m2":
            raise ValueError("m3_task_refused")
        selected.append(spec)
    return selected


def guard_live(metadata):
    if not metadata.get("hypotheses_tracked") or not metadata.get("hypotheses_in_head"):
        raise ValueError("hypotheses_not_committed")
    if not metadata.get("worktree_clean"):
        raise ValueError("dirty_worktree")


def infrastructure_failure(error):
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(error, GatewayError):
        code = str(error)
        return code in {"gateway timeout", "gateway connection failure"} or code in {
            f"gateway HTTP {status}" for status in range(500, 600)
        }
    return False


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, indent=2)
    if scrub_secrets(content) != content:
        raise RecordingRejected("unsafe artifact")
    path.write_text(content + "\n")


class _Capture:
    def __init__(self, inner):
        self.inner = inner
        self.errors = []
        self.results = []
        self.models = set()
        self.unmapped_models = set()
        self.fallback_used = False

    async def complete(self, **kwargs):
        try:
            result = await self.inner.complete(**kwargs)
        except Exception as error:
            self.errors.append(error)
            raise
        self.models.add(result.model)
        if result.unmapped_model:
            self.unmapped_models.add(
                result.deployment_id
                if result.deployment_id is not None
                else result.model
            )
        self.fallback_used |= result.fallback_used
        return result

    async def call(self, name, arguments, **kwargs):
        try:
            result = await self.inner.call(name, arguments, **kwargs)
        except Exception as error:
            self.errors.append(error)
            raise
        # Keep only structural IDs, never document bodies or credentials.
        self.results.append(
            {
                "name": name,
                "ok": not result.is_error,
                "result": _structure(result.payload),
            }
        )
        return result


async def run_dev(
    specs,
    *,
    root,
    chat,
    tools,
    seed_check,
    metadata,
    costs,
    saver=None,
    evaluation_root=ROOT,
):
    # No injected client can bypass the selection or registration guards.
    specs = select_tasks([s.model_dump() for s in specs], [s.task_id for s in specs])
    guard_live(metadata)
    run_id = uuid4().hex
    directory = root / "data/results/dev" / run_id
    manifest = dict(
        metadata,
        mode="live",
        model_id=_MODEL,
        call_params=PARAMS,
        prompt_hash=prompt_hash(
            system_prompt=SYSTEM_PROMPT,
            tools=agent_tool_definitions(),
            model=_MODEL,
            params=PARAMS,
        ),
        contract_snapshot_sha256=snapshot_sha256(),
        fallback_used=False,
        models_seen=[],
        unmapped_models=[],
        started_at=datetime.now(UTC).isoformat(),
        finished_at=None,
        trace_ids={},
        infrastructure_failures=0,
        attempts={},
        costs_by_purpose={},
        warnings=[]
        if str(metadata["a_git_head"]).startswith("d2eec9c")
        else ["a_head_differs_d2eec9c"],
    )
    before = costs()
    grades = {}
    observations = []
    status_path = root / "data/replay/golden/status.json"
    statuses = json.loads(status_path.read_text()) if status_path.exists() else {}
    meta = load_meta(evaluation_root / "data/eval/meta.json")
    tickets = json.loads((evaluation_root / "data/seed/tickets.json").read_text())[
        "tickets"
    ]
    users = json.loads((evaluation_root / "data/seed/users.json").read_text())["users"]
    directory.mkdir(parents=True)
    try:
        for spec in specs:
            attempts = manifest["attempts"][spec.task_id] = []
            for number in range(2):
                if not await seed_check():
                    raise ValueError(
                        "mock_seed_mismatch: mock 시드를 복원한 뒤 실행하세요"
                    )
                attempt_dir = directory / "attempts" / f"{spec.task_id}-{number + 1}"
                response_capture = _Capture(chat)
                recording_chat = RecordingChatClient(
                    response_capture,
                    task_id=spec.task_id,
                    directory=attempt_dir / "llm",
                    system_prompt=SYSTEM_PROMPT,
                    tools=agent_tool_definitions(),
                    model=_MODEL,
                    params=PARAMS,
                )
                recording_tools = RecordingToolExecutor(
                    tools, task_id=spec.task_id, directory=attempt_dir / "a_run"
                )
                captured_chat, captured_tools = (
                    _Capture(recording_chat),
                    _Capture(recording_tools),
                )
                trace_id = uuid4().hex
                with trace(
                    trace_id, user_id=spec.user, sinks=[JsonlSink(directory / "traces")]
                ):
                    state = await run_task(
                        build_graph(
                            captured_chat,
                            captured_tools,
                            saver if saver is not None else InMemorySaver(),
                        ),
                        task_id=trace_id,
                        user_id=spec.user,
                        request=spec.request,
                    )
                errors = captured_chat.errors + captured_tools.errors
                budget = any(isinstance(error, BudgetExceededError) for error in errors)
                infra = any(infrastructure_failure(error) for error in errors)
                rejected = any(isinstance(error, RecordingRejected) for error in errors)
                llm_path = None
                try:
                    llm_path = recording_chat.finish()
                except RecordingRejected:
                    rejected = True
                summary = recording_tools.finish()
                manifest["models_seen"] = sorted(
                    set(manifest["models_seen"]) | response_capture.models
                )
                manifest["unmapped_models"] = sorted(
                    set(manifest["unmapped_models"]) | response_capture.unmapped_models
                )
                manifest["fallback_used"] |= response_capture.fallback_used
                manifest["infrastructure_failures"] += int(infra)
                attempt = {
                    "attempt": number + 1,
                    "trace_id": trace_id,
                    "infrastructure_failure": infra,
                    "budget_exceeded": budget,
                    "llm_recorded": llm_path is not None,
                    "a_calls": summary.stats.total,
                    "a_skipped": summary.stats.skipped,
                }
                attempts.append(attempt)
                obs = build_observation(
                    spec=spec,
                    run_index=number,
                    state=state,
                    tool_calls=[
                        {"name": r["name"], "ok": r["ok"]}
                        for r in captured_tools.results
                    ],
                    trace_id=trace_id,
                )
                row = obs.model_copy(
                    update={"tool_results": captured_tools.results}
                ).model_dump()
                _write(attempt_dir / "observation.json", row)
                if budget:
                    raise BudgetExceededError("예산 중단: ADR-013 축소 순서를 따른다")
                if infra and number == 0:
                    continue
                observations.append(row)
                grade = grade_run(
                    spec=spec, obs=obs, meta=meta, tickets=tickets, users=users
                )
                grades[spec.task_id] = asdict(grade)
                eligible = summary.golden_eligible and not rejected and not infra
                reasons = (
                    ["non_synthetic_a_call"] if not summary.golden_eligible else []
                )
                if rejected:
                    reasons.append("llm_recording_rejected")
                if infra:
                    reasons.append("infrastructure_failure")
                observation_digest = digest_for(spec, obs)
                recorded_fail = (
                    tcr_status(spec, obs, meta=meta, tickets=tickets, users=users)
                    == "recorded_fail"
                )
                status = {
                    "eligible": eligible,
                    "golden_eligible": eligible,
                    "excluded_reason": reasons[0] if reasons else None,
                    "reason_codes": reasons,
                    "recorded_fail": recorded_fail,
                    "attempts": len(attempts),
                    "status": ("recorded_fail" if recorded_fail else "pass")
                    if eligible
                    else "excluded",
                }
                if eligible:
                    status["observation_digest"] = observation_digest
                statuses[spec.task_id] = status
                for source, destination in [
                    (llm_path, root / "data/replay/llm"),
                    (summary.path, root / "data/replay/a_run"),
                ]:
                    if source is not None:
                        destination.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination / f"{spec.task_id}.jsonl")
                    else:
                        (destination / f"{spec.task_id}.jsonl").unlink(missing_ok=True)
                manifest["trace_ids"][spec.task_id] = trace_id
                _write(status_path, statuses)
                break
    finally:
        after = costs()
        manifest["costs_by_purpose"] = {
            key: after[key] - before.get(key, 0) for key in after
        }
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        _write(directory / "manifest.json", manifest)
        _write(directory / "grades.json", grades)
        with (directory / "observations.jsonl").open("w") as stream:
            for row in observations:
                stream.write(scrub_secrets(json.dumps(row, ensure_ascii=False)) + "\n")
    adopted = [
        row
        for row in observations
        if row["trace_id"] == manifest["trace_ids"].get(row["task_id"])
    ]
    n = candidate(
        adopted, {key: value["tcr"]["passed"] for key, value in grades.items()}
    )
    for key, grade in grades.items():
        print(
            f"{key}: TCR {'통과' if grade['tcr']['passed'] else '실패'}; 노출 후보(확정 대기): {len(grade['rules']['leak_candidates'])}"
        )
    selected_statuses = {spec.task_id: statuses[spec.task_id] for spec in specs}
    print(
        f"골든 자격 task 수: {sum(s['eligible'] for s in selected_statuses.values())}; 제외 사유: "
        f"{ {key: s['reason_codes'] for key, s in selected_statuses.items() if not s['eligible']} }"
    )
    total = sum(a["a_calls"] for aa in manifest["attempts"].values() for a in aa)
    skipped = sum(a["a_skipped"] for aa in manifest["attempts"].values() for a in aa)
    print(
        f"인프라 실패: {manifest['infrastructure_failures']}; 건너뛴 A 호출 비율: {skipped}/{total}; 비용: {manifest['costs_by_purpose']}"
    )
    print(f"N={n}: {NOTICE}")
    return {"directory": str(directory), "manifest": manifest, "grades": grades}


def _git(root, *args):
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout.strip()


A_INDEX_FILE = "src/kodocs/tools/deps.py"
_A_INDEX = re.compile(r'^TOOL_INDEX\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)


def resolve_index_name(a_root, env_value: str) -> dict:
    """A MCP 서버가 쓰는 인덱스 이름. A 저장소의 설정(`TOOL_INDEX`)을 읽기 전용으로 읽고, 읽을 수
    없을 때만 사람이 적은 환경 변수 값을 쓴다(출처를 함께 남긴다). A 저장소는 수정하지 않는다."""
    try:
        found = _A_INDEX.search(
            (Path(a_root) / A_INDEX_FILE).read_text(encoding="utf-8")
        )
    except OSError:
        found = None
    if found:
        result = {
            "index_name": found.group(1),
            "index_name_source": f"a_repo:{A_INDEX_FILE}:TOOL_INDEX",
        }
        if env_value and env_value != found.group(1):
            result["index_name_env_mismatch"] = (
                True  # 사용자 입력이 A 설정과 다름(A 값을 기록)
            )
        return result
    return {"index_name": env_value, "index_name_source": "env(사용자 입력)"}


def git_metadata(root, a_root):
    relative = "docs/results/hypotheses.md"
    status_code, worktree_status = _git(root, "status", "--porcelain")
    metadata = {
        "hypotheses_tracked": _git(root, "ls-files", "--error-unmatch", relative)[0]
        == 0,
        "hypotheses_in_head": _git(root, "cat-file", "-e", f"HEAD:{relative}")[0] == 0,
        "worktree_clean": status_code == 0 and worktree_status == "",
        "commit_hash": _git(root, "rev-parse", "HEAD")[1],
        "hypotheses_registration_commit": _git(
            root, "log", "-1", "--format=%H", "--", relative
        )[1],
        "a_git_head": _git(a_root, "rev-parse", "HEAD")[1],
    }
    metadata.update(resolve_index_name(a_root, os.getenv("KODOCS_INDEX_NAME", "")))
    guard_live(metadata)
    if not metadata["a_git_head"] or not metadata["index_name"]:
        raise ValueError("A_ROOT·KODOCS_INDEX_NAME을 확인하세요")
    metadata["hypotheses_sha256"] = hashlib.sha256(
        (root / relative).read_bytes()
    ).hexdigest()
    return metadata


def mock_seed_check(root):
    """Use existing internal admin CLI; never publish or bypass an admin port."""
    tickets = json.loads((root / "data/seed/tickets.json").read_text())["tickets"]
    users = json.loads((root / "data/seed/users.json").read_text())["users"]
    expected = [
        {"ticket_count": len(tickets), "comments": [], "children": []},
        {
            "channels": [
                {"channel_id": channel, "message_count": 0, "messages": []}
                for channel in [
                    *(f"team-{team}" for team in sorted({u["team"] for u in users})),
                    "announce",
                ]
            ]
        },
    ]
    actual = []
    for service in ("mock-jira", "mock-slack"):
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/docker-compose.yml",
                "exec",
                "-T",
                service,
                "python",
                "-m",
                "govops.mocks.admin",
                "state",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            return False
        try:
            actual.append(json.loads(result.stdout))
        except ValueError:
            return False

    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    return digest(actual) == digest(expected)


async def read_seed_matches(jira, tickets, users):
    """Compare readable ticket fields and ID hashes against the seed, without bodies."""
    actual_ids = set()
    expected_ids = {ticket["ticket_id"] for ticket in tickets}
    for ticket in tickets:
        actual = None
        for user in users:
            actual = await jira.get_ticket(user["label"], ticket["ticket_id"])
            if actual is not None:
                break
        if actual is None or any(
            (ticket | {"parent_id": None, "metadata": {}}).get(key) != value
            for key, value in actual.items()
        ):
            return False
        actual_ids.add(actual["ticket_id"])

    def digest(ids):
        return hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest()

    return digest(actual_ids) == digest(expected_ids)


async def _live(specs, metadata):
    register_secret_values(
        value
        for key, value in os.environ.items()
        if any(part in key.upper() for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
    )
    a_client = AMcpClient(os.environ["KODOCS_MCP_URL"])
    await check_contract(a_client, user_id=specs[0].user)
    ledger = CostLedger()

    def costs():
        return {
            report.purpose: report.settled_usd for report in cost_report(ledger.root)
        }

    async def seed_check():
        if not await asyncio.to_thread(mock_seed_check, ROOT):
            return False
        tickets = json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"]
        users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
        return await read_seed_matches(tools.jira, tickets, users)

    async with (
        httpx.AsyncClient(trust_env=False) as rest,
        open_checkpointer(os.getenv("GOVOPS_DATABASE_URL") or None) as saver,
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
        return await run_dev(
            specs,
            root=ROOT,
            chat=LiteLLMChatClient(ledger=ledger),
            tools=tools,
            seed_check=seed_check,
            metadata=metadata,
            costs=costs,
            saver=saver,
        )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        specs = select_tasks(
            json.loads((ROOT / "data/eval/tasks.json").read_text()),
            args.tasks.split(",") if args.tasks is not None else None,
        )
        if args.dry_run:
            print(",".join(s.task_id for s in specs))
            return 0
        metadata = git_metadata(
            ROOT,
            Path(
                env_or("A_ROOT", "~/PycharmProjects/ko-docs-agentic-rag")
            ).expanduser(),
        )
        asyncio.run(_live(specs, metadata))
    except BudgetExceededError:
        print("예산 중단: ADR-013 축소 순서를 따른다")
        return 2
    except Exception as error:  # noqa: BLE001 — credential-safe CLI boundary
        # Exception strings may retain credentials; report only known safe guards.
        if isinstance(error, ValueError) and str(error).split(":")[0] in {
            "hypotheses_not_committed",
            "dirty_worktree",
            "test_task_refused",
            "m3_task_refused",
            "mock_seed_mismatch",
            "A_ROOT·KODOCS_INDEX_NAME을 확인하세요",
        }:
            print(str(error))
        else:
            print("dev 실행 실패: 설정·계약·시드·기록을 확인하세요")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

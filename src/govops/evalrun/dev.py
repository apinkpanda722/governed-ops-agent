"""Shared dev evaluation helpers; live execution is owned by dev_m3."""

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import httpx

from govops.eval.spec import TaskSpec
from govops.evalrun.tool_trace import structure as _structure
from govops.gateway.client import GatewayError
from govops.obs.mask import scrub_secrets
from govops.replay.recordings import RecordingRejected

ROOT = Path(__file__).resolve().parents[3]


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

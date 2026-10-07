import asyncio
import json
from pathlib import Path

import httpx
import pytest

from govops.costs import BudgetExceededError
from govops.evalrun.dev import guard_live, infrastructure_failure, select_tasks
from govops.gateway.client import GatewayError

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


@pytest.mark.parametrize("change", ["clean", "ledger", "other", "git_failure"])
def test_git_probe_registration(monkeypatch, tmp_path, change):
    from govops.evalrun.dev import git_metadata

    path = tmp_path / "docs/results/hypotheses.md"
    path.parent.mkdir(parents=True)
    path.write_text("사전 등록")

    def probe(root, *args):
        if args[0] == "status":
            ledger = json.loads((tmp_path / "scripts/exempt_paths.json").read_text())[
                "ledger_paths"
            ][0]
            return (1 if change == "git_failure" else 0), (
                f" M {ledger}"
                if change == "ledger"
                else " M docs/a"
                if change == "other"
                else ""
            )
        if args[0] == "rev-parse":
            return 0, "d2eec9c" if root.name == "a" else "d" * 40
        if args[0] == "log":
            return 0, "e" * 40
        return 0, ""

    monkeypatch.setattr("govops.evalrun.dev._git", probe)
    monkeypatch.setenv("KODOCS_INDEX_NAME", "synthetic")
    if change in {"other", "git_failure"}:
        with pytest.raises(ValueError, match="dirty_worktree"):
            git_metadata(tmp_path, tmp_path / "a")
        return
    result = git_metadata(tmp_path, tmp_path / "a")
    assert result["hypotheses_registration_commit"] == "e" * 40
    import hashlib

    assert result["hypotheses_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_index_name_comes_from_a_settings_else_env_with_its_source(tmp_path):
    from govops.evalrun.dev import A_INDEX_FILE, resolve_index_name

    a_root = tmp_path / "a"
    target = a_root / A_INDEX_FILE
    target.parent.mkdir(parents=True)
    target.write_text('TOOL_INDEX = "kodocs-m5-composite-structural"\n')
    found = resolve_index_name(a_root, "")
    assert found["index_name"] == "kodocs-m5-composite-structural"
    assert found["index_name_source"].startswith("a_repo:")
    assert "index_name_env_mismatch" not in found
    # 사용자 입력이 A 설정과 다르면 A 값을 기록하고 불일치를 표시한다
    assert resolve_index_name(a_root, "kodocs-other")["index_name_env_mismatch"] is True
    # A 설정을 읽을 수 없으면 환경 변수 값과 출처를 함께 남긴다(A 저장소는 수정하지 않는다)
    assert resolve_index_name(tmp_path / "none", "kodocs-m5-x-y") == {
        "index_name": "kodocs-m5-x-y",
        "index_name_source": "env(사용자 입력)",
    }


@pytest.fixture(autouse=True)
def exempt_config(tmp_path):
    from pathlib import Path

    target = tmp_path / "scripts/exempt_paths.json"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(
        (Path(__file__).resolve().parents[1] / "scripts/exempt_paths.json").read_bytes()
    )

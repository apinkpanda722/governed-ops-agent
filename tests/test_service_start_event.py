import hashlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from govops.agent.graph import READ_ONLY
from govops.api.startup import StartupError
from govops.api.tasks import TaskService
from govops.api.wiring import build_task_service
from govops.audit.log import AuditLog, AuditWriteError, MemoryAuditLog
from govops.audit.verify import verify_chain
from govops.gateway.hook_digest import HOOK_FILES
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy

ROOT = Path(__file__).resolve().parents[1]


def build(directory):
    return build_task_service(
        chat=object(),
        tools=SimpleNamespace(jira=object(), slack=object(), policy=ToolPolicy()),
        saver=InMemorySaver(),
        outbox=InMemoryOutboxStore(),
        profile=READ_ONLY,
        audit_dir=directory,
    )


def records(directory):
    return [
        json.loads(line)
        for path in sorted(directory.glob("chain-*.jsonl"))
        for line in path.read_text().splitlines()
    ]


def test_start_hashes_and_shared_genesis(tmp_path):
    build(tmp_path)
    rows = records(tmp_path)
    assert [r["event"] for r in rows] == ["chain.genesis", "service.start"]
    start = rows[1]
    assert start["actor"]["sub"] == "system"
    assert start["task_id"] is None
    for field, name in (
        ("policy_sha256", "data/policy/team_policy.json"),
        ("access_index_sha256", "data/seed/access_index.json"),
        ("rules_sha256", "src/govops/guardrails/rules_v1.json"),
    ):
        assert re.fullmatch("[0-9a-f]{64}", start[field])
        assert start[field] == hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
    payload = "".join(
        f"{name}\0{hashlib.sha256((ROOT / name).read_bytes()).hexdigest()}\n"
        for name in HOOK_FILES
    ).encode()
    assert start["hook_code_digest"] == hashlib.sha256(payload).hexdigest()[:12]
    assert start["hook_rules_digest"] == start["rules_sha256"][:12]
    for field in ("hook_code_digest", "hook_rules_digest"):
        assert re.fullmatch("[0-9a-f]{12}", start[field])
    build(tmp_path)
    assert [r["event"] for r in records(tmp_path)] == [
        "chain.genesis",
        "service.start",
        "service.start",
    ]
    assert verify_chain(tmp_path).ok


@pytest.mark.parametrize("failure", ["missing", "permissions", "write"])
def test_start_refused(tmp_path, monkeypatch, failure):
    directory = tmp_path / "audit"
    if failure == "missing":
        monkeypatch.setenv("AUDIT_KEY_FILE", str(tmp_path / "missing.pem"))
        code = "audit_key_missing"
    elif failure == "permissions":
        Path(os.environ["AUDIT_KEY_FILE"]).chmod(0o644)
        code = "audit_key_permissions"
    else:
        parent = tmp_path / "file"
        parent.write_text("synthetic")
        directory = parent / "audit"
        code = "audit_write_failed"
    with pytest.raises(StartupError, match=f"^{code}$"):
        build(directory)
    assert not list(tmp_path.rglob("chain-*.jsonl"))


@pytest.mark.parametrize(
    "target", ["team_policy_sha256", "rules_sha256_full", "compute_digests"]
)
def test_hash_unavailable_refuses_assembly(tmp_path, monkeypatch, target):
    def unavailable(path):
        raise OSError("private-path")

    monkeypatch.setattr(f"govops.api.wiring.{target}", unavailable)
    with pytest.raises(StartupError, match="^audit_start_hash_unavailable$"):
        build(tmp_path)
    assert records(tmp_path) == []


def test_append_failure_refuses_assembly(tmp_path, monkeypatch):
    def failed_append(*args, **kwargs):
        raise AuditWriteError("private-error", code="audit_chain_corrupt")

    def forbidden_graph(*args, **kwargs):
        pytest.fail("must not assemble after audit failure")

    monkeypatch.setattr(AuditLog, "append", failed_append)
    monkeypatch.setattr("govops.api.wiring.build_graph", forbidden_graph)
    with pytest.raises(StartupError, match="^audit_write_failed$"):
        build(tmp_path)
    assert records(tmp_path) == []


def test_access_index_unavailable(tmp_path, monkeypatch):
    read_bytes = Path.read_bytes

    def read(path):
        if path == ROOT / "data/seed/access_index.json":
            raise OSError("private-path")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(StartupError, match="^audit_start_hash_unavailable$"):
        build(tmp_path)
    assert records(tmp_path) == []


@pytest.mark.parametrize("audit", [None, MemoryAuditLog()])
def test_anchor_unsupported(audit):
    with pytest.raises(RuntimeError, match="^audit_anchor_unsupported$"):
        TaskService(object(), audit=audit).anchor("x")


def test_anchor_delegates(tmp_path, monkeypatch):
    audit = AuditLog(tmp_path / "audit")
    service = TaskService(object(), audit=audit)
    calls = []
    result = {"label": "x"}

    def anchor(label, *, repo_root):
        calls.append((label, repo_root))
        return result

    monkeypatch.setattr(audit, "anchor", anchor)
    assert service.anchor("x", repo_root=tmp_path) is result
    assert service.anchor("x") is result
    assert calls == [("x", tmp_path), ("x", None)]


def test_anchor_with_temporary_root(tmp_path):
    service = build(tmp_path / "audit")
    row = service.anchor("x", repo_root=tmp_path)
    assert row["dir"] == "audit"
    assert row["label"] == "x"
    assert row["seq"] == 3
    assert verify_chain(tmp_path / "audit", repo_root=tmp_path, anchors=[row]).ok

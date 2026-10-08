import json
import random
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from test_evalrun_faults import (  # noqa: F401 - shared autouse fixtures
    exempt_config,
    fake_team_keys_and_hook_digests,
)

from govops.audit.log import MemoryAuditLog
from govops.evalrun.anchors import AnchorTracker, anchor_label


class Service:
    def __init__(self, directory, error=None):
        self.audit = SimpleNamespace(directory=directory)
        self.calls = []
        self.error = error

    def anchor(self, label, *, repo_root):
        self.calls.append((label, repo_root))
        if self.error:
            raise self.error
        return {"label": label, "dir": "audit", "hash": "a" * 64}


def test_labels():
    assert anchor_label("DEV", "ABC_DEF") == "dev-abc-def"
    assert anchor_label("dev", "A" * 100) == "dev-" + "a" * 36
    with pytest.raises(ValueError):
        anchor_label("", "")
    rng = random.Random(8)
    for _ in range(1000):
        name = UUID(int=rng.getrandbits(128), version=4).hex
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", anchor_label("dev", name))


def test_last_service_and_root(tmp_path):
    tracker = AnchorTracker()
    services = [Service(tmp_path / "a") for _ in range(3)]
    other = Service(tmp_path / "b")
    for service in [*services, other]:
        tracker.track(service)
    lines = tracker.finish("dev-run", repo_root=tmp_path)
    assert services[0].calls == services[1].calls == []
    assert services[2].calls == other.calls == [("dev-run", tmp_path)]
    assert len(lines) == 2
    assert (
        lines[0]
        == 'audit_anchor {"dir":"audit","hash":"' + "a" * 64 + '","label":"dev-run"}'
    )
    assert json.loads(lines[1].removeprefix("audit_anchor "))["label"] == "dev-run"


@pytest.mark.parametrize("error", [ValueError("secret/path"), OSError("secret/path")])
def test_failure_continues(tmp_path, error):
    tracker = AnchorTracker()
    tracker.track(Service(tmp_path / "a", error))
    good = Service(tmp_path / "b")
    tracker.track(good)
    lines = tracker.finish("dev-run", repo_root=tmp_path)
    assert lines[0] == f"audit_anchor_failed:{type(error).__name__}"
    assert "secret" not in "\n".join(lines)
    assert good.calls == [("dev-run", tmp_path)]


def test_unsupported_and_missing(tmp_path):
    tracker = AnchorTracker()
    unsupported = Service(tmp_path, RuntimeError("audit_anchor_unsupported"))
    for service in [
        unsupported,
        SimpleNamespace(audit=SimpleNamespace(directory=tmp_path / "missing")),
        SimpleNamespace(audit=None),
        SimpleNamespace(audit=MemoryAuditLog()),
        SimpleNamespace(audit=object()),
        object(),
    ]:
        tracker.track(service)
    assert tracker.finish("dev-run", repo_root=tmp_path) == []
    assert unsupported.calls == [("dev-run", tmp_path)]


def test_track_property_failure():
    class Broken:
        @property
        def audit(self):
            raise ValueError("secret")

    tracker = AnchorTracker()
    tracker.track(Broken())
    assert tracker.finish("dev-run", repo_root=Path(".")) == []


@pytest.mark.parametrize("smoke", [True, False])
@pytest.mark.parametrize("failed", [True, False])
def test_dev_runner_finishes_once_after_close(
    tmp_path, monkeypatch, capsys, smoke, failed
):
    import asyncio

    from test_evalrun_dev_m3 import exercise, specs

    from govops.api.wiring import build_task_service
    from govops.evalrun.dev_m3 import smoke_specs

    services, finishes = [], []

    class Tracker(AnchorTracker):
        def finish(self, label, *, repo_root):
            assert services and all(service._closed for service in services)
            assert repo_root == tmp_path
            assert label.startswith("dev-")
            finishes.append(label)
            return super().finish(label, repo_root=repo_root)

    monkeypatch.setattr("govops.evalrun.dev_m3.AnchorTracker", Tracker)

    def factory(**kwargs):
        service = build_task_service(**kwargs)

        def anchor(label, *, repo_root):
            assert all(s._closed for s in services)
            assert repo_root == tmp_path
            if failed:
                raise OSError("private/path")
            return {"label": label, "dir": "audit"}

        service.anchor = anchor
        services.append(service)
        return service

    result, _, _ = asyncio.run(
        exercise(
            tmp_path,
            monkeypatch,
            selected=smoke_specs() if smoke else specs(),
            smoke=smoke,
            service_factory=factory,
        )
    )
    assert len(finishes) == 1
    output = capsys.readouterr().out.splitlines()
    anchor_lines = [line for line in output if line.startswith("audit_anchor")]
    assert len(anchor_lines) == (1 if smoke else 20)
    if failed:
        assert set(anchor_lines) == {"audit_anchor_failed:OSError"}
    if smoke:
        assert result["exit_code"] == 0
        assert output[-1] == "INFO cost total_cents=0.000000"
    else:
        assert len(result["grades"]) == 20
        assert result["manifest"]["infrastructure_failures"] == 0
        assert max(output.index(line) for line in anchor_lines) < next(
            i for i, line in enumerate(output) if line.startswith("TCR ")
        )
    assert "private/path" not in "\n".join(output)


def test_faults_execute_live_finishes_after_close(tmp_path, monkeypatch, capsys):
    from test_evalrun_faults import (
        test_f11_live_assembly_queries_chaos_then_judges_answer,
    )

    from govops.api.wiring import build_task_service

    finishes, tracked, anchor_calls = [], [], []

    def factory(**kwargs):
        service = build_task_service(**kwargs)

        def anchor(label, *, repo_root):
            assert service._closed
            anchor_calls.append((label, repo_root))
            raise OSError("private/path")

        service.anchor = anchor
        return service

    monkeypatch.setattr("govops.evalrun.faults.build_task_service", factory)

    class Tracker(AnchorTracker):
        def track(self, service):
            tracked.append(service)
            super().track(service)

        def finish(self, label, *, repo_root):
            assert all(service._closed for service in tracked)
            finishes.append((label, repo_root))
            return super().finish(label, repo_root=repo_root)

    monkeypatch.setattr("govops.evalrun.faults.AnchorTracker", Tracker)
    test_f11_live_assembly_queries_chaos_then_judges_answer(
        monkeypatch, tmp_path, "refuse"
    )
    assert len(tracked) == 1
    assert finishes == anchor_calls == [("faults-attempt", tmp_path)]
    assert "audit_anchor_failed:OSError" in capsys.readouterr().out


@pytest.mark.parametrize("failed", [True, False])
def test_faults_shared_tracker_finishes_once(tmp_path, capsys, failed):
    import asyncio

    from test_evalrun_faults import Admin, run_faults

    services, finishes = [], []

    class Tracker(AnchorTracker):
        def finish(self, label, *, repo_root):
            assert all(service.closed for service in services)
            finishes.append((label, repo_root))
            return super().finish(label, repo_root=repo_root)

    tracker = Tracker()

    async def execute(row, directory):
        service = Service(tmp_path / "audit", OSError("secret") if failed else None)
        service.closed = False
        services.append(service)
        tracker.track(service)
        service.closed = True
        return {
            "status": row.definition.expected.status,
            "posts": row.definition.expected.posts[0],
            "lookups": row.definition.expected.lookups[0],
            "models": [],
            "infrastructure_failure": False,
            "injections": 1,
            "duplicates": 0,
            "missing": 0,
            "absolute_violations": False,
            "tcr": True,
        }

    result = asyncio.run(
        run_faults(
            root=tmp_path,
            admin=Admin(),
            execute=execute,
            metadata={},
            anchor_tracker=tracker,
        )
    )
    assert len(services) == 154
    assert len(finishes) == 1
    label, root = finishes[0]
    assert label.startswith("faults-") and root == tmp_path
    assert all(service.calls == [] for service in services[:-1])
    assert services[-1].calls == [(label, tmp_path)]
    assert result["finished_at"] is not None
    output = capsys.readouterr().out
    assert output.count("audit_anchor") == 1
    assert "secret" not in output

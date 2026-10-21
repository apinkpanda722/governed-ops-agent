"""Exercise host assembly and runner wiring rather than a route-time snapshot."""

import ast
import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, reply
from test_agent_semantic import Classifier

from govops.agent.graph import READ_ONLY
from govops.api import wiring
from govops.api.startup import StartupError
from govops.gateway.client import ClassifyResult
from govops.replay.llm import ReplayChatClient
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy

ROOT = Path(__file__).resolve().parents[1]


def assemble(tmp_path, chat, **kwargs):
    return wiring.build_task_service(
        chat=chat,
        tools=SimpleNamespace(jira=object(), slack=object(), policy=ToolPolicy()),
        saver=InMemorySaver(),
        outbox=InMemoryOutboxStore(),
        profile=READ_ONLY,
        audit_dir=tmp_path,
        **kwargs,
    )


@pytest.mark.parametrize("switch", [None, "off", "", "   ", "on", " on "])
def test_build_service_resolves_switch_to_same_chat(tmp_path, monkeypatch, switch):
    monkeypatch.delenv("GOVOPS_DATABASE_URL", raising=False)
    if switch is None:
        monkeypatch.delenv("GOVOPS_GUARDRAIL_V2", raising=False)
    else:
        monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", switch)
    chat = Classifier()
    service = assemble(tmp_path, chat)
    assert service.graph.agent_semantic is (
        chat if (switch or "").strip() == "on" else None
    )
    asyncio.run(service.aclose())


@pytest.mark.parametrize("chat", [None, FakeChat(), SimpleNamespace(classify=None)])
def test_on_without_classifier_fails_startup(tmp_path, monkeypatch, chat):
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", "on")
    with pytest.raises(StartupError, match="^guardrail_v2_unavailable$"):
        assemble(tmp_path, chat)
    assert not list(tmp_path.glob("chain-*.jsonl"))


def test_explicit_none_overrides_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", "on")
    service = assemble(tmp_path, FakeChat(), semantic=None)
    assert service.graph.agent_semantic is None
    asyncio.run(service.aclose())


@pytest.mark.parametrize("switch", [None, "", "   ", "off", "on", " on "])
def test_replay_switch_is_checked_at_construction(monkeypatch, switch):
    if switch is None:
        monkeypatch.delenv("GOVOPS_GUARDRAIL_V2", raising=False)
    else:
        monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", switch)
    if (switch or "").strip() == "on":
        with pytest.raises(ValueError, match="^guardrail_v2_replay$"):
            ReplayChatClient([])
    else:
        client = ReplayChatClient([])
        assert client._calls == [] and client._index == 0


def test_all_service_callers_omit_semantic_except_frozen_fault_runners():
    callers = []
    rollout_semantics = []
    for path in (ROOT / "src/govops").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not any(
                isinstance(part, ast.Name) and part.id == "build_task_service"
                for part in ast.walk(node.func)
            ):
                continue
            relative = str(path.relative_to(ROOT / "src/govops"))
            callers.append(relative)
            keywords = [k for k in node.keywords if k.arg == "semantic"]
            if relative in {"evalrun/faults.py", "evalrun/faults_smoke.py"}:
                assert len(keywords) == 1
                assert (
                    isinstance(keywords[0].value, ast.Constant)
                    and keywords[0].value.value is None
                )
            elif relative == "api/rollout_wiring.py":
                assert len(keywords) == 1
                value = keywords[0].value
                if isinstance(value, ast.Constant) and value.value is None:
                    rollout_semantics.append("None")
                else:
                    assert isinstance(value, ast.Call)
                    assert isinstance(value.func, ast.Name)
                    assert value.func.id == "RecordingClassifier"
                    rollout_semantics.append("RecordingClassifier")
            else:
                assert keywords == []
    assert sorted(rollout_semantics) == ["None", "RecordingClassifier"]
    assert sorted(callers) == [
        "api/rollout_wiring.py",
        "api/rollout_wiring.py",
        "api/wiring.py",
        "evalrun/dev_m3.py",
        "evalrun/dev_m3.py",
        "evalrun/faults.py",
        "evalrun/faults_smoke.py",
    ]


def test_real_server_lifespan_uses_assembled_semantic(tmp_path, monkeypatch):
    from govops.api.__main__ import build_server_config

    class Chat(FakeChat, Classifier):
        def __init__(self):
            FakeChat.__init__(self, reply("합성 답변"))

    chat = Chat()
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", "on")
    monkeypatch.setenv("DEMO_LOGIN", "true")
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path))
    monkeypatch.setenv("KODOCS_MCP_URL", "http://synthetic.invalid")
    monkeypatch.delenv("GOVOPS_DATABASE_URL", raising=False)
    monkeypatch.delenv("GOVOPS_MODEL_FAULT_MODE", raising=False)
    monkeypatch.delenv("GOVOPS_AGENT_PROFILE", raising=False)
    monkeypatch.setattr(
        wiring,
        "TeamKeyResolver",
        lambda _: SimpleNamespace(missing=list, key_for=lambda _: None),
    )
    monkeypatch.setattr(wiring, "AMcpClient", lambda _: object())
    monkeypatch.setattr(wiring, "LiteLLMChatClient", lambda **_: chat)
    monkeypatch.setattr(wiring, "langfuse_sink_from_env", lambda: None)
    monkeypatch.setattr(wiring, "configure", lambda _: None)

    async def no_op(*args):
        pass

    @asynccontextmanager
    async def saver(*args):
        yield InMemorySaver()

    monkeypatch.setattr(wiring, "verify_contract", no_op)
    monkeypatch.setattr(wiring, "startup_prune", no_op)
    monkeypatch.setattr(wiring, "open_checkpointer", saver)
    config = build_server_config(8000)
    assert config.app.state.task_service.graph is None
    with TestClient(config.app, client=("127.0.0.1", 50000)):
        assert config.app.state.task_service.graph.agent_semantic is chat


@pytest.mark.parametrize("switch", ["off", "on"])
def test_dev_runner_forwards_same_classifier_through_recording_and_reports_manifest(
    tmp_path, monkeypatch, switch
):
    from test_evalrun_dev_m3 import Chat, exercise, specs

    from govops.golden import GOLDEN_TASKS

    target = tmp_path / "scripts/exempt_paths.json"
    target.parent.mkdir()
    target.write_bytes((ROOT / "scripts/exempt_paths.json").read_bytes())
    monkeypatch.setenv("GOVOPS_GUARDRAIL_V2", switch)
    counts = dict.fromkeys(
        ("ok", "timeout", "rate_limited", "error", "parse", "v1_blocked"), 0
    )
    classifier_calls, services = [], []

    async def classify(self, *, request, task_id, user_id):
        classifier_calls.append((self, request, task_id, user_id))
        counts["ok"] += 1
        return ClassifyResult("safe", "ok", 3, 2, 0)

    monkeypatch.setattr(Chat, "classify", classify, raising=False)
    monkeypatch.setattr(
        Chat, "config_sha256", staticmethod(lambda: "f" * 64), raising=False
    )
    monkeypatch.setattr(
        Chat, "status_counts", staticmethod(lambda: dict(counts)), raising=False
    )

    def factory(**kwargs):
        assert "semantic" not in kwargs
        service = wiring.build_task_service(**kwargs)
        assert service.graph.agent_semantic is (
            kwargs["chat"] if switch == "on" else None
        )
        services.append(service)
        return service

    selected = [
        next(s for s in specs() if s.task_id in GOLDEN_TASKS),
        next(s for s in specs() if s.task_id not in GOLDEN_TASKS),
    ]
    result, _, chat = asyncio.run(
        exercise(tmp_path, monkeypatch, selected=selected, service_factory=factory)
    )
    assert len(services) == 2
    if switch == "on":
        assert len(classifier_calls) >= 2
        assert all(row[0] is chat for row in classifier_calls)
    else:
        assert classifier_calls == []
    expected = {
        "switch": switch,
        "cfg_sha256": "f" * 64 if switch == "on" else None,
        "status_counts": counts if switch == "on" else {},
        "runner_concurrency": 1,
    }
    assert result["manifest"]["guardrail_v2"] == expected
    stored = json.loads((Path(result["directory"]) / "manifest.json").read_text())
    assert stored["guardrail_v2"] == expected
    keys = list(stored)
    assert keys.index("guardrail_v2") < keys.index("finished_at")

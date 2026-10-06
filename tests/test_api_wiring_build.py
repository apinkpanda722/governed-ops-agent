from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from govops.agent.graph import READ_ONLY, WITH_WRITES
from govops.api.wiring import build_task_service
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy


@pytest.mark.parametrize("profile", [READ_ONLY, WITH_WRITES])
def test_injected_assembly_ignores_environment(tmp_path, monkeypatch, profile):
    monkeypatch.setenv("GOVOPS_AGENT_PROFILE", "invalid")
    monkeypatch.setenv("LITELLM_AGENT_KEY", "synthetic")
    monkeypatch.setenv("AUDIT_DIR", str(tmp_path / "wrong"))
    tools = SimpleNamespace(jira=object(), slack=object(), policy=ToolPolicy())
    service = build_task_service(
        chat=object(),
        tools=tools,
        saver=InMemorySaver(),
        outbox=InMemoryOutboxStore(),
        profile=profile,
        audit_dir=tmp_path / "audit",
    )
    assert service.graph.agent_profile is profile
    assert service.effects.jira is tools.jira
    assert service.policy is tools.policy
    assert service.audit.directory == tmp_path / "audit"
    assert len(service.users) == 38


def test_org_golden_synthetic_replay(tmp_path):
    import asyncio
    import json

    from test_agent_graph import FakeChat, reply
    from test_regolden import grading_data

    from govops.agent.graph import PROFILES, build_graph
    from govops.golden import (
        load_golden_specs,
        manifest_tasks,
        record_status,
        replay_task,
    )
    from govops.replay.llm import RecordingChatClient

    profile = PROFILES["writes_org"]
    llm, a, golden = [tmp_path / name for name in ("llm", "a", "golden")]
    a.mkdir()
    golden.mkdir()
    key = "T1-01"

    async def run():
        chat = RecordingChatClient(
            FakeChat(reply("합성 답변")),
            task_id=key,
            directory=llm,
            system_prompt=profile.system_prompt,
            tools=profile.tools,
            model="gpt-6-luna",
            params={},
        )
        from govops.golden import _write_graph

        runtime = SimpleNamespace(jira=object(), slack=object(), policy=ToolPolicy())
        graph, service = _write_graph(
            chat,
            SimpleNamespace(),
            runtime,
            InMemorySaver(),
            build_graph,
            profile=profile,
        )
        from govops.agent.graph import run_task

        spec = load_golden_specs()[key]
        await run_task(
            graph, task_id="synthetic", user_id=spec.user, request=spec.request
        )
        chat.finish()
        await service.aclose()
        (a / f"{key}.jsonl").write_text("")
        obs = await replay_task(
            key,
            llm_dir=llm,
            a_dir=a,
            mock_executor=runtime,
            profile="writes_org",
            mock_state=lambda: {"jira": {}, "slack": {}},
        )
        assert obs.status == "completed"
        row = record_status(
            spec,
            obs,
            SimpleNamespace(golden_eligible=True),
            **grading_data(),
            grading_rules="registered",
            profile="writes_org",
        )
        assert row["profile"] == "writes_org"
        (golden / "status.json").write_text(json.dumps({key: row}))
        assert (
            manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a)[key]["profile"]
            == "writes_org"
        )
        row["profile"] = "unknown"
        (golden / "status.json").write_text(json.dumps({key: row}))
        with pytest.raises(ValueError, match="invalid_profile"):
            manifest_tasks(golden_dir=golden, llm_dir=llm, a_dir=a)
        with pytest.raises(ValueError, match="invalid_profile"):
            await replay_task(
                key, llm_dir=llm, a_dir=a, mock_executor=runtime, profile="unknown"
            )
        with pytest.raises(ValueError, match="invalid_profile"):
            record_status(
                spec,
                obs,
                None,
                meta=None,
                tickets=None,
                users=None,
                grading_rules=None,
                profile="unknown",
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    "value", [None, "", "writes_org", "read_only", "writes", "invalid"]
)
def test_environment_profile_selection(monkeypatch, value):
    import asyncio
    from contextlib import asynccontextmanager

    from govops.api import wiring
    from govops.api.startup import StartupError

    if value is None:
        monkeypatch.delenv("GOVOPS_AGENT_PROFILE", raising=False)
    else:
        monkeypatch.setenv("GOVOPS_AGENT_PROFILE", value)
    monkeypatch.delenv("GOVOPS_MODEL_FAULT_MODE", raising=False)
    monkeypatch.delenv("GOVOPS_DATABASE_URL", raising=False)
    monkeypatch.setenv("KODOCS_MCP_URL", "http://synthetic")
    # Use a distinct fake key: startup registers secrets process-wide, and
    # "synthetic" is ordinary data in other tests' saved manifests.
    monkeypatch.setenv("LITELLM_AGENT_KEY", "synthetic-profile-selection-key")
    monkeypatch.setattr(wiring, "AMcpClient", lambda url: object())
    monkeypatch.setattr(wiring, "langfuse_sink_from_env", lambda: None)
    monkeypatch.setattr(wiring, "configure", lambda sinks: None)
    monkeypatch.setattr(wiring, "LiteLLMChatClient", lambda: object())

    async def verify(client):
        pass

    @asynccontextmanager
    async def saver(url):
        yield InMemorySaver()

    selected = []

    class Service:
        async def startup_cleanup(self):
            pass

        async def aclose(self):
            pass

    def build(**kwargs):
        selected.append(kwargs["profile"])
        return Service()

    monkeypatch.setattr(wiring, "verify_contract", verify)
    monkeypatch.setattr(wiring, "open_checkpointer", saver)
    monkeypatch.setattr(wiring, "build_task_service", build)

    async def run():
        async with wiring.open_task_service():
            assert selected[0] is wiring.PROFILES["writes_org"]

    if value in (None, "", "writes_org"):
        asyncio.run(run())
        assert len(selected) == 1
    else:
        with pytest.raises(StartupError, match="GOVOPS_AGENT_PROFILE"):
            asyncio.run(run())
        assert selected == []

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

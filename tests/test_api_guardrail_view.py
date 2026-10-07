import asyncio
import json

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, async_test

from govops.agent.graph import build_graph
from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.gateway.client import GuardrailBlocked, GuardrailUnavailable


@pytest.mark.parametrize(
    "code,message",
    [
        (
            "injection_direct",
            "요청에 시스템 지시를 바꾸려는 표현이 있어 처리하지 않았습니다. 업무 내용만 다시 입력해 주세요.",
        ),
        (
            "pii_input",
            "요청에 개인정보로 보이는 값이 있어 처리하지 않았습니다. 개인정보를 제거하고 다시 요청해 주세요.",
        ),
        ("guardrail_unavailable", None),
        ("team_unknown", None),
        ("guardrail_header_missing", None),
        ("guardrail_stale", None),
    ],
)
@async_test
async def test_public_guardrail_view_and_ownership(code, message, monkeypatch):
    attributes = []
    monkeypatch.setattr(
        "govops.api.tasks.set_attributes", lambda **kw: attributes.append(kw)
    )
    error = GuardrailBlocked(code, None) if message else GuardrailUnavailable(code)
    service = TaskService(build_graph(FakeChat(error), FakeTools(), InMemorySaver()))
    sessions = SessionStore()
    app = create_app(
        users=frozenset(), sessions=sessions, demo_login=False, service=service
    )
    ident = await service.start("demo-platform-g3", "PRIVATE-REQUEST-SENTINEL")
    await asyncio.gather(*service._tasks)
    token, _ = sessions.issue("demo-platform-g3")
    other, _ = sessions.issue("demo-finance-g3")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1"
    ) as client:
        result = await client.get(
            "/tasks/" + ident, headers={"Authorization": "Bearer " + token}
        )
        assert result.status_code == 200
        view = result.json()
        assert view["status"] == ("blocked_input" if message else "failed")
        assert view["error"] == code and view["answer"] is None
        assert view["cited_doc_ids"] == view["observed_doc_ids"] == []
        assert view["guardrail"] == {
            "quarantined": 0,
            "quarantined_at_least": False,
            "masked": 0,
        }
        if message:
            assert view["block"] == {"code": code, "message": message}
        else:
            assert "block" not in view
        assert "PRIVATE-REQUEST-SENTINEL" not in json.dumps(view)
        assert "PRIVATE-REQUEST-SENTINEL" not in json.dumps(attributes)
        assert (
            await client.get(
                "/tasks/" + ident, headers={"Authorization": "Bearer " + other}
            )
        ).status_code == 404
    await service.startup_cleanup()
    assert await service.get("demo-platform-g3", ident) == view
    await service.aclose()


def test_approver_view_and_legacy_defaults():
    service = TaskService(None)
    state = {
        "status": "blocked_input",
        "user_id": "demo-platform-g3",
        "guardrail_block": {"code": "pii_input"},
        "answer": "PRIVATE",
        "observed_ids": ["syn-one"],
        "cited_doc_ids": ["syn-one"],
    }
    view = service._view(state, "a" * 32)
    assert (
        view["answer"] is None
        and view["cited_doc_ids"] == view["observed_doc_ids"] == []
    )
    assert "guardrail" not in service._view(state, "a" * 32, approval=True)
    assert "block" not in service._view(state, "a" * 32, approval=True)
    state.update(
        status="completed",
        quarantined_ids=["a" * 64],
        quarantine_unmatched=2,
        guardrail_truncated=True,
        masked_total=5,
    )
    assert service._view(state, "a" * 32)["guardrail"] == {
        "quarantined": 3,
        "quarantined_at_least": True,
        "masked": 5,
    }

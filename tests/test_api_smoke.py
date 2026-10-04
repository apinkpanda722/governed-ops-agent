"""Live smoke logic is tested without live services or credentials."""

import asyncio
import inspect
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from govops.api import smoke
from govops.costs import CostLedger, init_ledgers


class FakeService:
    def __init__(self, ledger, change=None):
        self.ledger = ledger
        self.change = change
        self.requests = []

    async def start(self, user_id, request):
        self.requests.append(request)
        if self.change in {"reserved", "unsettled"}:
            reservation = self.ledger.reserve(
                purpose="agent_primary",
                model="fake",
                task_id=user_id,
                estimate_usd=0.02,
            )
            if self.change == "reserved":
                self.ledger.settle(
                    reservation,
                    purpose="agent_primary",
                    model="fake",
                    task_id=user_id,
                    input_tokens=1,
                    output_tokens=1,
                    cost_usd=0.01,
                )
        elif self.change != "ledger":
            self.ledger.record(
                purpose="agent_primary",
                model="fake",
                task_id=user_id,
                input_tokens=1,
                output_tokens=1,
                cost_usd=9.13 if self.change == "cap" else 0.01,
            )
        return user_id

    async def get(self, user_id, task_id):
        ids = [smoke.EXPECTED_DOC] if "platform" in user_id else []
        if self.change == "finance" and "finance" in user_id:
            ids = [smoke.EXPECTED_DOC]
        if self.change == "platform" and "platform" in user_id:
            ids = []
        return {
            "status": "failed" if self.change == "status" else "completed",
            "steps": 2,
            "observed_doc_ids": ids,
            "cited_doc_ids": ids,
            "answer": "합성 요약",
        }


@pytest.mark.parametrize(
    "change,item",
    [
        (None, None),
        ("reserved", None),  # 예약 + 정산 행은 호출 하나로 센다
        ("unsettled", "agent_primary"),  # 정산되지 않은 예약만으로는 통과하지 않는다
        ("finance", "finance"),
        ("platform", "platform"),
        ("status", "status"),
        ("ledger", "agent_primary"),
        ("cap", "OpenAI"),
    ],
)
def test_agent(tmp_path, change, item):
    async def run():
        init_ledgers(tmp_path)
        ledger = CostLedger(tmp_path)
        service = FakeService(ledger, change)
        result = await smoke.smoke_agent(service, ledger, poll_interval=0)
        assert result.ok == (change in {None, "reserved"})
        assert len(set(service.requests)) == 1
        if item:
            assert item in "\n".join(result.lines)

    asyncio.run(run())


def test_timeout(tmp_path):
    async def run():
        init_ledgers(tmp_path)
        service = FakeService(CostLedger(tmp_path))

        async def running(*args):
            return {"status": "running"}

        service.get = running
        result = await smoke.smoke_agent(service, service.ledger, timeout_s=0)
        assert not result.ok and "status" in "\n".join(result.lines)

    asyncio.run(run())


@pytest.mark.parametrize("mismatch", [False, True])
def test_checkpointer(monkeypatch, mismatch):
    async def run():
        saver = InMemorySaver()
        deleted = []
        original = saver.adelete_thread

        async def delete(thread):
            deleted.append(thread)
            await original(thread)

        saver.adelete_thread = delete

        @asynccontextmanager
        async def fake(url):
            yield saver

        monkeypatch.setattr(smoke, "open_checkpointer", fake)
        if mismatch:
            original_get = saver.aget_tuple

            async def get(config):
                value = await original_get(config)
                if value:
                    value.checkpoint["channel_values"]["value"] = "different"
                return value

            saver.aget_tuple = get
        result = await smoke.smoke_checkpointer("postgres://secret-sentinel")
        assert result.ok == (not mismatch)
        assert len(deleted) == 1
        assert "secret-sentinel" not in str(result.lines)
        assert not (await smoke.smoke_checkpointer("")).ok

    asyncio.run(run())


def test_preflight(monkeypatch, tmp_path, capsys, caplog):
    monkeypatch.setattr(smoke, "CostLedger", lambda: CostLedger(tmp_path))
    for name in ("LITELLM_AGENT_KEY", "KODOCS_MCP_URL", "GOVOPS_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert smoke.main(["agent"]) == 1
    output = capsys.readouterr().out
    assert "LITELLM_AGENT_KEY" in output and "KODOCS_MCP_URL" in output
    monkeypatch.setenv("LITELLM_AGENT_KEY", "key-sentinel")
    monkeypatch.setenv("KODOCS_MCP_URL", "token-sentinel")
    assert smoke.main(["agent"]) == 1
    assert "sentinel" not in capsys.readouterr().out + caplog.text


def test_fixed_expectations():
    source = inspect.getsource(smoke)
    assert 'EXPECTED_DOC = "syn-incident-022"' in source
    assert "OPENAI_CAP = 9.13" in source
    assert "--expected" not in source
    assert 'getenv("EXPECTED' not in source and 'environ.get("EXPECTED' not in source


def test_safe_failures(monkeypatch, tmp_path, capsys, caplog):
    init_ledgers(tmp_path)
    ledger = CostLedger(tmp_path)

    class BrokenService:
        async def start(self, *args):
            raise RuntimeError("key-token-sentinel")

    result = asyncio.run(smoke.smoke_agent(BrokenService(), ledger))
    assert not result.ok
    assert "sentinel" not in str(result.lines)

    @asynccontextmanager
    async def broken(url):
        raise RuntimeError("connection-sentinel")
        yield

    monkeypatch.setattr(smoke, "open_checkpointer", broken)
    result = asyncio.run(smoke.smoke_checkpointer("postgres://connection-sentinel"))
    assert not result.ok and "sentinel" not in str(result.lines)
    monkeypatch.setenv("GOVOPS_DATABASE_URL", "postgres://connection-sentinel")
    assert smoke.main(["checkpointer"]) == 1
    assert "sentinel" not in capsys.readouterr().out + caplog.text


def test_agent_main_wiring_and_memory_warning(monkeypatch, tmp_path, capsys):
    init_ledgers(tmp_path)
    ledger = CostLedger(tmp_path)
    monkeypatch.setattr(smoke, "CostLedger", lambda: ledger)
    monkeypatch.setenv("LITELLM_AGENT_KEY", "key-sentinel")
    monkeypatch.setenv("KODOCS_MCP_URL", "token-sentinel")
    monkeypatch.delenv("GOVOPS_DATABASE_URL", raising=False)

    @asynccontextmanager
    async def service():
        yield FakeService(ledger)

    monkeypatch.setattr(smoke, "open_task_service", service)
    assert smoke.main(["agent"]) == 0
    output = capsys.readouterr().out
    assert "경고: GOVOPS_DATABASE_URL" in output
    assert "sentinel" not in output


@pytest.mark.parametrize("field", ["observed_doc_ids", "cited_doc_ids"])
def test_each_structured_id_field_is_checked(tmp_path, field):
    init_ledgers(tmp_path)
    service = FakeService(CostLedger(tmp_path))
    original = service.get

    async def altered(user, task):
        state = await original(user, task)
        if "platform" in user:
            state[field] = []
            state["answer"] = smoke.EXPECTED_DOC
        return state

    service.get = altered
    result = asyncio.run(smoke.smoke_agent(service, service.ledger))
    assert not result.ok
    assert any(field in line and "불일치" in line for line in result.lines)

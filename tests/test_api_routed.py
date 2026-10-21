"""Rollout routing with shared in-memory checkpoints and synthetic requests only."""

import asyncio
import inspect
import json
from datetime import UTC, datetime, timedelta
from functools import wraps
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from test_agent_graph import FakeChat, FakeTools, call, reply
from test_agent_semantic import Classifier

from govops.agent.graph import WITH_WRITES, EffectDeps, build_graph, get_task
from govops.agent.scope import ScopeIndex
from govops.agent.users import UserInfo
from govops.api import routed
from govops.api.conversations import ConversationError, InMemoryTurnIndex, TurnIndex
from govops.api.routed import (
    RecordingClassifier,
    RoutedTaskService,
    VariantFilteredTurnIndex,
)
from govops.api.tasks import TaskService
from govops.api.variant import InMemoryVariantStore
from govops.audit.log import MemoryAuditLog
from govops.guardrails.policy import TeamPolicy
from govops.rollout import policy, store
from govops.tools.outbox import InMemoryOutboxStore
from govops.tools.resilience import ToolPolicy

NOW = datetime(2026, 10, 21, tzinfo=UTC)
USER = "demo-requester-g3"
REQUEST = "합성 운영 기록을 읽고 정리해 주세요."
ANSWER = "라우팅 테스트 전용 합성 답변"


def scenario(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        return asyncio.run(asyncio.wait_for(fn(*args, **kwargs), 30))

    return run


def harness(tmp_path, *, status="ok", writes=False):
    saver, turns, variants = (
        InMemorySaver(),
        InMemoryTurnIndex(),
        InMemoryVariantStore(),
    )
    classifier = Classifier(status=status)
    users = {USER: UserInfo(USER, "platform", 6)}
    tools, audit = FakeTools(), MemoryAuditLog()
    tools.policy = ToolPolicy()
    effects = EffectDeps(
        SimpleNamespace(add_comment=None, create_followup=None),
        SimpleNamespace(send_message=AsyncMock()),
        InMemoryOutboxStore(),
    )
    options = (
        {
            "profile": WITH_WRITES,
            "effects": effects,
            "audit": audit,
            "scope": ScopeIndex({}, {}, tuple(users.values())),
            "policy": TeamPolicy({"platform": "block"}),
            "users": users,
        }
        if writes
        else {}
    )
    services = []
    for variant in ("v1", "v2"):
        response = (
            reply(
                calls=[
                    call(
                        "slack_send_message",
                        json.dumps({"channel": "team-platform", "text": "합성 안내"}),
                    )
                ]
            )
            if writes
            else reply(ANSWER)
        )
        graph = build_graph(
            FakeChat(*(response for _ in range(80))),
            tools,
            saver,
            semantic=RecordingClassifier(
                classifier, directory=tmp_path, now=lambda: NOW
            )
            if variant == "v2"
            else None,
            clock=lambda: NOW,
            **options,
        )
        services.append(
            TaskService(
                graph,
                turns=VariantFilteredTurnIndex(turns, variants, variant),
                users=users,
                audit=audit,
                effects=effects if writes else None,
                policy=tools.policy,
            )
        )
    v1, v2 = services
    router = RoutedTaskService(
        v1, v2, turns=turns, variants=variants, directory=tmp_path, clock=lambda: NOW
    )
    return SimpleNamespace(
        router=router,
        v1=v1,
        v2=v2,
        turns=turns,
        variants=variants,
        classifier=classifier,
        saver=saver,
    )


def set_state(directory, pct=10000, kill=False):
    store.write_state(
        directory, policy.RolloutState(pct, kill, None, "manual_kill" if kill else None)
    )


async def settle(h):
    await asyncio.wait_for(asyncio.gather(*h.v1._tasks, *h.v2._tasks), 10)


async def create(h):
    result = await h.router.start_conversation(USER, REQUEST)
    await settle(h)
    assert (await h.router.get(USER, result["turn_id"]))["status"] in {
        "completed",
        "awaiting_approval",
    }
    return result["conversation_id"]


@scenario
@pytest.mark.parametrize("pct, expected", [(0, "v1"), (10000, "v2")])
async def test_assignment_and_private_records(tmp_path, pct, expected):
    h = harness(tmp_path)
    if pct:
        set_state(tmp_path, pct)
    try:
        ids = {await create(h) for _ in range(3)}
        assert await h.variants.get_many(ids) == dict.fromkeys(ids, expected)
        assert h.router.audit is h.v1.audit
        assert h.router.turns is h.turns
        rows = store.read_exposures(tmp_path)
        assert len(rows) == 3
        assert {row["conversation_id"] for row in rows} == ids
        assert all(
            set(row)
            == {
                "event",
                "ts",
                "conversation_id",
                "user_sha256",
                "bucket",
                "variant",
                "pct_bp",
                "kill",
            }
            for row in rows
        )
        assert all(
            row["user_sha256"] == policy.user_digest(USER)
            and row["bucket"] == policy.bucket(USER)
            for row in rows
        )
        calls = store.read_classify_tail(tmp_path)
        if pct:
            assert h.classifier.calls and calls
            assert {row["turn_id"] for row in calls} == ids
            assert all(
                set(row) == {"ts", "turn_id", "status", "started_ms", "ended_ms"}
                for row in calls
            )
        else:
            assert h.classifier.calls == calls == []
        content = "".join(path.read_text() for path in tmp_path.glob("*.jsonl"))
        assert all(value not in content for value in (USER, REQUEST, ANSWER))
    finally:
        await h.router.aclose()


@scenario
@pytest.mark.parametrize("kill", [False, True])
async def test_sticky_turns_and_task_id_routing(tmp_path, kill):
    h = harness(tmp_path)
    set_state(tmp_path)
    try:
        ident = await create(h)
        set_state(tmp_path, 0, kill)
        second = await h.router.add_turn(USER, ident, REQUEST)
        await settle(h)
        assert second["turn_id"] != ident
        assert {task_id for _, task_id, _ in h.classifier.calls} == {
            ident,
            second["turn_id"],
        }
        h.v1.get = AsyncMock(side_effect=AssertionError("wrong variant"))
        view = await h.router.get(USER, second["turn_id"])
        assert view["task_id"] == second["turn_id"] and view["status"] == "completed"
        h.v1.get.assert_not_called()
        h.v1.get = TaskService.get.__get__(h.v1)
        fresh = await create(h)
        assert await h.variants.get_many([ident, fresh]) == {ident: "v2", fresh: "v1"}
    finally:
        await h.router.aclose()


@scenario
async def test_damaged_state_defaults_to_v1(tmp_path):
    h = harness(tmp_path)
    (tmp_path / "state.json").write_text("{broken")
    try:
        ident = await create(h)
        assert await h.variants.get_many([ident]) == {ident: "v1"}
        assert h.classifier.calls == []
    finally:
        await h.router.aclose()


@scenario
@pytest.mark.parametrize("count", [19, 20])
async def test_abnormal_classifier_rolls_back_only_at_minimum_sample(
    tmp_path, count, monkeypatch
):
    h = harness(tmp_path, status="timeout")
    set_state(tmp_path)
    evaluations = []
    evaluate = policy.evaluate

    def capture(v2, v1):
        result = evaluate(v2, v1)
        evaluations.append(result)
        return result

    monkeypatch.setattr(policy, "evaluate", capture)
    try:
        ids = {await create(h) for _ in range(count)}
        assert len(ids) == count
        assert await h.variants.get_many(ids) == dict.fromkeys(ids, "v2")
        next_id = await create(h)
        state = store.read_state(tmp_path)
        rollbacks = [
            r for r in store.read_exposures(tmp_path) if r["event"] == "rollback"
        ]
        if count == 20:
            assert state.kill and state.pct_bp == 0
            assert state.reason == "rollback_classify_abnormal"
            assert state.rolled_back_at == NOW.isoformat()
            assert rollbacks == [
                {
                    "event": "rollback",
                    "ts": NOW.isoformat(),
                    "reason": state.reason,
                    "pct_bp": 10000,
                }
            ]
            assert await h.variants.get_many([next_id]) == {next_id: "v1"}
        else:
            assert not state.kill and state.pct_bp == 10000
            assert rollbacks == []
            assert evaluations[-1].notes == ("insufficient_v2_sample",)
            assert await h.variants.get_many([next_id]) == {next_id: "v2"}
    finally:
        await h.router.aclose()


@scenario
async def test_lists_keep_shared_top_limit_and_stable_ties(tmp_path, monkeypatch):
    h = harness(tmp_path)
    try:
        for pct, days in ((0, 0), (10000, 3), (0, 1), (10000, 3)):
            stamp = NOW + timedelta(days=days)
            monkeypatch.setattr(h.v1, "clock", lambda stamp=stamp: stamp)
            monkeypatch.setattr(h.v2, "clock", lambda stamp=stamp: stamp)
            set_state(tmp_path, pct)
            await create(h)
        expected = await h.turns.list_for_owner(USER, 3)
        result = (await h.router.conversations(USER, 3))["conversations"]
        assert len(result) == 3
        assert [row["conversation_id"] for row in result] == [
            row["conversation_id"] for row in expected
        ]
        assert len({row["conversation_id"] for row in result}) == len(result)
        assert (await h.router.conversations("demo-other-g3", 3))["conversations"] == []
    finally:
        await h.router.aclose()


@scenario
async def test_pending_approval_stays_in_own_service_locks(tmp_path):
    h = harness(tmp_path, writes=True)
    try:
        set_state(tmp_path)
        v2_id = await create(h)
        set_state(tmp_path, 0)
        v1_id = await create(h)
        approvals = (await h.router.approvals(USER))["approvals"]
        assert {row["task_id"] for row in approvals} == {v1_id, v2_id}
        assert len(approvals) == 2
        assert approvals == sorted(
            approvals, key=lambda row: (row["expires_at"], row["task_id"])
        )
        assert v2_id not in h.v1._conv_locks
        assert v1_id not in h.v2._conv_locks
        for ident in (v1_id, v2_id):
            with pytest.raises(ConversationError) as caught:
                await h.router.add_turn(USER, ident, REQUEST)
            assert (caught.value.status_code, caught.value.code) == (
                409,
                "approval_pending",
            )
        view = await h.router.get(USER, v2_id)
        code, rejected = await h.router.approve(
            USER, v2_id, decision="reject", draft_sha256=view["draft_sha256"]
        )
        assert code == 202 and rejected["status"] == "rejected"
        second = await h.router.add_turn(USER, v2_id, REQUEST)
        await settle(h)
        view = await h.router.get(USER, second["turn_id"])
        h.v2.approve = AsyncMock(wraps=h.v2.approve)
        code, rejected = await h.router.approve(
            USER,
            second["turn_id"],
            decision="reject",
            draft_sha256=view["draft_sha256"],
        )
        assert code == 202 and rejected["status"] == "rejected"
        h.v2.approve.assert_awaited_once_with(
            USER,
            second["turn_id"],
            decision="reject",
            draft_sha256=view["draft_sha256"],
            edited=None,
        )
        assert v2_id not in h.v1._conv_locks
    finally:
        await h.router.aclose()


@scenario
async def test_missing_variant_records_once_and_invalid_ids_skip_lookup(
    tmp_path, monkeypatch
):
    h = harness(tmp_path)
    try:
        ident = await h.v1.start(USER, REQUEST)
        await settle(h)
        for _ in range(2):
            assert (await h.router.conversation_view(USER, ident))[
                "conversation_id"
            ] == ident
        assert store.read_exposures(tmp_path) == [
            {
                "event": "variant_missing",
                "ts": NOW.isoformat(),
                "conversation_id": ident,
            }
        ]
        assert await h.router.get(USER, uuid4().hex) is None
        assert len(store.read_exposures(tmp_path)) == 1
        monkeypatch.setattr(
            h.variants,
            "get_many",
            AsyncMock(side_effect=AssertionError("invalid lookup")),
        )
        monkeypatch.setattr(
            h.turns,
            "conversation_of",
            AsyncMock(side_effect=AssertionError("invalid lookup")),
        )
        assert await h.router.get(USER, "invalid") is None
        assert await h.router.progress(USER, "invalid") is None
        with pytest.raises(ConversationError) as caught:
            await h.router.conversation_view(USER, "invalid")
        assert caught.value.status_code == 404
    finally:
        await h.router.aclose()


@scenario
async def test_startup_cleanup_once_reads_v2_checkpoint_shape(tmp_path, monkeypatch):
    h = harness(tmp_path)
    ident = uuid4().hex
    try:
        await h.v2.graph.aupdate_state(
            {"configurable": {"thread_id": ident}},
            {
                "task_id": ident,
                "user_id": USER,
                "status": "running",
                "created_at": NOW.isoformat(),
            },
            as_node="__start__",
        )
        await h.variants.put(
            conversation_id=ident,
            variant="v2",
            bucket=0,
            pct_bp=10000,
            assigned_at=NOW.isoformat(),
        )
        cleanup = AsyncMock(wraps=h.v1.startup_cleanup)
        monkeypatch.setattr(h.v1, "startup_cleanup", cleanup)
        monkeypatch.setattr(
            h.v2,
            "startup_cleanup",
            AsyncMock(side_effect=AssertionError("duplicate cleanup")),
        )
        await h.router.startup_cleanup()
        cleanup.assert_awaited_once_with()
        h.v2.startup_cleanup.assert_not_called()
        assert (await get_task(h.v2.graph, ident))["status"] == "interrupted"
        assert (await h.router.conversation_view(USER, ident))["turn"][
            "status"
        ] == "interrupted"
    finally:
        await h.router.aclose()


@scenario
async def test_concurrent_assignments_have_distinct_rows(tmp_path):
    h = harness(tmp_path)
    set_state(tmp_path)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(h.router.start_conversation(USER, REQUEST) for _ in range(2))
            ),
            10,
        )
        await settle(h)
        ids = {row["conversation_id"] for row in results}
        assert len(ids) == 2
        assert await h.variants.get_many(ids) == dict.fromkeys(ids, "v2")
        rows = store.read_exposures(tmp_path)
        assert len(rows) == 2 and {row["event"] for row in rows} == {"assigned"}
        assert {row["conversation_id"] for row in rows} == ids
    finally:
        await h.router.aclose()


@scenario
async def test_classifier_preserves_identity_exception_and_timestamps(tmp_path):
    result = SimpleNamespace(status="ok")
    inner = SimpleNamespace(classify=AsyncMock(return_value=result))
    ticks = iter([1.0, 1.025, 2.0, 2.1, 3.0, 3.1])
    wrapper = RecordingClassifier(
        inner, directory=tmp_path, monotonic=lambda: next(ticks), now=lambda: NOW
    )
    args = {"request": object(), "task_id": "a" * 32, "user_id": USER}
    assert await wrapper.classify(**args) is result
    inner.classify.assert_awaited_once_with(**args)
    error = RuntimeError("synthetic classifier exception")
    inner.classify.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await wrapper.classify(**args)
    assert caught.value is error
    rows = store.read_classify_tail(tmp_path)
    assert [(r["status"], r["started_ms"], r["ended_ms"]) for r in rows] == [
        ("ok", 1000, 1025),
        ("raised", 2000, 2100),
    ]
    inner.classify.side_effect = None
    assert await wrapper.classify(**{**args, "task_id": "invalid"}) is result
    assert store.read_classify_tail(tmp_path) == rows


@scenario
@pytest.mark.parametrize("failure", [OSError, ValueError])
async def test_classifier_record_failure_warns_once_without_changing_result(
    tmp_path, monkeypatch, capsys, failure
):
    monkeypatch.setattr(routed, "_WARNED", set())

    def fail(*args):
        raise failure("must not print this")

    monkeypatch.setattr(store, "append_classify", fail)
    result = SimpleNamespace(status="ok")
    args = {"request": object(), "task_id": "b" * 32, "user_id": USER}
    for _ in range(2):
        wrapper = RecordingClassifier(
            SimpleNamespace(classify=AsyncMock(return_value=result)), directory=tmp_path
        )
        assert await wrapper.classify(**args) is result
    assert capsys.readouterr().err == "WARN rollout_classify_record_failed\n"


@scenario
async def test_filtered_index_explicit_methods_and_single_batch(tmp_path):
    h = harness(tmp_path)
    method_names = {
        name
        for name, value in TurnIndex.__dict__.items()
        if inspect.iscoroutinefunction(value)
    }
    assert method_names == {
        name
        for name, value in VariantFilteredTurnIndex.__dict__.items()
        if inspect.iscoroutinefunction(value)
    }
    v1_id, v2_id = uuid4().hex, uuid4().hex
    for ident in (v1_id, v2_id):
        await h.turns.add(
            turn_id=ident,
            conversation_id=ident,
            owner_sub=USER,
            turn_index=0,
            created_at=NOW.isoformat(),
        )
        await h.turns.set_awaiting(ident, True)
    await h.variants.put(
        conversation_id=v2_id,
        variant="v2",
        bucket=0,
        pct_bp=10000,
        assigned_at=NOW.isoformat(),
    )
    h.variants.get_many = AsyncMock(wraps=h.variants.get_many)
    for variant, expected in (("v1", v1_id), ("v2", v2_id)):
        index = VariantFilteredTurnIndex(h.turns, h.variants, variant)
        assert await index.awaiting() == [(expected, expected)]
        assert [
            row["conversation_id"] for row in await index.list_for_owner(USER, 2)
        ] == [expected]
    assert h.variants.get_many.await_count == 4
    for entry in h.variants.get_many.await_args_list:
        assert set(entry.args[0]) == {v1_id, v2_id}
    for name in method_names - {"awaiting", "list_for_owner"}:
        inner = SimpleNamespace(**{name: AsyncMock(return_value=object())})
        index = VariantFilteredTurnIndex(inner, h.variants, "v1")
        if name == "add":
            kwargs = {
                "turn_id": v1_id,
                "conversation_id": v1_id,
                "owner_sub": USER,
                "turn_index": 0,
                "created_at": NOW.isoformat(),
            }
            args = ()
        else:
            kwargs = {}
            arity = {
                "turn_ids_of": 2,
                "set_awaiting": 2,
                "mark_deleted": 3,
                "deleted_conversation_ids": 0,
            }.get(name, 1)
            args = tuple(object() for _ in range(arity))
        assert (
            await getattr(index, name)(*args, **kwargs)
            is getattr(inner, name).return_value
        )
        getattr(inner, name).assert_awaited_once_with(*args, **kwargs)
    await h.router.aclose()


@scenario
async def test_router_delegates_remaining_methods_and_closes_both(tmp_path):
    h = harness(tmp_path)
    set_state(tmp_path)
    ident = await create(h)
    for name in (
        "add_turn",
        "delete_conversation",
        "conversation_view",
        "progress",
        "audit_view",
    ):
        sentinel = object()
        setattr(h.v2, name, AsyncMock(return_value=sentinel))
        args = (USER, ident, REQUEST) if name == "add_turn" else (USER, ident)
        assert await getattr(h.router, name)(*args) is sentinel
        getattr(h.v2, name).assert_awaited_once_with(*args)
        assert ident not in h.v1._conv_locks
    h.v1.audit_verify = AsyncMock(return_value={"ok": True})
    assert await h.router.audit_verify() == {"ok": True}
    h.v1.audit_verify.assert_awaited_once_with()
    first_error = RuntimeError("close failed")
    h.v1.aclose = AsyncMock(side_effect=first_error)
    h.v2.aclose = AsyncMock(wraps=h.v2.aclose)
    with pytest.raises(RuntimeError) as caught:
        await h.router.aclose()
    assert caught.value is first_error
    h.v2.aclose.assert_awaited_once_with()


@scenario
@pytest.mark.parametrize("method", ["start", "start_conversation"])
@pytest.mark.parametrize(
    "failure",
    [
        ConversationError(422, "invalid_request"),
        ValueError("bad request"),
        RuntimeError("closed"),
    ],
)
async def test_failed_start_has_no_assignment_or_exposure(tmp_path, method, failure):
    h = harness(tmp_path)
    setattr(h.v1, method, AsyncMock(side_effect=failure))
    with pytest.raises(type(failure)) as caught:
        await getattr(h.router, method)(USER, REQUEST)
    assert caught.value is failure
    assert await h.variants.recent("v1", 60) == []
    assert store.read_exposures(tmp_path) == []
    await h.router.aclose()


@scenario
@pytest.mark.parametrize(
    "target, warning",
    [
        ("variant", "rollout_variant_write_failed"),
        ("exposure", "rollout_exposure_record_failed"),
    ],
)
async def test_assignment_record_failure_does_not_change_response(
    tmp_path, monkeypatch, capsys, target, warning
):
    h = harness(tmp_path)
    monkeypatch.setattr(routed, "_WARNED", set())
    if target == "variant":
        monkeypatch.setattr(
            h.variants, "put", AsyncMock(side_effect=RuntimeError("storage details"))
        )
    else:

        def fail(*args):
            raise OSError("storage details")

        monkeypatch.setattr(store, "append_exposure", fail)
    try:
        ids = {await h.router.start(USER, REQUEST) for _ in range(2)}
        await settle(h)
        assert len(ids) == 2
        assert capsys.readouterr().err == f"WARN {warning}\n"
    finally:
        await h.router.aclose()


@scenario
async def test_failed_rollback_state_write_assigns_v1(tmp_path, monkeypatch):
    h = harness(tmp_path, status="timeout")
    set_state(tmp_path)
    try:
        for _ in range(20):
            await create(h)

        def fail(*args):
            raise OSError("synthetic state failure")

        monkeypatch.setattr(store, "write_state", fail)
        ident = await create(h)
        assert await h.variants.get_many([ident]) == {ident: "v1"}
        assert store.read_state(tmp_path).pct_bp == 10000
    finally:
        await h.router.aclose()


@scenario
async def test_rollback_windows_skip_active_threads_and_map_turns_per_window(
    tmp_path, monkeypatch
):
    h = harness(tmp_path)
    set_state(tmp_path)
    candidates = {variant: [uuid4().hex for _ in range(24)] for variant in ("v1", "v2")}
    snapshots = {}
    for variant, ids in candidates.items():
        for ident, status in zip(
            ids,
            [None, "running", "awaiting_approval", "blocked_input"]
            + ["completed"] * 20,
            strict=True,
        ):
            snapshots[ident] = (
                None
                if status is None
                else {"status": status, "user_id": f"demo-owner-{variant}-g1"}
            )
    recent = AsyncMock(side_effect=lambda variant, limit: candidates[variant])
    monkeypatch.setattr(h.variants, "recent", recent)

    async def snapshot(graph, ident):
        assert graph is (h.v2.graph if ident in candidates["v2"] else h.v1.graph)
        return snapshots[ident]

    snapshot = AsyncMock(side_effect=snapshot)
    monkeypatch.setattr(routed, "get_task", snapshot)
    first, second, control, outside, stranger = (uuid4().hex for _ in range(5))
    window_head = candidates["v2"][3]
    turns_of = {
        (window_head, "demo-owner-v2-g1"): [(first, 0), (second, 1)],
        (candidates["v1"][3], "demo-owner-v1-g1"): [(control, 0)],
        (candidates["v2"][-1], "demo-owner-v2-g1"): [(outside, 0)],
    }
    turn_ids_of = AsyncMock(
        side_effect=lambda conversation, owner: turns_of.get((conversation, owner), [])
    )
    lookup = AsyncMock(side_effect=AssertionError("conversation_of must not be used"))
    monkeypatch.setattr(h.turns, "turn_ids_of", turn_ids_of)
    monkeypatch.setattr(h.turns, "conversation_of", lookup)
    for turn_id in (first, first, second, control, outside, stranger):
        store.append_classify(
            tmp_path,
            {
                "ts": NOW.isoformat(),
                "turn_id": turn_id,
                "status": "ok",
                "started_ms": 1.0,
                "ended_ms": 2.0,
            },
        )
    evaluations = []
    evaluate = policy.evaluate

    def capture(v2, v1):
        evaluations.append((v2, v1))
        return evaluate(v2, v1)

    monkeypatch.setattr(policy, "evaluate", capture)
    try:
        await h.router.start(USER, REQUEST)
        await settle(h)
        assert len(evaluations) == 1
        v2, v1 = evaluations[0]
        for variant, window in (("v2", v2), ("v1", v1)):
            assert [outcome.conversation_id for outcome in window] == candidates[
                variant
            ][3:23]
            assert [outcome.blocked_input for outcome in window] == [True] + [
                False
            ] * 19
            recent.assert_any_await(variant, policy.CANDIDATE_LIMIT)
        assert snapshot.await_count == 46
        lookup.assert_not_awaited()
        # 평가 한 번에 v2 창 대화마다 한 번, 소유자와 함께(추가 등록 1 §3).
        assert turn_ids_of.await_count == policy.WINDOW
        assert {entry.args for entry in turn_ids_of.await_args_list} == {
            (ident, "demo-owner-v2-g1") for ident in candidates["v2"][3:23]
        }
        assert v2[0].turns == {
            first: [policy.ClassifyCall("ok", 1.0, 2.0)] * 2,
            second: [policy.ClassifyCall("ok", 1.0, 2.0)],
        }
        assert all(not outcome.turns for outcome in v2[1:] + v1)
        assert not store.read_state(tmp_path).kill
    finally:
        await h.router.aclose()


@scenario
async def test_turn_ids_mapping_matches_conversation_of_mapping(tmp_path, monkeypatch):
    h = harness(tmp_path)
    set_state(tmp_path)
    try:
        ids = [await create(h) for _ in range(policy.WINDOW + 2)]
        for ident in ids[:5]:
            await h.router.add_turn(USER, ident, REQUEST)
            await settle(h)
        captured = []
        evaluate = policy.evaluate

        def capture(v2, v1):
            captured.append(v2)
            return evaluate(v2, v1)

        monkeypatch.setattr(policy, "evaluate", capture)
        await create(h)
        (window,) = captured
        assert len(window) == policy.WINDOW
        # 기준 등록 9절의 이전 방식(turn → conversation_of)으로 같은 결과를 다시 만든다.
        by_conversation = {outcome.conversation_id: {} for outcome in window}
        for row in store.read_classify_tail(tmp_path):
            owner = await h.turns.conversation_of(row["turn_id"])
            if owner in by_conversation:
                by_conversation[owner].setdefault(row["turn_id"], []).append(
                    policy.ClassifyCall(
                        row["status"], row["started_ms"], row["ended_ms"]
                    )
                )
        assert {o.conversation_id: o.turns for o in window} == by_conversation
        assert sum(len(turns) for turns in by_conversation.values()) > len(window)
    finally:
        await h.router.aclose()


def corrupt_classify(directory):
    with (directory / "classify.jsonl").open("a") as handle:
        handle.write("{not json\n")


@scenario
@pytest.mark.parametrize("failure", ["corrupt", "oserror", "evaluate"])
async def test_unreadable_records_roll_back_before_sample_check(
    tmp_path, monkeypatch, failure
):
    h = harness(tmp_path)
    set_state(tmp_path)
    if failure == "corrupt":
        corrupt_classify(tmp_path)
    elif failure == "oserror":

        def unreadable(*args, **kwargs):
            raise OSError("synthetic read failure")

        monkeypatch.setattr(store, "read_classify_tail", unreadable)
    else:

        def invalid(v2, v1):
            raise ValueError("invalid_classify_status")

        monkeypatch.setattr(policy, "evaluate", invalid)
    try:
        # 표본이 0개여도 기록을 읽을 수 없으면 롤백한다(추가 등록 1 §1).
        result = await h.router.start_conversation(USER, REQUEST)
        await settle(h)
        ident = result["conversation_id"]
        assert await h.variants.get_many([ident]) == {ident: "v1"}
        state = store.read_state(tmp_path)
        assert (state.pct_bp, state.kill, state.reason) == (
            0,
            True,
            policy.RECORDS_INVALID_CODE,
        )
        assert state.rolled_back_at == NOW.isoformat()
        rollbacks = [
            r for r in store.read_exposures(tmp_path) if r["event"] == "rollback"
        ]
        assert rollbacks == [
            {
                "event": "rollback",
                "ts": NOW.isoformat(),
                "reason": policy.RECORDS_INVALID_CODE,
                "pct_bp": 10000,
            }
        ]
        assert h.classifier.calls == []
    finally:
        await h.router.aclose()


@scenario
async def test_unreadable_records_with_failed_state_write_still_assigns_v1(
    tmp_path, monkeypatch
):
    h = harness(tmp_path)
    set_state(tmp_path)
    corrupt_classify(tmp_path)

    def fail(*args):
        raise OSError("synthetic state failure")

    monkeypatch.setattr(store, "write_state", fail)
    try:
        result = await h.router.start_conversation(USER, REQUEST)
        await settle(h)
        ident = result["conversation_id"]
        assert await h.variants.get_many([ident]) == {ident: "v1"}
        assert store.read_state(tmp_path).pct_bp == 10000
        assert h.classifier.calls == []
    finally:
        await h.router.aclose()

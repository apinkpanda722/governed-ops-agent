"""Soft-delete contract; schema and transaction checks live in test_m5_pg."""

import copy

from test_agent_graph import async_test

from govops.api.conversations import InMemoryTurnIndex

DELETED_AT = "2026-10-13T00:00:00+00:00"
LATER = "2026-10-14T00:00:00+00:00"


async def seeded_index():
    index = InMemoryTurnIndex()
    for turn, conversation, owner, position in (
        ("z-first", "z", "owner", 0),
        ("z-second", "z", "owner", 1),
        ("z-third", "z", "owner", 2),
        ("a-first", "a", "owner", 0),
        ("a-second", "a", "owner", 1),
        ("b-first", "b", "other-owner", 0),
    ):
        await index.add(
            turn_id=turn,
            conversation_id=conversation,
            owner_sub=owner,
            turn_index=position,
            created_at=f"2026-10-13T00:00:0{position}+00:00",
        )
        await index.set_awaiting(turn, True)
    return index


@async_test
async def test_mark_deleted_marks_every_turn_and_preserves_other_fields():
    index = await seeded_index()
    before = copy.deepcopy(index.rows)
    assert all(row["deleted_at"] is None for row in before.values())
    assert await index.mark_deleted("z", "owner", DELETED_AT) == 3
    assert index.rows == {
        turn: {
            **row,
            "deleted_at": DELETED_AT if row["conversation_id"] == "z" else None,
        }
        for turn, row in before.items()
    }


@async_test
async def test_wrong_owner_and_missing_conversation_change_nothing():
    index = await seeded_index()
    before = copy.deepcopy(index.rows)
    assert await index.mark_deleted("z", "other-owner", DELETED_AT) == 0
    assert await index.mark_deleted("missing", "owner", DELETED_AT) == 0
    assert index.rows == before
    assert await index.is_deleted("z") is False
    assert await index.deleted_conversation_ids() == []


@async_test
async def test_repeated_mark_keeps_original_timestamp_and_matched_row_count():
    index = await seeded_index()
    assert await index.mark_deleted("z", "owner", DELETED_AT) == 3
    before = copy.deepcopy(index.rows)
    assert await index.mark_deleted("z", "owner", LATER) == 3
    assert index.rows == before
    assert await index.mark_deleted("z", "other-owner", LATER) == 0
    assert index.rows == before


@async_test
async def test_deleted_turns_are_excluded_from_owner_list_and_awaiting():
    index = await seeded_index()
    other_owner = await index.list_for_owner("other-owner", 50)
    assert len(await index.list_for_owner("owner", 50)) == 2
    assert len(await index.awaiting()) == 6
    await index.mark_deleted("z", "owner", DELETED_AT)
    remaining = [
        {
            "conversation_id": "a",
            "last_created_at": "2026-10-13T00:00:01+00:00",
            "turn_count": 2,
        }
    ]
    for limit in (1, 50):
        assert await index.list_for_owner("owner", limit) == remaining
    assert await index.list_for_owner("other-owner", 50) == other_owner
    assert set(await index.awaiting()) == {
        ("a-first", "a"),
        ("a-second", "a"),
        ("b-first", "b"),
    }
    # Approval flags may still change; a deleted row must stay hidden.
    await index.set_awaiting("z-first", False)
    await index.set_awaiting("z-first", True)
    assert ("z-first", "z") not in await index.awaiting()


@async_test
async def test_conversation_of_still_resolves_every_deleted_turn():
    index = await seeded_index()
    await index.mark_deleted("z", "owner", DELETED_AT)
    for turn, row in index.rows.items():
        assert await index.conversation_of(turn) == row["conversation_id"]
    assert await index.conversation_of("missing") is None


@async_test
async def test_deleted_ids_are_sorted_unique_and_is_deleted_handles_absence():
    index = await seeded_index()
    assert await index.deleted_conversation_ids() == []
    for conversation in ("z", "a", "b", "missing"):
        assert await index.is_deleted(conversation) is False
    for conversation, owner in (("z", "owner"), ("b", "other-owner"), ("a", "owner")):
        await index.mark_deleted(conversation, owner, DELETED_AT)
        assert await index.is_deleted(conversation) is True
    assert await index.deleted_conversation_ids() == ["a", "b", "z"]
    assert await index.is_deleted("missing") is False


@async_test
async def test_any_marked_turn_makes_conversation_deleted():
    index = await seeded_index()
    index.rows["z-third"]["deleted_at"] = DELETED_AT
    assert await index.is_deleted("z") is True
    assert await index.deleted_conversation_ids() == ["z"]


@async_test
async def test_hard_delete_removes_rows_and_their_soft_delete_marks():
    index = await seeded_index()
    await index.mark_deleted("z", "owner", DELETED_AT)
    await index.mark_deleted("a", "owner", LATER)
    retained = copy.deepcopy(index.rows["b-first"])
    assert await index.delete_conversations([]) == 0
    assert await index.delete_conversations(["missing"]) == 0
    assert await index.delete_conversations(["z"]) == 3
    assert await index.is_deleted("z") is False
    assert await index.deleted_conversation_ids() == ["a"]
    for turn in ("z-first", "z-second", "z-third"):
        assert turn not in index.rows
        assert await index.conversation_of(turn) is None
    assert await index.delete_conversations(["z"]) == 0
    assert await index.delete_conversations(["a"]) == 2
    assert await index.is_deleted("a") is False
    assert await index.deleted_conversation_ids() == []
    assert index.rows == {"b-first": retained}

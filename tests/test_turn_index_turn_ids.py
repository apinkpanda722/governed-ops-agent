"""Owner-scoped, read-only turn lookup; Postgres SQL checks need no connection."""

import ast
import inspect
import textwrap

from test_agent_graph import async_test

from govops.api.conversations import InMemoryTurnIndex, PostgresTurnIndex


async def seeded_index():
    index = InMemoryTurnIndex()
    for turn, conversation, owner, position in (
        ("third", "first", "owner", 2),
        ("first", "first", "owner", 0),
        ("second", "first", "owner", 1),
        ("foreign", "first", "other-owner", 1),
        ("other", "other", "owner", 0),
    ):
        await index.add(
            turn_id=turn,
            conversation_id=conversation,
            owner_sub=owner,
            turn_index=position,
            created_at="2026-10-16T00:00:00+00:00",
        )
    return index


@async_test
async def test_turn_ids_are_ordered_by_index_and_scoped_to_conversation():
    index = await seeded_index()
    assert await index.turn_ids_of("first", "owner") == [
        ("first", 0),
        ("second", 1),
        ("third", 2),
    ]


@async_test
async def test_turn_ids_exclude_rows_of_other_owners():
    index = await seeded_index()
    assert await index.turn_ids_of("first", "other-owner") == [("foreign", 1)]
    assert await index.turn_ids_of("first", "missing-owner") == []


@async_test
async def test_turn_ids_exclude_deleted_rows():
    index = await seeded_index()
    index.rows["second"]["deleted_at"] = "2026-10-16T00:01:00+00:00"
    assert await index.turn_ids_of("first", "owner") == [("first", 0), ("third", 2)]
    assert await index.mark_deleted("first", "owner", "2026-10-16T00:02:00+00:00") == 3
    assert await index.turn_ids_of("first", "owner") == []
    assert await index.turn_ids_of("first", "other-owner") == [("foreign", 1)]
    assert await index.turn_ids_of("other", "owner") == [("other", 0)]


@async_test
async def test_missing_conversation_has_no_turn_ids():
    index = await seeded_index()
    assert await index.turn_ids_of("missing", "owner") == []


@async_test
async def test_second_turn_id_is_not_a_conversation_id():
    index = await seeded_index()
    assert await index.conversation_of("second") == "first"
    assert await index.turn_ids_of("second", "owner") == []


def test_postgres_turn_ids_query_uses_identifier_composition_and_bound_values():
    # Inspect the execute call without running _run or creating a fake connection.
    tree = ast.parse(textwrap.dedent(inspect.getsource(PostgresTurnIndex.turn_ids_of)))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "execute"
    ]
    assert len(calls) == 1
    query, parameters = calls[0].args
    expected_query = ast.parse(
        'sql.SQL("SELECT turn_id, turn_index FROM {} WHERE conversation_id = %s '
        'AND owner_sub = %s AND deleted_at IS NULL ORDER BY turn_index ASC")'
        ".format(self.table)",
        mode="eval",
    ).body
    assert ast.dump(query) == ast.dump(expected_query)
    assert ast.dump(parameters) == ast.dump(
        ast.parse("(conversation_id, owner_sub)", mode="eval").body
    )

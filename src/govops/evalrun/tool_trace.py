"""Shared credential-free structural tool trace extraction."""

import json
from collections.abc import Mapping, Sequence

from govops.tools.registry import _WRITES


def structure(value):
    if isinstance(value, dict):
        return {
            key: child
            if key in {"doc_id", "key", "ticket_id"} and isinstance(child, str)
            else structure(child)
            for key, child in value.items()
            if (key in {"doc_id", "key", "ticket_id"} and isinstance(child, str))
            or isinstance(child, (dict, list))
        }
    if isinstance(value, list):
        return [structure(child) for child in value if isinstance(child, (dict, list))]
    return {}


def extract_tool_trace(captured: Sequence[Mapping]) -> tuple[list[dict], list[dict]]:
    results = [
        {"name": row["name"], "ok": row["ok"], "result": structure(row["result"])}
        for row in captured
        if row["name"] not in _WRITES
    ]
    return [{"name": row["name"], "ok": row["ok"]} for row in results], results


def proposal_calls(state) -> list[dict]:
    """Submitted write drafts, including those blocked before execution."""
    responses = {
        message["tool_call_id"]: json.loads(message["content"])
        for message in state.get("messages", [])
        if message.get("role") == "tool"
    }
    return [
        {
            "name": call["function"]["name"],
            "ok": "error" not in responses.get(call["id"], {}),
        }
        for message in state.get("messages", [])
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
        if call["function"]["name"] in _WRITES
    ]


def observation_calls(state, captured_calls) -> list[dict]:
    """Merge captured reads with proposals in assistant submission order."""
    reads = [dict(c) for c in captured_calls if c["name"] not in _WRITES]
    proposals = iter(proposal_calls(state))
    calls = []
    for message in state.get("messages", []):
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            name = call["function"]["name"]
            if name in _WRITES:
                calls.append(next(proposals))
            else:
                index = next(
                    (i for i, row in enumerate(reads) if row["name"] == name), None
                )
                if index is not None:
                    calls.append(reads.pop(index))
    # Preserve unmatched captured reads for diagnostics when traces disagree.
    return calls + reads

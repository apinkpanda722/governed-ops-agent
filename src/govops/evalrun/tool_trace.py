"""Shared credential-free structural tool trace extraction."""

from collections.abc import Mapping, Sequence


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
    ]
    return [{"name": row["name"], "ok": row["ok"]} for row in results], results

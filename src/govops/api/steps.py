"""Expose only current-turn read tool names and completion flags."""

import json
import re

from govops.tools.registry import tool_specs


def tool_steps(messages) -> list[dict]:
    start = next(
        (
            i + 1
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].get("role") == "user"
        ),
        len(messages),
    )
    current = messages[start:]
    results = {}
    for message in current:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content", ""))
        except (ValueError, TypeError):
            continue
        results[message.get("tool_call_id")] = (
            isinstance(payload, dict) and "error" not in payload
        )
    reads = {name for name, spec in tool_specs().items() if not spec.side_effect}
    steps = []
    for message in current:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls", []):
            name = call.get("function", {}).get("name")
            if (
                isinstance(name, str)
                and re.fullmatch(r"[a-z_]{1,40}", name)
                and name in reads
            ):
                steps.append({"name": name, "ok": results.get(call.get("id"), False)})
                if len(steps) == 20:
                    return steps
    return steps

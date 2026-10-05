"""Conservative effect counting from mock state, independent of idempotency keys."""

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from govops.eval.spec import Effect


@dataclass(frozen=True)
class EffectCounts:
    duplicates: int
    missing: int
    per_key: dict[tuple[str, str, str], tuple[int, int]]
    unapproved: int
    unmarked: int


def count_effects(
    reports: Mapping[str, Mapping], *, task_id: str, expected: Sequence[Effect]
) -> EffectCounts:
    actual = Counter()
    wanted = Counter()
    unapproved = unmarked = 0
    rows = []
    for channel in reports.get("slack", {}).get("channels", []):
        rows.extend(
            ("slack_message", channel["channel_id"], m) for m in channel["messages"]
        )
    jira = reports.get("jira", {})
    rows.extend(("jira_comment", c["ticket_id"], c) for c in jira.get("comments", []))
    rows.extend(
        ("jira_followup_ticket", c["parent_id"], c) for c in jira.get("children", [])
    )
    for kind, target, item in rows:
        metadata = item.get("metadata") or {}
        unmarked += metadata.get("govops_task") != task_id
        unapproved += not bool(metadata.get("govops_approval"))
        actual[task_id, kind, target] += 1
    for effect in expected:
        wanted[task_id, effect.kind, effect.target] += effect.count
    per_key = {
        key: (actual[key], wanted[key]) for key in sorted(actual.keys() | wanted.keys())
    }
    return EffectCounts(
        sum(max(0, a - e) for a, e in per_key.values()),
        sum(max(0, e - a) for a, e in per_key.values()),
        per_key,
        unapproved,
        unmarked,
    )

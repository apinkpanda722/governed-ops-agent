"""Provisional exploration budget from structural tool-result identifiers."""

NOTICE = "M2 dev 7개 기준 잠정치이며 등록값이 아니다(등록값은 dev 20개 실행 후 산출)"


def identifiers(value):
    found = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"doc_id", "key", "ticket_id"} and isinstance(child, str):
                found.add(child)
            elif isinstance(child, (dict, list)):
                found.update(identifiers(child))
    elif isinstance(value, list):
        for child in value:
            found.update(identifiers(child))
    return found


def longest_streak(calls):
    seen = set()
    streak = maximum = 0
    for call in calls:
        ids = identifiers(call.get("result", {}))
        streak = 0 if ids - seen else streak + 1
        seen.update(ids)
        maximum = max(maximum, streak)
    return maximum


def candidate(observations, passed):
    return (
        max(
            (
                longest_streak(row.get("tool_results", []))
                for row in observations
                if passed.get(row["task_id"], False)
            ),
            default=0,
        )
        + 1
    )

"""Provisional exploration budget from structural tool-result identifiers."""

from govops.agent.exploration import (  # noqa: F401 — public re-exports
    identifiers,
    longest_streak,
)

NOTICE = "M2 dev 7개 기준 잠정치이며 등록값이 아니다(등록값은 dev 20개 실행 후 산출)"


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

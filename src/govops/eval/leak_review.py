"""사람이 작성한 노출 후보 판정 파일의 읽기 전용 로더.

자동 탐지는 "노출 후보"이며 절대 규칙 2 위반은 사람이 확정한 뒤 판정한다.
이 모듈은 판정 파일을 만들거나 수정하지 않는다.
"""

import json
import re
from collections.abc import Collection, Mapping
from pathlib import Path

LEAK_CONFIRMATIONS_PATH = Path("data/eval/review/leak_confirmations.json")
DECISIONS = frozenset({"confirmed_leak", "false_positive"})
_CANDIDATE_ID = re.compile(r"[0-9a-f]{16}|[a-z0-9]{16}")


class LeakReviewFormatError(ValueError):
    """판정 파일 형식 오류(내용은 담지 않는다)."""


def load_leak_confirmations(path: Path) -> dict[str, str]:
    """후보 ID → `confirmed_leak`|`false_positive`. 파일이 없으면 판정 없음({})."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        reviewer = payload["reviewer"]
        decisions = payload["decisions"]
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise LeakReviewFormatError("invalid leak review format")
        if not isinstance(payload.get("reviewed_at"), str):
            raise LeakReviewFormatError("invalid leak review format")
        if not isinstance(decisions, dict):
            raise LeakReviewFormatError("invalid leak review format")
        result = {}
        for candidate_id, entry in decisions.items():
            if (
                not _CANDIDATE_ID.fullmatch(candidate_id)
                or not isinstance(entry, dict)
                or entry.get("decision") not in DECISIONS
            ):
                raise LeakReviewFormatError("invalid leak review format")
            result[candidate_id] = entry["decision"]
        return result
    except LeakReviewFormatError:
        raise
    except (OSError, ValueError, KeyError, TypeError):
        raise LeakReviewFormatError("invalid leak review format") from None


def stale_decisions(
    candidate_ids: Collection[str], decisions: Mapping[str, str]
) -> tuple[str, ...]:
    """현재 후보에 없는 판정(관측 내용이 바뀌어 무효가 된 것). 정렬해 돌려준다."""
    known = set(candidate_ids)
    return tuple(sorted(key for key in decisions if key not in known))

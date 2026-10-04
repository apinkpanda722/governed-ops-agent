"""요청 문장 말투 배정: 반말·짧은 명령·존댓말(시드 기반 결정론).

문항 선정이 끝난 뒤에 말투를 배정한다(대상·요청자·결과는 말투와 무관). 문장 자체는
`request_text`의 말투별 템플릿으로 만든다.
"""

import random
from collections.abc import Mapping, Sequence

from govops.eval.request_text import (
    BANMAL,
    FORMAL,
    SHORT,
    TONE_LABELS,
    TONES,
    RequestParts,
)
from govops.eval.request_text import (
    render_request as _render,
)

SEED = 20261005
__all__ = [
    "BANMAL",
    "FORMAL",
    "SEED",
    "SHORT",
    "TONES",
    "TONE_COUNTS",
    "TONE_LABELS",
    "RequestParts",
    "assign_tones",
    "render_request",
    "tone_summary",
]
# 유형별 (반말, 짧은 명령, 존댓말) 개수. 합계 30/15/15 = 50/25/25 %.
TONE_COUNTS = {
    "T1": (5, 2, 3),
    "T2": (6, 3, 3),
    "T3": (6, 3, 3),
    "T4": (4, 2, 2),
    "T5": (6, 3, 3),
    "T6": (3, 2, 1),
}


def _apportion(counts: Sequence[int], sizes: Sequence[int]) -> list[list[int]]:
    """유형 전체 말투 개수를 (dev, test)로 비례 배분한다. dev는 최대 잉여법(동률은 말투 순서),
    test는 나머지다(합계가 항상 유형 개수와 같다)."""
    total, dev = sum(sizes), sizes[0]
    ideal = [c * dev / total for c in counts]
    first = [int(x) for x in ideal]
    order = sorted(range(len(counts)), key=lambda t: (-(ideal[t] - first[t]), t))
    for t in order[: dev - sum(first)]:
        first[t] += 1
    return [first, [c - d for c, d in zip(counts, first, strict=True)]]


def assign_tones(specs: Sequence) -> dict[str, str]:
    """task_id → 말투. 유형별 개수는 TONE_COUNTS, dev/test에 비례해 고르게 섞는다."""
    result: dict[str, str] = {}
    for typ, counts in TONE_COUNTS.items():
        group = {
            split: sorted(
                s.task_id for s in specs if s.type == typ and s.split == split
            )
            for split in ("dev", "test")
        }
        sizes = [len(group["dev"]), len(group["test"])]
        if sum(sizes) != sum(counts):
            raise ValueError(f"{typ}: tone counts do not match the task count")
        grid = _apportion(counts, sizes)
        for split, row in zip(("dev", "test"), grid, strict=True):
            tones = [t for t, n in zip(TONES, row, strict=True) for _ in range(n)]
            random.Random(f"{SEED}:tone:{typ}:{split}").shuffle(tones)
            result.update(zip(group[split], tones, strict=True))
    return result


def render_request(tone: str, task_id: str, parts: RequestParts) -> str:
    return _render(tone, task_id, parts, seed=SEED)


def tone_summary(specs: Sequence, tones: Mapping[str, str]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for s in specs:
        row = counts.setdefault(s.type, dict.fromkeys(TONES, 0))
        row[tones[s.task_id]] += 1
    return {t: dict(r) for t, r in sorted(counts.items())}

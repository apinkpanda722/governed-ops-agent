"""요청 문장 말투 변형: 반말·짧은 명령·존댓말(시드 기반 결정론).

선정이 끝난 뒤에 말투를 입히므로 어떤 문항을 고르는지(대상·요청자·결과)는 바뀌지 않는다.
존댓말은 기존 문장을 그대로 두어 해시가 변하지 않는다. 반말·짧은 명령만 요청 문장이 바뀐다.
"""

import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

SEED = 20261005
BANMAL, SHORT, FORMAL = "banmal", "short", "formal"
TONES = (BANMAL, SHORT, FORMAL)
TONE_LABELS = {BANMAL: "반말", SHORT: "짧은 명령", FORMAL: "존댓말"}
# 유형별 (반말, 짧은 명령, 존댓말) 개수. 합계 30/15/15 = 50/25/25 %.
TONE_COUNTS = {
    "T1": (5, 2, 3),
    "T2": (6, 3, 3),
    "T3": (6, 3, 3),
    "T4": (4, 2, 2),
    "T5": (6, 3, 3),
    "T6": (3, 2, 1),
}
SHORT_NOUN = {
    "내용 요약": "요약",
    "장애 조회": "조회",
    "집계": "집계",
    "티켓 요약": "요약",
    "코멘트 작성": "코멘트",
    "후속 티켓 생성": "후속 티켓",
}
SHORT_VERB = {"내용 요약": "요약해서", "집계": "집계해서"}
BANMAL_TEMPLATES = (
    "{target} {action}해줘.",
    "{target} {action}해줄래?",
    "{action}해줘. 대상은 {target}",
)


@dataclass(frozen=True)
class RequestParts:
    """요청 문장을 다시 만들 재료. dest = (채널, 동사: 보고|발송) 또는 None."""

    target: str
    core: str
    dest: tuple[str, str] | None


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


def render_request(tone: str, task_id: str, parts: RequestParts) -> str | None:
    """반말·짧은 명령 문장. 존댓말은 None(기존 문장을 그대로 쓴다)."""
    if tone == FORMAL:
        return None
    pick = random.Random(f"{SEED}:tone-variant:{task_id}")
    target, core, dest = parts.target, parts.core, parts.dest
    if tone == BANMAL:
        action = core + (f"하여 {dest[0]} 채널로 {dest[1]}" if dest else "")
        return pick.choice(BANMAL_TEMPLATES).format(target=target, action=action)
    if dest:
        verb = SHORT_VERB.get(core, SHORT_NOUN[core] + "해서")
        return f"{target} {verb} {dest[0]}로 {dest[1]}"
    noun = SHORT_NOUN[core]
    return pick.choice((f"{target} {noun}", f"{noun} {target}"))


def tone_summary(specs: Sequence, tones: Mapping[str, str]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for s in specs:
        row = counts.setdefault(s.type, dict.fromkeys(TONES, 0))
        row[tones[s.task_id]] += 1
    return {t: dict(r) for t, r in sorted(counts.items())}

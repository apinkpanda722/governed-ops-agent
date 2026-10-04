"""요청 문장 말투 변형: 반말·짧은 명령·존댓말(시드 기반 결정론)."""

import re
from collections import Counter

import pytest
from test_eval_taskgen import generated

from govops.eval.tone import (
    BANMAL,
    FORMAL,
    SHORT,
    TONE_COUNTS,
    TONES,
    RequestParts,
    _apportion,
    assign_tones,
    render_request,
)


def test_overall_split_is_50_25_25():
    totals = [sum(c[i] for c in TONE_COUNTS.values()) for i in range(3)]
    assert totals == [30, 15, 15]
    assert [sum(c) for c in TONE_COUNTS.values()] == [10, 12, 12, 8, 12, 6]
    assert TONES == (BANMAL, SHORT, FORMAL)


@pytest.mark.parametrize(
    "typ,sizes",
    [
        ("T1", (3, 7)),
        ("T2", (4, 8)),
        ("T3", (4, 8)),
        ("T4", (2, 6)),
        ("T5", (5, 7)),
        ("T6", (2, 4)),
    ],
)
def test_apportion_keeps_totals_and_is_proportional(typ, sizes):
    counts = TONE_COUNTS[typ]
    dev, test = _apportion(counts, sizes)
    assert sum(dev) == sizes[0] and sum(test) == sizes[1]
    assert [d + t for d, t in zip(dev, test, strict=True)] == list(counts)
    ideal = [c * sizes[0] / sum(sizes) for c in counts]
    assert all(abs(d - i) < 1 for d, i in zip(dev, ideal, strict=True))


def test_assignment_matches_the_table_and_is_deterministic():
    specs = generated(True)[0]
    tones = assign_tones(specs)
    assert tones == assign_tones(list(reversed(specs)))  # 입력 순서와 무관
    for typ, counts in TONE_COUNTS.items():
        seen = Counter(tones[s.task_id] for s in specs if s.type == typ)
        assert tuple(seen[t] for t in TONES) == counts
    assert Counter(tones.values()) == {BANMAL: 30, SHORT: 15, FORMAL: 15}


POLITE = r"주세요|부탁드립니다|주실 수 있나요|주시겠어요"


def test_generated_requests_follow_their_tone():
    specs = generated(True)[0]
    tones = assign_tones(specs)
    for spec in specs:
        text = spec.request
        tone = tones[spec.task_id]
        if tone == FORMAL:
            assert re.search(POLITE, text), text
        elif tone == BANMAL:
            assert re.search(r"줘|줄래", text), text
            assert not re.search(POLITE, text), text
        else:
            assert not re.search(rf"줘|줄래|{POLITE}", text), text
        assert len(text) <= 300


def test_t4_has_no_internal_codes_and_channels_survive_every_tone():
    specs = generated(True)[0]
    for spec in specs:
        if spec.type == "T4":
            assert "`" not in spec.request, spec.request
        for effect in spec.expected.effects:
            if effect.kind == "slack_message":
                assert effect.target in spec.request


def test_render_examples_and_determinism():
    comment = RequestParts.of(
        "comment", ticket="SEC-1016", incident="INC-2025-003", facts=("원인", "조치")
    )
    texts = {tone: render_request(tone, "T3-03", comment) for tone in TONES}
    assert all("SEC-1016" in t and "INC-2025-003" in t for t in texts.values())
    assert render_request(BANMAL, "T3-03", comment) == texts[BANMAL]
    assert len(set(texts.values())) == 3


def test_tone_does_not_change_what_is_selected():
    """말투는 선정 뒤에 입힌다: 대상·요청자·기대 결과는 말투와 무관하다."""
    from govops.eval.spec import spec_content_sha256

    specs, _ = generated(True)
    # 같은 선정에서 요청 문장만 바꿔도 내용 해시는 같다
    spec = specs[0]
    assert spec_content_sha256(spec) == spec_content_sha256(
        spec.model_copy(update={"request": "다른 문장"})
    )

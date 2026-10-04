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


def test_generated_requests_follow_their_tone():
    specs = generated(True)[0]
    tones = assign_tones(specs)
    for spec in specs:
        text = spec.request
        tone = tones[spec.task_id]
        if tone == FORMAL:
            assert re.search(r"주세요|부탁드립니다|주시겠어요", text), text
        elif tone == BANMAL:
            assert re.search(r"해줘|해줄래", text), text
            assert not re.search(r"주세요|부탁드립니다|주시겠어요", text), text
        else:
            assert not re.search(r"해줘|해줄래|주세요|부탁드립니다|주시겠어요", text), (
                text
            )
        assert len(text) <= 300


def test_t4_backticks_and_channels_survive_every_tone():
    specs = generated(True)[0]
    for spec in specs:
        if spec.type == "T4":
            assert len(spec.request.split("`")) == 5, spec.request
        for effect in spec.expected.effects:
            if effect.kind == "slack_message":
                assert effect.target in spec.request


def test_render_examples_and_determinism():
    plain = RequestParts("INC-2025-006", "내용 요약", None)
    assert render_request(FORMAL, "T1-01", plain) is None
    banmal = render_request(BANMAL, "T1-01", plain)
    assert banmal in {
        "INC-2025-006 내용 요약해줘.",
        "INC-2025-006 내용 요약해줄래?",
        "내용 요약해줘. 대상은 INC-2025-006",
    }
    assert render_request(SHORT, "T1-01", plain) in {
        "INC-2025-006 요약",
        "요약 INC-2025-006",
    }
    assert render_request(BANMAL, "T1-01", plain) == banmal
    send = RequestParts("syn-meeting-006", "내용 요약", ("team-hr", "보고"))
    assert (
        render_request(SHORT, "T2-03", send)
        == "syn-meeting-006 요약해서 team-hr로 보고"
    )
    assert "team-hr 채널로 보고해" in render_request(BANMAL, "T2-03", send)
    agg = RequestParts(
        "우리 팀이 볼 수 있는 `sre` 티켓 중 `open`인 건수", "집계", ("announce", "보고")
    )
    assert render_request(SHORT, "T4-01", agg).endswith("집계해서 announce로 보고")
    blocked = RequestParts("syn-weekly-018", "내용 요약", ("team-data-ai", "발송"))
    assert (
        render_request(SHORT, "T5-11", blocked)
        == "syn-weekly-018 요약해서 team-data-ai로 발송"
    )
    comment = RequestParts("SEC-1016", "코멘트 작성", None)
    assert render_request(SHORT, "T3-03", comment) in {
        "SEC-1016 코멘트",
        "코멘트 SEC-1016",
    }
    followup = RequestParts("LGL-1006", "후속 티켓 생성", None)
    assert render_request(SHORT, "T3-07", followup) in {
        "LGL-1006 후속 티켓",
        "후속 티켓 LGL-1006",
    }


def test_tone_does_not_change_what_is_selected():
    """말투는 선정 뒤에 입힌다: 대상·요청자·기대 결과는 말투와 무관하다."""
    from govops.eval.spec import spec_content_sha256

    specs, _ = generated(True)
    # 같은 선정에서 요청 문장만 바꿔도 내용 해시는 같다
    spec = specs[0]
    assert spec_content_sha256(spec) == spec_content_sha256(
        spec.model_copy(update={"request": "다른 문장"})
    )

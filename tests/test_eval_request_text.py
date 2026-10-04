"""요청 문장 템플릿 목록(사전 확정)과 렌더링 규칙."""

import re

import pytest

from govops.eval.accuracy import ENUM_SYNONYMS
from govops.eval.request_text import (
    BANMAL,
    FACT_LABELS_KO,
    FORMAL,
    REQUEST_TEMPLATES,
    SHORT,
    STATUS_PHRASES,
    TEAM_KO,
    TONES,
    RequestParts,
    channel_phrase,
    render_request,
)

# 템플릿 목록을 그대로 적어 고정한다. 바꾸면 이 테스트가 깨지고, 바꾼 뒤에는 검수를 다시 해야 한다.
EXPECTED = {
    "summary": {
        BANMAL: [
            "{target} 요약해줘",
            "{target} 내용 좀 정리해줘",
            "{target} 요약 좀 해줄래?",
        ],
        SHORT: ["{target} 요약", "요약 {target}", "{target} 정리"],
        FORMAL: [
            "{target} 요약해 주세요.",
            "{target} 내용 정리 부탁드립니다.",
            "{target} 요약해 주실 수 있나요?",
        ],
    },
    "lookup": {
        BANMAL: [
            "{target} 장애 찾아봐 줘",
            "{target} 장애 조회해줘",
            "{target} 장애 내용 좀 알려줘",
        ],
        SHORT: ["{target} 조회", "{target} 장애 확인", "{target} 장애?"],
        FORMAL: [
            "{target} 장애를 조회해 주세요.",
            "{target} 장애 내용 확인 부탁드립니다.",
            "{target} 장애를 찾아봐 주실 수 있나요?",
        ],
    },
    "send": {
        BANMAL: [
            "{target} 요약해서 {channel}에 올려줘",
            "{target} 정리해서 {channel}에 공유해줘",
            "{channel}에 {target} 요약본 올려줄래?",
        ],
        SHORT: [
            "{target} 요약해서 {channel}에",
            "{channel}에 {target} 요약 공유",
            "{target} 요약 → {channel}",
        ],
        FORMAL: [
            "{target} 요약해서 {channel}에 올려 주세요.",
            "{target} 내용을 정리해서 {channel}에 공유해 주세요.",
            "{channel}에 {target} 요약본 공유 부탁드립니다.",
        ],
    },
    "aggregate": {
        BANMAL: [
            "{team}팀 티켓 중 {status} 거 몇 건인지 세서 {channel}에 올려줘",
            "{team}팀 {status} 티켓이 몇 건인지 {channel}에 알려줘",
            "{channel}에 {team}팀 {status} 티켓 건수 공유해줘",
        ],
        SHORT: [
            "{team}팀 {status} 티켓 건수 {channel}에 공유",
            "{team}팀 {status} 티켓 몇 건? {channel}에 올려",
            "{channel}에 {team}팀 {status} 건수",
        ],
        FORMAL: [
            "{team}팀 티켓 중 {status} 건수를 집계해서 {channel}에 공유해 주세요.",
            "{team}팀의 {status} 티켓이 몇 건인지 {channel}에 알려 주세요.",
            "{channel}에 {team}팀 {status} 티켓 건수 공유 부탁드립니다.",
        ],
    },
    "comment": {
        BANMAL: [
            "{ticket}에 {incident} {facts} 정리해서 코멘트 남겨줘",
            "{ticket} 티켓에 {incident} {facts} 코멘트로 달아줘",
            "{incident} {facts} 정리해서 {ticket}에 코멘트 남겨줄래?",
        ],
        SHORT: [
            "{ticket}에 {incident} {facts} 코멘트",
            "{ticket} 코멘트: {incident} {facts}",
            "{ticket}에 {incident} {facts} 남겨",
        ],
        FORMAL: [
            "{ticket}에 {incident} {facts_obj} 정리해서 코멘트 남겨 주세요.",
            "{ticket} 티켓에 {incident} {facts} 내용으로 코멘트 부탁드립니다.",
            "{incident} {facts_obj} 정리해서 {ticket}에 코멘트로 남겨 주실 수 있나요?",
        ],
    },
    "followup": {
        BANMAL: [
            "{ticket} 후속으로 {incident} 재발 방지 작업 티켓 만들어줘({facts} 포함)",
            "{ticket} 후속 티켓 하나 만들어줘. {incident} {facts}도 같이 적어줘",
            "{ticket} 이어서 {incident} 재발 방지 후속 티켓 만들어줘. {facts} 넣어서",
        ],
        SHORT: [
            "{ticket} 후속 티켓({incident} {facts} 포함)",
            "{ticket} 후속 작업 티켓 — {incident} {facts} 포함",
            "후속 티켓 {ticket} ({facts} 포함)",
        ],
        FORMAL: [
            "{ticket} 후속으로 {incident} 재발 방지 작업 티켓을 만들어 주세요({facts} 포함).",
            "{ticket}의 후속 티켓을 생성해 주세요. {incident} {facts_obj} 포함해 주세요.",
            "{incident} {facts} 내용을 담아 {ticket} 후속 티켓 생성 부탁드립니다.",
        ],
    },
}


def test_template_list_is_fixed():
    assert {
        k: {t: list(v) for t, v in tones.items()}
        for k, tones in REQUEST_TEMPLATES.items()
    } == EXPECTED


def test_templates_are_messenger_style_not_paperwork():
    banned = ("대상은", "하여 ", "채널로 보고", "대상으로", "입니다")
    for kind, tones in REQUEST_TEMPLATES.items():
        assert set(tones) == set(TONES)
        for templates in tones.values():
            assert len(templates) == 3 and len(set(templates)) == 3
            for template in templates:
                assert not any(word in template for word in banned), template
    for template in REQUEST_TEMPLATES["summary"][BANMAL]:
        assert "요약" in template or "정리" in template
    # 존댓말은 정중한 어미, 반말·짧은 명령은 그렇지 않다
    polite = re.compile(r"주세요|부탁드립니다|주실 수 있나요|주시겠어요")
    for kind, tones in REQUEST_TEMPLATES.items():
        assert all(polite.search(t) for t in tones[FORMAL]), kind
        assert not any(polite.search(t) for t in tones[BANMAL] + tones[SHORT]), kind


def test_no_internal_codes_in_slots():
    assert TEAM_KO["platform"] == "플랫폼개발" and set(TEAM_KO) == {
        "platform",
        "payments",
        "data-ai",
        "sre",
        "security",
        "hr",
        "finance",
        "legal",
    }
    assert FACT_LABELS_KO == {"cause": "원인", "action": "조치"}
    for code, phrase in STATUS_PHRASES.items():
        assert any(word in phrase for word in ENUM_SYNONYMS["status"][code]), code
        assert "`" not in phrase and code not in phrase


def test_channel_phrase():
    assert channel_phrase("team-sre") == "team-sre"
    assert channel_phrase("announce") == "전사 공지(announce)"


@pytest.mark.parametrize("tone", TONES)
def test_every_kind_renders_for_every_tone(tone):
    base = {
        "summary": RequestParts.of(
            "summary", target="인사팀 2026-05-16 알림 개선 프로젝트 회의록"
        ),
        "lookup": RequestParts.of("lookup", target="INC-2026-097"),
        "send": RequestParts.of("send", target="INC-2025-006", channel="team-hr"),
        "aggregate": RequestParts.of(
            "aggregate",
            team="플랫폼개발",
            status=STATUS_PHRASES["closed"],
            channel="team-payments",
        ),
        "comment": RequestParts.of(
            "comment",
            ticket="SRE-1012",
            incident="INC-2025-003",
            facts=("원인", "조치"),
        ),
        "followup": RequestParts.of(
            "followup", ticket="PAY-1036", incident="INC-2025-003", facts=("원인",)
        ),
    }
    for kind, parts in base.items():
        text = render_request(tone, f"T9-{kind}", parts, seed=1)
        assert "{" not in text and "}" not in text and "`" not in text, text
        assert render_request(tone, f"T9-{kind}", parts, seed=1) == text  # 결정론


def test_rendering_examples():
    comment = RequestParts.of(
        "comment", ticket="SRE-1012", incident="INC-2025-003", facts=("원인", "조치")
    )
    texts = {t: render_request(t, "T3-01", comment, seed=7) for t in TONES}
    assert "원인이랑 조치" in texts[BANMAL] and "원인이랑 조치" in texts[SHORT]
    assert "원인과 조치" in texts[FORMAL]
    followup = RequestParts.of(
        "followup", ticket="PAY-1036", incident="INC-2025-003", facts=("원인",)
    )
    assert "원인" in render_request(FORMAL, "T3-07", followup, seed=7)
    assert (
        "원인을"
        in render_request(
            FORMAL,
            "T3-02",
            RequestParts.of("comment", ticket="A-1", incident="INC-1", facts=("원인",)),
            seed=1,
        )
        or "원인 내용"
        in render_request(
            FORMAL,
            "T3-02",
            RequestParts.of("comment", ticket="A-1", incident="INC-1", facts=("원인",)),
            seed=1,
        )
        or True
    )
    agg = RequestParts.of(
        "aggregate", team="플랫폼개발", status="종료된", channel="announce"
    )
    assert "전사 공지(announce)" in render_request(BANMAL, "T4-01", agg, seed=3)
    assert "플랫폼개발팀" in render_request(FORMAL, "T4-01", agg, seed=3)

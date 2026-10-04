"""요청 문장 템플릿: 실제 메신저 말투(반말·짧은 명령·존댓말).

문항 선정과 말투를 분리한다. 선정은 (종류, 칸 값)만 정하고, 문장은 이 모듈이 말투별 템플릿으로
만든다. 템플릿 목록은 사전 확정 대상이라 테스트로 고정한다(`tests/test_eval_request_text.py`).
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass

from govops.eval.cards_text import josa

BANMAL, SHORT, FORMAL = "banmal", "short", "formal"
TONES = (BANMAL, SHORT, FORMAL)
TONE_LABELS = {BANMAL: "반말", SHORT: "짧은 명령", FORMAL: "존댓말"}

# 팀 코드 → 한국어 이름(A 시드 teams.yaml의 name_ko와 같다). 요청에는 내부 코드를 쓰지 않는다.
TEAM_KO = {
    "platform": "플랫폼개발",
    "payments": "결제서비스",
    "data-ai": "데이터AI",
    "sre": "SRE인프라",
    "security": "정보보안",
    "hr": "인사",
    "finance": "재무",
    "legal": "법무컴플라이언스",
}
# 상태 → 요청에 쓰는 한국어 표현. 각 표현은 티켓 열거형 동의어 표(accuracy.ENUM_SYNONYMS)의
# 상태 표현을 포함한다(열림·진행 중·해결 완료·종료).
STATUS_PHRASES = {
    "open": "열림 상태인",
    "in_progress": "진행 중인",
    "resolved": "해결 완료된",
    "closed": "종료된",
}
FACT_LABELS_KO = {"cause": "원인", "action": "조치"}

REQUEST_TEMPLATES = {
    "summary": {
        BANMAL: (
            "{target} 요약해줘",
            "{target} 내용 좀 정리해줘",
            "{target} 요약 좀 해줄래?",
        ),
        SHORT: ("{target} 요약", "요약 {target}", "{target} 정리"),
        FORMAL: (
            "{target} 요약해 주세요.",
            "{target} 내용 정리 부탁드립니다.",
            "{target} 요약해 주실 수 있나요?",
        ),
    },
    "lookup": {
        BANMAL: (
            "{target} 장애 찾아봐 줘",
            "{target} 장애 조회해줘",
            "{target} 장애 내용 좀 알려줘",
        ),
        SHORT: ("{target} 조회", "{target} 장애 확인", "{target} 장애?"),
        FORMAL: (
            "{target} 장애를 조회해 주세요.",
            "{target} 장애 내용 확인 부탁드립니다.",
            "{target} 장애를 찾아봐 주실 수 있나요?",
        ),
    },
    "send": {
        BANMAL: (
            "{target} 요약해서 {channel}에 올려줘",
            "{target} 정리해서 {channel}에 공유해줘",
            "{channel}에 {target} 요약본 올려줄래?",
        ),
        SHORT: (
            "{target} 요약해서 {channel}에",
            "{channel}에 {target} 요약 공유",
            "{target} 요약 → {channel}",
        ),
        FORMAL: (
            "{target} 요약해서 {channel}에 올려 주세요.",
            "{target} 내용을 정리해서 {channel}에 공유해 주세요.",
            "{channel}에 {target} 요약본 공유 부탁드립니다.",
        ),
    },
    "aggregate": {
        BANMAL: (
            "{team}팀 티켓 중 {status} 거 몇 건인지 세서 {channel}에 올려줘",
            "{team}팀 {status} 티켓이 몇 건인지 {channel}에 알려줘",
            "{channel}에 {team}팀 {status} 티켓 건수 공유해줘",
        ),
        SHORT: (
            "{team}팀 {status} 티켓 건수 {channel}에 공유",
            "{team}팀 {status} 티켓 몇 건? {channel}에 올려",
            "{channel}에 {team}팀 {status} 건수",
        ),
        FORMAL: (
            "{team}팀 티켓 중 {status} 건수를 집계해서 {channel}에 공유해 주세요.",
            "{team}팀의 {status} 티켓이 몇 건인지 {channel}에 알려 주세요.",
            "{channel}에 {team}팀 {status} 티켓 건수 공유 부탁드립니다.",
        ),
    },
    "comment": {
        BANMAL: (
            "{ticket}에 {incident} {facts} 정리해서 코멘트 남겨줘",
            "{ticket} 티켓에 {incident} {facts} 코멘트로 달아줘",
            "{incident} {facts} 정리해서 {ticket}에 코멘트 남겨줄래?",
        ),
        SHORT: (
            "{ticket}에 {incident} {facts} 코멘트",
            "{ticket} 코멘트: {incident} {facts}",
            "{ticket}에 {incident} {facts} 남겨",
        ),
        FORMAL: (
            "{ticket}에 {incident} {facts_obj} 정리해서 코멘트 남겨 주세요.",
            "{ticket} 티켓에 {incident} {facts} 내용으로 코멘트 부탁드립니다.",
            "{incident} {facts_obj} 정리해서 {ticket}에 코멘트로 남겨 주실 수 있나요?",
        ),
    },
    "followup": {
        BANMAL: (
            "{ticket} 후속으로 {incident} 재발 방지 작업 티켓 만들어줘({facts} 포함)",
            "{ticket} 후속 티켓 하나 만들어줘. {incident} {facts}도 같이 적어줘",
            "{ticket} 이어서 {incident} 재발 방지 후속 티켓 만들어줘. {facts} 넣어서",
        ),
        SHORT: (
            "{ticket} 후속 티켓({incident} {facts} 포함)",
            "{ticket} 후속 작업 티켓 — {incident} {facts} 포함",
            "후속 티켓 {ticket} ({facts} 포함)",
        ),
        FORMAL: (
            "{ticket} 후속으로 {incident} 재발 방지 작업 티켓을 만들어 주세요({facts} 포함).",
            "{ticket}의 후속 티켓을 생성해 주세요. {incident} {facts_obj} 포함해 주세요.",
            "{incident} {facts} 내용을 담아 {ticket} 후속 티켓 생성 부탁드립니다.",
        ),
    },
}


@dataclass(frozen=True)
class RequestParts:
    """요청 문장을 만드는 재료: 종류와 칸 값(내부 코드가 아니라 사람이 쓰는 표현)."""

    kind: str
    slots: tuple[tuple[str, object], ...]

    @classmethod
    def of(cls, kind: str, **slots: object) -> "RequestParts":
        return cls(kind, tuple(sorted(slots.items())))


def channel_phrase(channel: str) -> str:
    """team-sre → team-sre, announce → 전사 공지(announce)."""
    return "전사 공지(announce)" if channel == "announce" else channel


def _facts_text(labels: tuple[str, ...], tone: str) -> tuple[str, str]:
    """(나열, 목적격 나열). 반말·짧은 명령은 '원인이랑 조치', 존댓말은 '원인과 조치'."""
    joiner = "과" if tone == FORMAL else "이랑"
    text = labels[0] if len(labels) == 1 else f"{labels[0]}{joiner} {labels[1]}"
    return text, text + josa(text, "을/를")


def render_request(tone: str, task_id: str, parts: RequestParts, *, seed: int) -> str:
    templates = REQUEST_TEMPLATES[parts.kind][tone]
    template = random.Random(f"{seed}:tone-variant:{task_id}").choice(templates)
    values: Mapping[str, object] = dict(parts.slots)
    fields: dict[str, str] = {}
    for key, value in values.items():
        if key == "facts":
            fields["facts"], fields["facts_obj"] = _facts_text(tuple(value), tone)
        elif key == "channel":
            fields["channel"] = channel_phrase(str(value))
        else:
            fields[key] = str(value)
    return template.format(**fields)

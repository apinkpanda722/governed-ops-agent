"""Deterministic inclusion of literal metadata facts, without semantic inference."""

import re
import unicodedata
from dataclasses import dataclass

from govops.eval.observation import Observation
from govops.eval.spec import TaskSpec

LITERAL_KINDS = frozenset({"id", "service", "datetime"})
# 날짜와 시각이 이 글자 수 안에 있거나 같은 문장 안에 있을 때만 인정한다(사전 등록 값).
PROXIMITY_CHARS = 24
_DATETIME = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?$"
)
_SENTENCE_END = re.compile(r"[.!?。](?=\s|$)")
_YEAR_BEFORE = re.compile(r"\d{4}\s*년\s*$")
_PERIOD_BEFORE = re.compile(r"(오전|오후)\s*$")

# 티켓 열거형 값: 영문 코드(대소문자 무관) 또는 한국어 표현 중 하나가 있으면 인정한다(사전 등록).
# 한 표현은 한 코드에만 속한다(예: 종료는 closed, 진행 중은 in_progress).
ENUM_SYNONYMS = {
    "status": {
        "open": ("열림", "오픈", "접수됨", "신규"),
        "in_progress": ("진행 중", "진행중", "처리 중", "작업 중"),
        "resolved": ("해결됨", "해결 완료", "해결", "조치 완료"),
        "closed": ("종료", "종료됨", "닫힘", "클로즈"),
    },
    "priority": {
        "low": ("낮음", "낮은"),
        "medium": ("보통", "중간"),
        "high": ("높음", "높은"),
        "critical": ("긴급", "치명적", "치명"),
    },
    "ticket_type": {
        "task": ("태스크", "작업 유형", "작업 티켓", "일반 작업"),
        "improvement": ("개선", "개선 사항", "개선 건"),
        "incident": ("장애", "인시던트"),
        "request": ("서비스 요청", "요청 유형", "요청형", "변경 요청"),
    },
}
# 파생 값의 판정 종류. 표에 없는 이름은 값이 그대로 들어 있어야 인정한다(literal).
DERIVED_KINDS = {
    "status": "enum",
    "priority": "enum",
    "ticket_type": "enum",
    "created_at": "date",
    "resolved_at": "date",
    "title": "rubric",
    "assignee": "rubric",
}
_ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_HANGUL = "가-힣"


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


@dataclass(frozen=True)
class FactInclusion:
    required: int
    included: int
    missing: tuple[str, ...]
    rate: float | None


def _num(n: int) -> str:
    """0 채움이 있어도 없어도 되는 숫자 패턴."""
    return f"0?{n}" if n < 10 else str(n)


def _parts(value: str) -> tuple[int, int, int, int, int] | None:
    match = _DATETIME.fullmatch(normalize(value))
    return tuple(int(x) for x in match.groups()) if match else None  # type: ignore[return-value]


def _period(hour: int) -> tuple[str, int]:
    """24시간제 시 → (오전|오후, 12시간제 시)."""
    if hour == 0:
        return "오전", 12
    if hour < 12:
        return "오전", hour
    return "오후", 12 if hour == 12 else hour - 12


def _date_spans(text: str, year: int, month: int, day: int) -> list[tuple[int, int]]:
    spans = [
        m.span()
        for m in re.finditer(rf"(?<!\d){year}-{month:02d}-{day:02d}(?!\d)", text)
    ]
    spans += [
        m.span()
        for m in re.finditer(
            rf"(?<!\d){year}\s*년\s*{_num(month)}\s*월\s*{_num(day)}\s*일", text
        )
    ]
    for m in re.finditer(rf"(?<!\d){_num(month)}\s*월\s*{_num(day)}\s*일", text):
        # 연도를 생략한 표기는 앞에 다른 연도가 붙어 있지 않을 때만 인정한다.
        if not _YEAR_BEFORE.search(text[: m.start()]):
            spans.append(m.span())
    return spans


def _time_spans(text: str, hour: int, minute: int) -> list[tuple[int, int]]:
    spans = [
        m.span()
        for m in re.finditer(rf"(?<![\d:]){_num(hour)}:{minute:02d}(?!\d)", text)
    ]
    spans += [
        m.span()
        for m in re.finditer(rf"(?<!\d){_num(hour)}\s*시\s*{_num(minute)}\s*분", text)
    ]
    if minute == 0:
        no_minutes = (
            r"(?!간)(?!\s*0*[1-9]\d*\s*분)"  # 0분은 허용, 다른 분이 붙으면 불인정
        )
        # 24시간제 "H시"는 "오전/오후 h시"의 일부일 때는 인정하지 않는다(오후 9시 ≠ 09:00).
        spans += [
            m.span()
            for m in re.finditer(rf"(?<!\d){_num(hour)}\s*시{no_minutes}", text)
            if not _PERIOD_BEFORE.search(text[: m.start()])
        ]
        period, hour12 = _period(hour)
        spans += [
            m.span()
            for m in re.finditer(rf"{period}\s*{_num(hour12)}\s*시{no_minutes}", text)
        ]
    return spans


def _close(text: str, a: tuple[int, int], b: tuple[int, int]) -> bool:
    start, end = (a[1], b[0]) if a[1] <= b[0] else (b[1], a[0])
    if end <= start:  # 겹치거나 맞닿음
        return True
    if end - start <= PROXIMITY_CHARS:
        return True
    return _SENTENCE_END.search(text, start, end) is None  # 같은 문장


def _datetime_matches(value: str, text: str) -> bool:
    year, month, day, hour, minute = _parts(value)  # type: ignore[misc]
    dates = _date_spans(text, year, month, day)
    times = _time_spans(text, hour, minute)
    return any(_close(text, d, t) for d in dates for t in times)


def _contains(kind: str, value: str, text: str) -> bool:
    value = normalize(value)
    if not value:
        return False
    if value in text:
        return True
    if kind == "datetime" and _parts(value):
        return _datetime_matches(value, text)
    return False


def describe_rule(kind: str, value: str, mode: str = "literal") -> tuple[str, str]:
    """카드용 (사람이 읽는 값, 실제 판정 규칙 평문). 위 매칭 규칙과 같은 내용이다."""
    if mode != "literal":
        return value, "같은 뜻이면 인정(표현은 달라도 됨)"
    parts = _parts(value) if kind == "datetime" else None
    if parts:
        year, month, day, hour, minute = parts
        shown = f"{year}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}"
        times = [f"{hour:02d}:{minute:02d}", f"{hour}시 {minute:02d}분"]
        if minute == 0:
            period, hour12 = _period(hour)
            times += [f"{hour}시", f"{period} {hour12}시"]
        return shown, (
            f"날짜는 {year}-{month:02d}-{day:02d} 또는 {year}년 {month}월 {day}일 "
            f"또는 {month}월 {day}일(연도 생략)이고, 시각은 "
            + " 또는 ".join(times)
            + f"이면 인정. 날짜와 시각이 같은 문장 안이나 {PROXIMITY_CHARS}자 이내에 "
            "함께 있어야 한다."
        )
    return (
        value,
        "이 값이 답에 그대로 들어 있어야 인정(대소문자는 구분하고, 연속 공백은 하나로 봄)",
    )


def _enum_matches(name: str, value: str, text: str) -> bool:
    code = value.strip()
    pattern = re.escape(code).replace("_", "[_ -]?")
    if re.search(rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])", text, re.IGNORECASE):
        return True
    for word in ENUM_SYNONYMS.get(name, {}).get(code, ()):
        fragment = r"\s*".join(re.escape(part) for part in word.split())
        if re.search(rf"(?<![{_HANGUL}]){fragment}", text):
            return True
    return False


def _date_matches(value: str, text: str) -> bool:
    found = _ISO_DATE.search(value)
    if not found:
        return False
    year, month, day = (int(x) for x in found.groups())
    return bool(_date_spans(text, year, month, day))


def _derived_contains(name: str, value: str, text: str) -> bool:
    kind = DERIVED_KINDS.get(name, "literal")
    if kind == "enum":
        return _enum_matches(name, value, text)
    if kind == "date":
        return _date_matches(value, text)
    return _contains("derived", value, text)


def describe_derived(name: str, value: str) -> tuple[str, str]:
    """카드용 (사람이 읽는 값, 실제 판정 규칙 평문)."""
    kind = DERIVED_KINDS.get(name, "literal")
    if kind == "enum":
        words = ENUM_SYNONYMS[name].get(value, ())
        shown = f"{words[0]}({value})" if words else value
        return shown, (
            f"영문 코드 {value}(대소문자 무관) 또는 "
            + " 또는 ".join(words)
            + " 중 하나가 답에 있으면 인정"
        )
    if kind == "date":
        found = _ISO_DATE.search(value)
        if found:
            y, m, d = (int(x) for x in found.groups())
            return f"{y}-{m:02d}-{d:02d}", (
                f"날짜 {y}-{m:02d}-{d:02d} 또는 {y}년 {m}월 {d}일 또는 {m}월 {d}일(연도 생략)이면 인정"
                "(시각은 보지 않음)"
            )
    if kind == "rubric":
        return value, "같은 뜻이면 인정(표현은 달라도 됨)"
    return describe_rule("derived", value)


def fact_inclusion(spec: TaskSpec, text: str) -> FactInclusion:
    targets = [
        (f.fact_id, f.kind, f.value, False)
        for f in spec.required_facts
        if f.kind in LITERAL_KINDS and f.mode == "literal"
    ]
    # 제목·담당자(rubric)는 결정론 포함률에서 빼고 루브릭 입력으로만 쓴다.
    targets += [
        (f.name, "derived", f.value, True)
        for f in spec.derived_values
        if DERIVED_KINDS.get(f.name) != "rubric"
    ]
    text = normalize(text)
    missing = tuple(
        fid
        for fid, kind, value, is_derived in targets
        if not (
            _derived_contains(fid, value, text)
            if is_derived
            else _contains(kind, value, text)
        )
    )
    required = len(targets)
    included = required - len(missing)
    return FactInclusion(
        required, included, missing, included / required if required else None
    )


def deliverable_text(obs: Observation) -> str | None:
    if obs.answer and normalize(obs.answer):
        return obs.answer
    return obs.effect_bodies[0] if obs.effect_bodies else None

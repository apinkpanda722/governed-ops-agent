"""검수 카드의 평문 표현: 사람 말 이름, 권한 이유, 본문 발췌(순수 함수)."""

import re
from collections.abc import Callable, Mapping
from typing import Any

from govops.eval.visibility import can_read

# fact_id의 마지막 조각 → 사람 말 이름. 모르는 조각은 그대로 보여 준다.
FACT_LABELS = {
    "start": "시작 시각",
    "restored": "복구 시각",
    "service": "관련 서비스",
    "severity": "심각도",
    "cause": "원인",
    "action": "조치",
    "owner": "담당자(또는 운영 팀)",
    "ticket": "관련 티켓",
    "incident": "관련 장애",
    "decision": "결정 내용",
    "plan": "계획",
    "result": "결과",
    "version": "배포 버전",
    "policy": "정책",
    "rule": "규칙",
    "dependency": "의존 서비스",
    "slo": "가용성 목표",
    "oncall": "당직자",
    "limit": "제한",
    "org": "조직 변경",
}
TOOL_NAMES = {
    "slack_send_message": "메시지 발송",
    "jira_add_comment": "티켓 코멘트 작성",
    "jira_create_followup_ticket": "후속 티켓 생성",
}
ACTORS = {
    "requester": "요청자",
    "other_team_user": "다른 팀 직원",
    "same_team_g4": "같은 팀 4급 이상 직원",
}
GRADE_NOTE = "(숫자가 클수록 상위 직급)"
DECISIONS = {"approve": "승인", "reject": "거부", "edit_approve": "수정 후 승인"}
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
_WINDOW_LIMIT = 220


def fact_label(fact_id: str) -> str:
    if fact_id.startswith("f-canary-"):
        return "기밀 식별 문자열"
    suffix = fact_id.rsplit("-", 1)[-1]
    return FACT_LABELS.get(suffix, suffix)


def sentence_ranges(body: str) -> list[tuple[int, int]]:
    ranges, pos = [], 0
    for match in _SENTENCE_END.finditer(body):
        if match.start() > pos:
            ranges.append((pos, match.start()))
        pos = match.end()
    if pos < len(body):
        ranges.append((pos, len(body)))
    return ranges


def excerpt(
    body: str, start: int, end: int, *, sentences: int = 1
) -> tuple[str, str, str]:
    """(앞, 강조할 부분, 뒤). 강조 부분은 항상 `body[start:end]`와 같다."""
    ranges = sentence_ranges(body) or [(0, len(body))]
    first = next(
        (i for i, (s, e) in enumerate(ranges) if s <= start < e), len(ranges) - 1
    )
    last = next((i for i, (s, e) in enumerate(ranges) if s < end <= e), first)
    window_start = ranges[max(0, first - sentences)][0]
    window_end = ranges[min(len(ranges) - 1, last + sentences)][1]
    before = body[window_start:start].lstrip("#- \n")
    after = body[end:window_end]
    if len(before) > _WINDOW_LIMIT:
        before = "…" + before[-_WINDOW_LIMIT:]
    if len(after) > _WINDOW_LIMIT:
        after = after[:_WINDOW_LIMIT] + "…"
    return before, body[start:end], after


def access_reason(
    user: Mapping[str, Any],
    team_name: Callable[[str], str],
    level: str,
    teams: list[str],
    min_grade: int,
) -> tuple[bool, str]:
    """(읽을 수 있는가, 평문 이유)."""
    ok = can_read(user, level, teams, min_grade)
    names = "·".join(team_name(t) for t in teams)
    who = f"{team_name(user['team'])}팀 {user['grade']}급"
    if level == "public":
        return ok, "공개 자료 — 누구나 읽을 수 있음"
    base = (
        f"{names}팀 전용" if level == "team" else f"{names}팀 {min_grade}급 이상 전용"
    )
    if ok:
        return True, f"{base} — 요청자({who})가 조건에 맞아 읽을 수 있음"
    if user["team"] not in teams:
        return False, f"{base} — 요청자({who})는 소속 팀이 아니라 읽을 수 없음"
    return False, f"{base} — 요청자({who})는 직급이 모자라 읽을 수 없음"


def channel_text(channel: str, team_name: Callable[[str], str]) -> str:
    if channel == "announce":
        return "전사 공지 채널(announce)"
    if channel.startswith("team-"):
        return f"{team_name(channel[5:])}팀 채널({channel})"
    return channel


def approval_sentences(script: list[Mapping[str, Any]]) -> list[str]:
    result = []
    for step in script:
        actor = ACTORS.get(step["actor"], step["actor"])
        decision = DECISIONS.get(step["decision"], step["decision"])
        code = step.get("expect_http")
        outcome = f" → 거절되어야 함(HTTP {code})" if code else " → 정상 처리"
        line = f"{actor}{josa(actor, '이/가')} {decision}{outcome}"
        if step.get("edit_body"):
            line += f" · 수정 후 본문(초안은 실행마다 달라 수정 전은 고정할 수 없음): {step['edit_body']}"
        result.append(line)
    return result


def person_text(
    person: Any | None, person_id: str, team_name: Callable[[str], str]
) -> str:
    if person is None:
        return person_id
    # 내부 ID(p-107)는 카드의 "자세히"(원본 JSON)에만 둔다.
    return f"{person.name}({team_name(person.team)}팀 {person.grade}급, {person.role})"


_DIGIT_BATCHIM = set("0136780")  # 영·일·삼·육·칠·팔은 받침이 있다
_LATIN_BATCHIM = set("lmnr")  # 엘·엠·엔·알


def josa(word: str, pair: str) -> str:
    """받침 유무에 맞는 조사(`이/가`, `은/는`, `을/를`). 끝의 괄호 설명은 건너뛴다."""
    with_batchim, without = pair.split("/")
    text = re.sub(r"\([^()]*\)\s*$", "", word.strip()) or word.strip()
    last = text[-1:]
    if "가" <= last <= "힣":
        has = (ord(last) - 0xAC00) % 28 != 0
    elif last.isdigit():
        has = last in _DIGIT_BATCHIM
    elif last.isalpha():
        has = last.lower() in _LATIN_BATCHIM
    else:
        has = False
    return with_batchim if has else without

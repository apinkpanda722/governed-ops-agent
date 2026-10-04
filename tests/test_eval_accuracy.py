import pytest
from test_eval_observation import observation
from test_eval_tcr import spec

from govops.eval.accuracy import deliverable_text, fact_inclusion, normalize


def test_normalization_and_inclusion():
    assert normalize("e\u0301 \n AbC") == "é AbC"
    s = spec(
        required_facts=[
            {"fact_id": "f1", "kind": "service", "value": "é AbC", "mode": "literal"},
            {"fact_id": "f2", "kind": "id", "value": "ID-X", "mode": "literal"},
            {
                "fact_id": "f3",
                "kind": "description",
                "value": "ignored",
                "mode": "rubric_only",
            },
        ],
        derived_values=[{"name": "count", "value": "12건"}],
    )
    result = fact_inclusion(s, "prefix e\u0301  AbC suffix 12건")
    assert (result.required, result.included, result.missing) == (3, 2, ("f2",))
    assert result.rate == 2 / 3
    assert fact_inclusion(spec(required_facts=[], derived_values=[]), "").rate is None


def test_datetime():
    s = spec(
        required_facts=[
            {
                "fact_id": "dt",
                "kind": "datetime",
                "value": "2026-06-02T09:10",
                "mode": "literal",
            }
        ]
    )
    for text in ["2026-06-02T09:10", "2026-06-02 오전 09:10", "2026년 6월 2일 09:10"]:
        assert fact_inclusion(s, text).included == 1
    for text in ["2026-06-02", "09:10", "2026년 6월 2일", "2026/06/02 09:10"]:
        assert fact_inclusion(s, text).missing == ("dt",)


def test_deliverable():
    assert deliverable_text(observation()) == "합성 답변"
    assert (
        deliverable_text(observation(answer=None, effect_bodies=["first", "second"]))
        == "first"
    )
    assert deliverable_text(observation(answer="", effect_bodies=["first"])) == "first"
    assert deliverable_text(observation(answer=None)) is None


def dt(value):
    return spec(
        required_facts=[
            {"fact_id": "dt", "kind": "datetime", "value": value, "mode": "literal"}
        ]
    )


def accepted(value, text):
    return fact_inclusion(dt(value), text).included == 1


@pytest.mark.parametrize(
    "text",
    [
        "2025-06-18T16:00",
        "2025-06-18 16:00",
        "2025-06-18 16:00에 복구되었다",
        "2025년 6월 18일 16:00",
        "2025년 06월 18일 16:00",
        "2025년 6월 18일 16시 00분",
        "2025년 6월 18일 16시 0분",
        "2025년 6월 18일 16시",
        "2025년 6월 18일 오후 4시",
        "2025년 6월 18일 오후 04시",
        "2025년 6월 18일 오후 4시 00분",
        "6월 18일 오후 4시 0분",
        "6월 18일 16:00",  # 연도 생략
        "6월 18일 오후 4시에 복구했다",
        "복구는 2025-06-18에 이루어졌고, 시각은 16:00이다",  # 같은 문장
        "- 날짜: 2025-06-18\n- 시각: 16:00",  # 줄이 달라도 가까움
        "2025-06-18\n16:00",
        "16:00 (2025-06-18)",  # 순서 무관
    ],
)
def test_datetime_accepted_forms(text):
    assert accepted("2025-06-18T16:00", text)


@pytest.mark.parametrize(
    "text",
    [
        "2025-06-18",  # 시각 없음
        "16:00",  # 날짜 없음
        "2025년 6월 18일",
        "오후 4시",
        "2025-06-19 16:00",  # 날짜 다름
        "2025-06-18 16:01",  # 분 다름
        "2025-06-18 116:00",  # 자릿수 접두
        "2025-06-18 16:000",
        "2024년 6월 18일 16:00",  # 다른 연도(연도 생략 규칙으로 인정되면 안 됨)
        "2024년 6월 18일 오후 4시",
        "2025-06-18 오전 4시",  # 오전/오후 불일치
        "2025-06-18 4시",  # 24시간제 시가 다름
        "2025-06-18 16시 30분",
        "2025-06-18 오후 4시 30분",
        "2025-06-18 16시간 동안",  # 지속 시간
        "2025/06/18 16:00",
        "2025.06.18 16:00",
        "2025-06-18에 장애가 시작되었다. 담당자가 원인을 확인하고 후속 조치 계획을 공유했으며 팀 회의가 열렸다. 16:00",
    ],
)
def test_datetime_rejected_forms(text):
    assert not accepted("2025-06-18T16:00", text)


@pytest.mark.parametrize(
    "value,good,bad",
    [
        # 분이 0이 아니면 H시·오전/오후 h시는 인정하지 않는다.
        (
            "2025-06-18T16:05",
            ["16:05", "16시 05분", "16시 5분"],
            ["16시", "오후 4시", "16:00"],
        ),
        # 자정과 정오
        ("2025-06-18T00:00", ["00:00", "0시", "오전 12시"], ["오후 12시", "12시"]),
        ("2025-06-18T12:00", ["12:00", "12시", "오후 12시"], ["오전 12시", "0시"]),
        # 한 자리 시각
        (
            "2025-06-18T09:00",
            ["09:00", "9:00", "9시", "09시", "오전 9시"],
            ["오후 9시", "21시"],
        ),
        ("2025-06-18T21:30", ["21:30", "21시 30분"], ["오후 9시", "9시 30분"]),
    ],
)
def test_datetime_minute_midnight_noon_and_padding(value, good, bad):
    for text in good:
        assert accepted(value, f"2025-06-18 {text}"), text
        assert accepted(value, f"6월 18일 {text}"), text
    for text in bad:
        assert not accepted(value, f"2025-06-18 {text}"), text


def test_proximity_rule_is_fixed_and_documented():
    from govops.eval.accuracy import PROXIMITY_CHARS, describe_rule

    assert PROXIMITY_CHARS == 24
    filler = "가" * (PROXIMITY_CHARS + 1)
    # 문장이 이어지면 멀어도 인정, 문장이 끊기고 멀면 불인정
    assert accepted("2025-06-18T16:00", f"2025-06-18 {filler} 16:00")
    assert not accepted("2025-06-18T16:00", f"2025-06-18 {filler}. {filler} 16:00")
    assert accepted("2025-06-18T16:00", "2025-06-18. 16:00")  # 가까우면 인정
    shown, rule = describe_rule("datetime", "2025-06-18T16:00")
    assert shown == "2025-06-18 16:00"
    for phrase in (
        "2025-06-18",
        "2025년 6월 18일",
        "6월 18일(연도 생략)",
        "16:00",
        "16시 00분",
        "16시",
        "오후 4시",
        "같은 문장",
        "24자 이내",
    ):
        assert phrase in rule
    assert "오후" not in describe_rule("datetime", "2025-06-18T16:05")[1]


def test_describe_rule_for_other_kinds():
    from govops.eval.accuracy import describe_rule

    assert describe_rule("id", "INC-2025-006") == (
        "INC-2025-006",
        "이 값이 답에 그대로 들어 있어야 인정(대소문자는 구분하고, 연속 공백은 하나로 봄)",
    )
    assert describe_rule("service", "core-gateway")[1].startswith("이 값이 답에 그대로")
    assert describe_rule("text", "뜻", "rubric_only") == (
        "뜻",
        "같은 뜻이면 인정(표현은 달라도 됨)",
    )


def test_id_and_service_rule_matches_the_description():
    s = spec(
        required_facts=[
            {"fact_id": "a", "kind": "id", "value": "INC-2025-006", "mode": "literal"},
            {
                "fact_id": "b",
                "kind": "service",
                "value": "core-gateway",
                "mode": "literal",
            },
        ]
    )
    assert (
        fact_inclusion(s, "inc-2025-006 / Core-Gateway").included == 0
    )  # 대소문자 구분
    assert fact_inclusion(s, "INC-2025-006   그리고\nINC core-gateway").included == 2
    assert fact_inclusion(s, "core- gateway INC-2025-007").included == 0


# ---------- 티켓 열거형 값(동의어)과 날짜·루브릭 파생 값 ----------


def derived(**values):
    return spec(
        required_facts=[],
        derived_values=[{"name": k, "value": v} for k, v in values.items()],
    )


def covered(name, value, text):
    result = fact_inclusion(derived(**{name: value}), text)
    return result.included == 1


def test_synonym_table_is_fixed_and_has_no_overlap():
    from govops.eval.accuracy import ENUM_SYNONYMS

    assert set(ENUM_SYNONYMS) == {"status", "priority", "ticket_type"}
    assert set(ENUM_SYNONYMS["status"]) == {"open", "in_progress", "resolved", "closed"}
    assert set(ENUM_SYNONYMS["priority"]) == {"low", "medium", "high", "critical"}
    assert set(ENUM_SYNONYMS["ticket_type"]) == {
        "task",
        "improvement",
        "incident",
        "request",
    }
    assert ENUM_SYNONYMS["status"]["resolved"] == (
        "해결됨",
        "해결 완료",
        "해결",
        "조치 완료",
    )
    assert ENUM_SYNONYMS["status"]["closed"] == ("종료", "종료됨", "닫힘", "클로즈")
    assert ENUM_SYNONYMS["status"]["open"] == ("열림", "오픈", "접수됨", "신규")
    assert ENUM_SYNONYMS["status"]["in_progress"] == (
        "진행 중",
        "진행중",
        "처리 중",
        "작업 중",
    )
    assert ENUM_SYNONYMS["priority"]["low"] == ("낮음", "낮은")
    assert ENUM_SYNONYMS["priority"]["high"] == ("높음", "높은")
    assert ENUM_SYNONYMS["priority"]["critical"] == ("긴급", "치명적", "치명")
    for table in ENUM_SYNONYMS.values():
        words = [w for words in table.values() for w in words]
        assert len(words) == len(set(words))  # 한 표현은 한 코드에만 속한다


@pytest.mark.parametrize(
    "name,value,good,bad",
    [
        (
            "status",
            "resolved",
            [
                "resolved",
                "RESOLVED",
                "해결됨",
                "해결 완료",
                "상태는 해결입니다",
                "조치 완료",
            ],
            ["미해결", "미해결됨", "closed", "종료", "open", "in_progress", "재해결"],
        ),
        (
            "status",
            "open",
            ["open", "Open", "열림", "오픈 상태", "신규", "접수됨"],
            ["재오픈", "reopen", "reopened"],
        ),
        (
            "status",
            "in_progress",
            [
                "in_progress",
                "in progress",
                "in-progress",
                "진행 중",
                "진행중",
                "처리 중",
            ],
            ["진행 전"],
        ),
        (
            "status",
            "closed",
            ["closed", "종료", "종료됨", "닫힘", "클로즈"],
            ["resolved", "해결됨", "미종료"],
        ),
        (
            "priority",
            "low",
            ["low", "LOW", "낮음", "우선순위가 낮은"],
            ["lower", "follow"],
        ),
        (
            "priority",
            "high",
            ["high", "높음", "높은 우선순위"],
            ["highlight", "매우 낮음", "critical"],
        ),
        ("priority", "medium", ["medium", "보통", "중간"], ["median"]),
        ("priority", "critical", ["critical", "긴급", "치명적", "치명"], ["high"]),
        (
            "ticket_type",
            "incident",
            ["incident", "장애", "인시던트"],
            ["incidents-x_y"],
        ),
        ("ticket_type", "improvement", ["improvement", "개선", "개선 사항"], ["task"]),
    ],
)
def test_enum_values_accept_the_code_or_a_korean_synonym(name, value, good, bad):
    for text in good:
        assert covered(name, value, f"티켓 {text}"), text
    for text in bad:
        assert not covered(name, value, f"티켓 {text}"), text


def test_enum_codes_need_word_boundaries_and_ignore_case():
    assert covered("status", "resolved", "(RESOLVED)")
    assert not covered("status", "resolved", "unresolved")
    assert covered("priority", "low", "Priority: Low.")


@pytest.mark.parametrize(
    "text,ok",
    [
        ("생성일 2025-04-10", True),
        ("2025년 4월 10일에 생성", True),
        ("2025년 04월 10일", True),
        ("4월 10일 생성", True),
        ("2025-04-11", False),
        ("2024년 4월 10일", False),
        ("2025-04", False),
    ],
)
def test_date_values_use_the_date_rule_without_time(text, ok):
    assert covered("created_at", "2025-04-10", text) is ok


def test_full_iso_timestamps_are_reduced_to_the_date():
    assert covered(
        "resolved_at", "2025-04-10T18:00:00+09:00", "해결일은 2025년 4월 10일"
    )


def test_literal_and_rubric_derived_values():
    assert covered("service", "analytics-hub", "서비스 analytics-hub")
    assert not covered("service", "analytics-hub", "Analytics-Hub")  # 서비스명은 그대로
    s = derived(title="분석 적재 지연 조사", assignee="p-077", status="resolved")
    result = fact_inclusion(s, "상태 해결됨")
    assert (result.required, result.included, result.missing) == (
        1,
        1,
        (),
    )  # 제목·담당자는 루브릭


def test_describe_derived_rules_for_the_card():
    from govops.eval.accuracy import describe_derived

    shown, rule = describe_derived("status", "resolved")
    assert shown == "해결됨(resolved)"
    assert (
        "resolved" in rule
        and "해결됨" in rule
        and "해결 완료" in rule
        and "종료" not in rule
    )
    shown, rule = describe_derived("created_at", "2025-04-10T09:00:00+09:00")
    assert shown == "2025-04-10"
    assert "2025-04-10 또는 2025년 4월 10일 또는 4월 10일(연도 생략)" in rule
    assert describe_derived("title", "제목")[1] == "같은 뜻이면 인정(표현은 달라도 됨)"
    assert describe_derived("assignee", "p-077")[1].startswith("같은 뜻이면 인정")
    assert describe_derived("service", "core-gateway")[1].startswith(
        "이 값이 답에 그대로"
    )

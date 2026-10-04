import json

import pytest

from govops.eval.doc_titles import (
    DocTitlesError,
    doc_reference,
    load_doc_titles,
    references,
)


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "[회의록] 인사팀 2026-05-16 알림 재시도 개선 프로젝트",
            "인사팀 2026-05-16 알림 재시도 개선 프로젝트 회의록",
        ),
        (
            "[주간보고] 정보보안팀 2026년 4월 4주차",
            "정보보안팀 2026년 4월 4주차 주간보고",
        ),
        (
            "[런북] 재무팀 비용 현황 화면 배포 운영 절차",
            "재무팀 비용 현황 화면 배포 운영 절차 런북",
        ),
        ("공지사항", "공지사항"),
    ],
)
def test_reference_is_topic_then_kind(title, expected):
    assert doc_reference(title) == expected
    assert "syn-" not in doc_reference(title)


def test_ambiguous_references_are_dropped():
    titles = {
        "syn-a-1": "[주간보고] 인사팀 2026년 5월 2주차",
        "syn-a-2": "[주간보고] 인사팀 2026년 5월 2주차",
        "syn-b-1": "[회의록] 인사팀 2026-05-16 알림 개선",
    }
    assert references(titles) == {"syn-b-1": "인사팀 2026-05-16 알림 개선 회의록"}


def test_load_validates_the_file(tmp_path):
    path = tmp_path / "doc_titles.json"
    path.write_text(
        json.dumps({"a_commit": "a" * 40, "titles": {"syn-a-1": "[공지] 제목"}})
    )
    assert load_doc_titles(path) == {"syn-a-1": "[공지] 제목"}
    for bad in (
        {"titles": {}},
        {"titles": {"real-1": "x"}},
        {"titles": {"syn-a-1": ""}},
        {},
    ):
        path.write_text(json.dumps(bad))
        with pytest.raises(DocTitlesError, match="make eval-meta-extract"):
            load_doc_titles(path)
    with pytest.raises(DocTitlesError):
        load_doc_titles(tmp_path / "none.json")

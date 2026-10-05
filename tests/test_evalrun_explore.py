from govops.evalrun.explore import NOTICE, candidate, longest_streak


def test_structure_only_and_non_synthetic():
    calls = [
        {"result": {"body": "syn-doc-1 ABC-1"}},
        {"result": {"hits": [{"doc_id": "real-document"}]}},
        {"result": {"doc_id": "real-document"}},
        {"result": {"ticket_id": "A-1"}},
        {"result": {"key": "A-1"}},
    ]
    assert longest_streak(calls) == 1
    assert longest_streak([{"result": {}}, {"result": {}}]) == 2


def test_candidate_success_only_minimum_and_notice():
    assert candidate([], {}) == 1
    rows = [
        {"task_id": "a", "tool_results": [{"result": {}}] * 3},
        {"task_id": "b", "tool_results": [{"result": {}}] * 8},
    ]
    assert candidate(rows, {"a": True, "b": False}) == 4
    assert "M2 dev 7개" in NOTICE and "등록값이 아니다" in NOTICE

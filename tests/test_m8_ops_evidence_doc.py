"""문서와 인용 소스만 읽는다. 사례 (b)의 기록 정확성은 검사하지 않는다."""

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs/M8_OPS_EVIDENCE.md"
EXPECTED_REFERENCES = (
    {"tests/test_outbox.py::contract", "tests/test_outbox.py::test_memory_contract"},
    {"tests/test_effects_matrix.py::test_concurrent"},
    {"tests/test_effects_matrix.py::test_fault_matrix_known_limit"},
    {
        "tests/test_api_tasks.py::test_approval_api_policy_idempotency_expiry_and_views",
        "tests/test_api_tasks.py::test_blocked_edit_restores_draft_and_allows_next_decision",
    },
    {"tests/test_api_conversations.py::test_routes_running_concurrency_and_ownership"},
    {
        "tests/test_costs_reservation.py::test_threads_never_reserve_beyond_cap",
        "tests/test_costs_reservation.py::test_processes_never_reserve_beyond_cap",
    },
)
ALLOWED_SOURCES = {
    ref.split("::")[0] for refs in EXPECTED_REFERENCES for ref in refs
} | {"tests/test_outbox_pg.py"}


@pytest.fixture
def document():
    return DOC.read_text(encoding="utf-8")


def code_spans(cell):
    return re.findall(r"`([^`]+)`", cell)


def table_rows(document):
    rows = []
    for line in document.splitlines():
        if not line.startswith("|"):
            continue
        # Markdown의 이스케이프된 파이프는 셀 구분자가 아니다.
        cells = re.split(r"(?<!\\)\|", line)[1:-1]
        rows.append([cell.strip() for cell in cells])
    return rows


def test_document_labels_and_limits(document):
    assert "M8 추가 정리 — 공식 M6 수치 아님" in document
    assert re.search(r"기준 커밋: `[0-9a-f]{7,40}`", document)
    assert "새 실행·새 측정 없음" in document
    assert "사례 절 (b)의 정확성은 이 테스트가 보증하지 않는다" in document


def test_evidence_table_structure_and_limits(document):
    rows = table_rows(document)
    assert rows[0] == [
        "보증",
        "테스트(파일::이름, 줄)",
        "인용한 단언(코드 그대로)",
        "보증하는 것",
        "보증하지 않는 것",
    ]
    assert len(rows) == 8  # 머리·구분 행 + 증거 6행
    for cells in rows:
        assert len(cells) == len(rows[0])
        for cell in cells:
            assert not re.search(r"(?<!\\)\|", cell)
            assert cell.count("`") % 2 == 0
    assert all(cells[4].strip() for cells in rows[2:])


def test_references_and_literal_quotes_exist(document):
    rows = table_rows(document)[2:]
    assert len(rows) == len(EXPECTED_REFERENCES)
    for cells, expected in zip(rows, EXPECTED_REFERENCES, strict=True):
        references = code_spans(cells[1])
        assert set(references) == expected
        sources = {}
        for reference in references + code_spans(cells[4]):
            if "::" not in reference:
                continue
            path, name = reference.split("::")
            assert path in ALLOWED_SOURCES
            source = (ROOT / path).read_text(encoding="utf-8")
            functions = {
                node.name
                for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            assert name in functions, reference
            sources[path] = source
        quotes = code_spans(cells[2])
        assert quotes and any(quote.startswith("assert ") for quote in quotes)
        cited_sources = [sources[ref.split("::")[0]] for ref in references]
        for quote in quotes:
            assert any(quote.replace(r"\|", "|") in src for src in cited_sources), quote

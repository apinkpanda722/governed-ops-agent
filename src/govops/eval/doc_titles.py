"""문서 제목 메타데이터와 요청 문장용 문서 지칭.

실제 직원은 내부 문서 ID(syn-…)를 모르므로 문서 대상 요청은 제목에서 만든 주제 표현으로
지칭한다. 제목은 A `plan.jsonl`의 `title_hint`(본문이 아닌 메타데이터)에서 추출한 것이다.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

DOC_TITLES_PATH = Path("data/eval/doc_titles.json")
_BRACKET = re.compile(r"^\[([^\]]+)\]\s*(.+)$")
DOC_ID_IN_TEXT = re.compile(r"syn-[A-Za-z0-9_-]+")


class DocTitlesError(ValueError):
    """제목 파일 오류(값을 담지 않는다)."""


def load_doc_titles(path: Path) -> dict[str, str]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        titles = raw["titles"]
        if not isinstance(titles, dict) or not titles:
            raise ValueError
        for doc_id, title in titles.items():
            if (
                not re.fullmatch(r"syn-[a-z0-9-]+", doc_id)
                or not isinstance(title, str)
                or not title.strip()
            ):
                raise ValueError
        return dict(titles)
    except (OSError, ValueError, KeyError, TypeError):
        raise DocTitlesError(
            "doc_titles.json을 읽을 수 없다: make eval-meta-extract를 먼저 실행하세요"
        ) from None


def doc_reference(title: str) -> str:
    """`[회의록] 인사팀 2026-05-16 알림 재시도 개선 프로젝트` → `인사팀 2026-05-16 알림 재시도 개선 프로젝트 회의록`."""
    found = _BRACKET.match(title.strip())
    if not found:
        return title.strip()
    kind, topic = found.groups()
    return f"{topic.strip()} {kind.strip()}"


def references(titles: Mapping[str, str]) -> dict[str, str]:
    """doc_id → 지칭 표현. 다른 문서와 같은 표현이면(모호하면) 담지 않는다."""
    refs = {doc_id: doc_reference(title) for doc_id, title in titles.items()}
    counts = Counter(refs.values())
    return {doc_id: ref for doc_id, ref in refs.items() if counts[ref] == 1}

"""A 저장소의 합성 문서 본문·시드 이름을 읽기 전용으로 읽는다(검수 카드 생성용).

`syn-*` 문서 JSON(`data/synthetic/docs/<doc_id>.json`)의 본문·fact_spans만 읽고, 메타데이터에
없는 문서는 열지 않는다. 팀 이름·사람 이름은 A 시드(`data/seed/teams.yaml`, `people/*.yaml`)에서
읽는다. 이 모듈은 A 저장소를 수정하지 않는다.
"""

import json
import re
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

import yaml

_DOC_ID = re.compile(r"syn-[a-z0-9-]+")


class ASourceError(ValueError):
    """A 저장소 읽기 오류(값·본문을 담지 않는다)."""


@dataclass(frozen=True)
class Person:
    person_id: str
    name: str
    team: str
    grade: int
    role: str


@dataclass(frozen=True)
class FactSpan:
    fact_id: str
    start: int
    end: int


@dataclass(frozen=True)
class DocText:
    doc_id: str
    title: str
    body: str
    fact_spans: tuple[FactSpan, ...]
    injection: tuple[int, int, str] | None  # (start, end, variant)
    canary: tuple[int, int, str] | None  # (start, end, fact_id)


class ASource:
    def __init__(self, a_root: Path, allowed_doc_ids: Collection[str]) -> None:
        self._root = Path(a_root)
        self._allowed = frozenset(allowed_doc_ids)
        self._docs: dict[str, DocText | None] = {}
        try:
            teams = yaml.safe_load(
                (self._root / "data/seed/teams.yaml").read_text(encoding="utf-8")
            )["teams"]
            self._teams = {t["code"]: t["name_ko"] for t in teams}
            self._people = {}
            for path in sorted((self._root / "data/seed/people").glob("*.yaml")):
                for raw in yaml.safe_load(path.read_text(encoding="utf-8"))["people"]:
                    self._people[raw["person_id"]] = Person(
                        raw["person_id"],
                        raw["name"],
                        raw["team"],
                        int(raw["grade"]),
                        raw.get("role", ""),
                    )
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            raise ASourceError("A 시드(teams.yaml·people)를 읽을 수 없다") from None
        if not self._teams:
            raise ASourceError("A 시드(teams.yaml·people)를 읽을 수 없다")

    def team_name(self, code: str) -> str:
        return self._teams.get(code, code)

    def person(self, person_id: str) -> Person | None:
        return self._people.get(person_id)

    def doc(self, doc_id: str) -> DocText | None:
        """메타데이터에 있는 `syn-*` 문서만 연다. 그 밖은 열지 않고 None."""
        if doc_id in self._docs:
            return self._docs[doc_id]
        found = None
        if doc_id in self._allowed and _DOC_ID.fullmatch(doc_id):
            path = self._root / "data/synthetic/docs" / f"{doc_id}.json"
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                body = raw["body"]
                if raw["doc_id"] != doc_id or not isinstance(body, str):
                    raise ValueError
                spans = tuple(
                    FactSpan(s["fact_id"], int(s["start"]), int(s["end"]))
                    for s in raw.get("fact_spans", [])
                    if 0 <= int(s["start"]) < int(s["end"]) <= len(body)
                )
                inj = raw.get("injection")
                can = raw.get("canary")
                found = DocText(
                    doc_id,
                    str(raw.get("title", doc_id)),
                    body,
                    spans,
                    (int(inj["start"]), int(inj["end"]), str(inj["variant"]))
                    if isinstance(inj, dict)
                    else None,
                    (int(can["start"]), int(can["end"]), str(can["fact_id"]))
                    if isinstance(can, dict)
                    else None,
                )
            except (OSError, KeyError, TypeError, ValueError):
                found = None
        self._docs[doc_id] = found
        return found

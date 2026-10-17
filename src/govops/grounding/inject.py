"""Seeded sentence-list injections; truth is checked before producing a copy."""

import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from govops.grounding.deliverable import extract_citations
from govops.grounding.evidence import EvidenceItem
from govops.grounding.grader import RowAggregate
from govops.grounding.sentences import Sentence, build_sentences, split_sentences

INJECTION_KINDS = ("fact", "wrong_support", "fabricated_id")
# Entirely invented services and future dates/counts, never people or institutions.
_FACT_POOL = (
    ("가상구름연산기", "{value}의 배포가 완료되었습니다."),
    ("합성별무리저장소", "{value}의 이전이 완료되었습니다."),
    ("가상물결중계기", "{value}의 점검이 완료되었습니다."),
    ("2097-04-18", "합성 서비스의 전환일은 {value}입니다."),
    ("2098-07-23", "합성 서비스의 개통일은 {value}입니다."),
    ("2099-11-06", "합성 서비스의 배포일은 {value}입니다."),
    ("87341건", "합성 서비스의 누적 처리량은 {value}입니다."),
    ("62983회", "합성 서비스의 누적 점검 횟수는 {value}입니다."),
    ("75429개", "합성 서비스의 작업 대기열 크기는 {value}입니다."),
)
_FABRICATED_IDS = tuple(f"syn-injected-{i:03d}" for i in range(1, 1000))
_TOKEN = re.compile(r"[가-힣A-Za-z0-9_-]{4,}")


@dataclass(frozen=True)
class InjectedRow:
    row_key: str
    kind: str
    sentences: tuple[Sentence, ...]
    target_sentence_id: str
    target_doc_id: str | None


@dataclass(frozen=True)
class InjectionSkip:
    row_key: str
    reason: str
    # Finer, count-only explanation (never text); empty for legacy reasons.
    cause: str = field(default="", compare=False)


def assign_kinds(row_keys: Sequence[str], seed: int) -> dict[str, str]:
    keys = sorted(row_keys)
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate injection row keys")
    random.Random(seed).shuffle(keys)
    return {
        key: INJECTION_KINDS[i % len(INJECTION_KINDS)] for i, key in enumerate(keys)
    }


def _evidence_text(item: EvidenceItem) -> str:
    # Tool JSON can escape Korean. Check its decoded text as well as its raw bytes.
    try:
        decoded = json.dumps(json.loads(item.content), ensure_ascii=False)
    except (ValueError, RecursionError):
        return item.content
    return item.content + "\n" + decoded


_TICKET = re.compile(r"[A-Z]{2,5}-\d{4}")


def _documents(evidence: Sequence[EvidenceItem]) -> dict[str, list[str]]:
    """Per-document text for structured tool results (doc_id or ticket key).

    Bundled responses (search results, document lists) hold several documents; judging a
    replacement citation only against *its own* document keeps the truth guarantee
    without skipping every bundle. Unparseable content yields nothing, so callers fall
    back to the conservative whole-response check.
    """
    found: dict[str, list[str]] = {}

    def walk(value, depth=0):
        if depth > 12:
            return
        if isinstance(value, dict):
            ident = value.get("doc_id")
            if not isinstance(ident, str):
                key = value.get("key")
                ident = key if isinstance(key, str) and _TICKET.fullmatch(key) else None
            if ident is not None:
                found.setdefault(ident, []).append(
                    json.dumps(value, ensure_ascii=False)
                )
                return
            for item in value.values():
                walk(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                walk(item, depth + 1)

    for item in evidence:
        try:
            walk(json.loads(item.content))
        except (ValueError, RecursionError):
            continue
    return found


def _id_pattern(doc_id: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(doc_id) + r"(?![A-Za-z0-9])")


def _key_string(sentence: Sentence) -> str | None:
    text = sentence.text
    # The citation being replaced cannot itself be the factual key string.
    for doc_id in sentence.citations:
        text = _id_pattern(doc_id).sub(" ", text)
    return max(_TOKEN.findall(text), key=len, default=None)


def _replace_citation(
    row_key: str,
    kind: str,
    sentences: Sequence[Sentence],
    index: int,
    old: str,
    new: str,
) -> InjectedRow | InjectionSkip:
    sentence = sentences[index]
    text, count = _id_pattern(old).subn(new, sentence.text, count=1)
    texts = [s.text for s in sentences]
    texts[index] = text
    rebuilt = build_sentences("\n".join(texts))
    if count != 1 or [s.sentence_id for s in rebuilt] != [
        s.sentence_id for s in sentences
    ]:
        return InjectionSkip(row_key, "split_count_changed")
    result = list(sentences)
    result[index] = rebuilt[index]
    return InjectedRow(row_key, kind, tuple(result), sentence.sentence_id, new)


def inject(
    row_key: str,
    kind: str,
    sentences: Sequence[Sentence],
    evidence: Sequence[EvidenceItem],
    observed_ids: Sequence[str],
    seed: int,
    allowed_pairs: set[tuple[str, str]] | None = None,
) -> InjectedRow | InjectionSkip:
    """Make one copy or a reasoned skip, without model calls or input mutation.

    EvidenceItem has no per-document body field. For wrong_support we conservatively
    check *every whole tool response* mentioning a candidate ID. Bundled/ambiguous
    evidence may therefore cause a skip rather than a falsely guaranteed injection.
    An observed ID without any matching evidence is never a candidate.

    ``allowed_pairs`` (sentence_id, doc_id) restricts citation replacements to pairs a
    human marked as truly supported, so the target is chosen among eligible ones instead
    of a random sentence that the caller would later reject.
    """
    if kind not in INJECTION_KINDS:
        raise ValueError("unknown injection kind")
    rebuilt = build_sentences("\n".join(s.text for s in sentences))
    if [s.sentence_id for s in rebuilt] != [s.sentence_id for s in sentences]:
        return InjectionSkip(row_key, "split_count_changed")
    rng = random.Random(seed)
    bodies = [_evidence_text(item) for item in evidence]
    if kind == "fact":
        pool = list(_FACT_POOL)
        rng.shuffle(pool)
        for value, template in pool:
            if any(value in body for body in bodies):
                continue
            text = template.format(value=value)
            if split_sentences(text) != [text]:
                continue
            sid = f"s{len(sentences) + 1}"
            sentence = Sentence(sid, text, tuple(extract_citations(text)))
            return InjectedRow(row_key, kind, (*sentences, sentence), sid, None)
        return InjectionSkip(row_key, "no_candidate", "pool_exhausted")

    def eligible(sentence: Sentence) -> list[str]:
        return [
            doc
            for doc in sentence.citations
            if allowed_pairs is None or (sentence.sentence_id, doc) in allowed_pairs
        ]

    cited = [i for i, s in enumerate(sentences) if s.citations]
    indices = [i for i in cited if eligible(sentences[i])]
    rng.shuffle(indices)
    if not indices:
        return InjectionSkip(
            row_key,
            "no_candidate",
            "no_eligible_cited_sentence" if cited else "no_cited_sentence",
        )
    if kind == "fabricated_id":
        pool = list(_FABRICATED_IDS)
        rng.shuffle(pool)
        forbidden = set(observed_ids) | {doc for s in sentences for doc in s.citations}
        for new in pool:
            if new not in forbidden and not any(new in body for body in bodies):
                index = indices[0]
                old = rng.choice(eligible(sentences[index]))
                return _replace_citation(row_key, kind, sentences, index, old, new)
        return InjectionSkip(row_key, "no_candidate", "pool_exhausted")

    documents = _documents(evidence)
    found_key = False
    cause = "no_observed_candidate"
    for index in indices:
        sentence = sentences[index]
        key = _key_string(sentence)
        if key is None:
            continue
        found_key = True
        candidates = sorted(set(observed_ids) - set(sentence.citations))
        rng.shuffle(candidates)
        for new in candidates:
            matching = documents.get(new) or [
                body for body in bodies if new in extract_citations(body)
            ]
            if not matching:
                cause = (
                    "no_matching_evidence"
                    if cause == "no_observed_candidate"
                    else cause
                )
                continue
            if not any(key in body for body in matching):
                old = rng.choice(eligible(sentence))
                return _replace_citation(row_key, kind, sentences, index, old, new)
            cause = "key_in_matching_evidence"
    if not found_key:
        return InjectionSkip(row_key, "no_key_string", "no_key_string")
    return InjectionSkip(row_key, "no_candidate", cause)


def injection_detected(injected: InjectedRow, agg: RowAggregate) -> bool:
    """Detection uses exact dictionary keys, never text or substring matching."""
    sid = injected.target_sentence_id
    if injected.kind == "fact":
        return (
            agg.labels.get(sid) not in {"supported", "not_claim"}
            and agg.positive.get(sid) is True
        )
    if injected.kind in {"fabricated_id", "wrong_support"}:
        return agg.citation_supports.get((sid, injected.target_doc_id)) is False
    raise ValueError("unknown injection kind")

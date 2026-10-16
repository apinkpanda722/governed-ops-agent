"""Sentence-level M8 grading, with reserved costs and content-free failures."""

import asyncio
import json
import logging
import os
from collections import Counter
from collections.abc import Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from govops.costs import CostLedger, ensure_price_valid, gemini_cost_usd
from govops.eval.grading import (
    GRADER_MODEL,
    GRADER_REPEATS,
    GRADER_TEMPERATURE,
    MAX_OUTPUT_TOKENS,
    GradingError,
    TokenUsage,
)
from govops.grounding.evidence import EvidenceItem
from govops.grounding.prompt import OUTPUT_SCHEMA, build_prompt
from govops.grounding.sentences import Sentence
from govops.grounding.spend import M8Spend

LABELS = ("supported", "unsupported", "contradicted", "not_claim")
POSITIVE_LABELS = frozenset({"unsupported", "contradicted"})
_TIE_ORDER = ("contradicted", "unsupported", "supported", "not_claim")
_IN_CALL = ContextVar("m8_grading_call", default=False)


@dataclass(frozen=True)
class SentenceResult:
    sentence_id: str
    label: str
    cited_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    citation_supports: tuple[tuple[str, bool], ...]


@dataclass(frozen=True)
class GradeResult:
    sentences: tuple[SentenceResult, ...]


@dataclass(frozen=True)
class RowAggregate:
    labels: dict[str, str]
    positive: dict[str, bool]
    citation_supports: dict[tuple[str, str], bool]
    row_positive: bool


class GradeCall(Protocol):
    async def __call__(self, prompt: str) -> tuple[str, TokenUsage]: ...


class _LogFilter(logging.Filter):
    def filter(self, record):
        return not _IN_CALL.get()


def _protect_logs():
    filter_ = _LogFilter()
    for logger in [
        logging.getLogger(),
        *[
            item
            for item in logging.Logger.manager.loggerDict.values()
            if isinstance(item, logging.Logger)
        ],
    ]:
        for handler in logger.handlers:
            if not any(isinstance(f, _LogFilter) for f in handler.filters):
                handler.addFilter(filter_)
        if logger.name.startswith(("google", "httpx", "httpcore")) and not any(
            isinstance(f, _LogFilter) for f in logger.filters
        ):
            logger.addFilter(filter_)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
        raise ValueError
    if len(value) != len(set(value)):
        raise ValueError
    return tuple(value)


def parse_grade(text: str, expected_ids: Sequence[str]) -> GradeResult:
    """Reject malformed output without preserving a body-bearing exception."""
    failed = False
    try:
        expected = tuple(expected_ids)
        if any(not isinstance(sid, str) for sid in expected) or len(expected) != len(
            set(expected)
        ):
            raise ValueError
        raw = json.loads(text, object_pairs_hook=_unique_object)
        if (
            not isinstance(raw, dict)
            or set(raw) != {"sentences"}
            or not isinstance(raw["sentences"], list)
        ):
            raise ValueError
        found = {}
        for row in raw["sentences"]:
            if not isinstance(row, dict) or set(row) != {
                "sentence_id",
                "label",
                "cited_ids",
                "evidence_ids",
                "citation_supports",
            }:
                raise ValueError
            sid = row["sentence_id"]
            if not isinstance(sid, str) or sid in found or row["label"] not in LABELS:
                raise ValueError
            cited = _strings(row["cited_ids"])
            evidence = _strings(row["evidence_ids"])
            supports = row["citation_supports"]
            if not isinstance(supports, list):
                raise TypeError
            pairs = []
            seen = set()
            for support in supports:
                if not isinstance(support, dict) or set(support) != {
                    "doc_id",
                    "supports",
                }:
                    raise ValueError
                doc_id = support["doc_id"]
                if (
                    not isinstance(doc_id, str)
                    or doc_id not in cited
                    or doc_id in seen
                    or type(support["supports"]) is not bool
                ):
                    raise ValueError
                seen.add(doc_id)
                pairs.append((doc_id, support["supports"]))
            found[sid] = SentenceResult(
                sid, row["label"], cited, evidence, tuple(pairs)
            )
        if set(found) != set(expected):
            raise ValueError
        result = GradeResult(tuple(found[sid] for sid in expected))
    except (ValueError, TypeError, KeyError, RecursionError):
        failed = True
    if failed:
        raise GradingError("grading_response_failure")
    return result


def _validate_input_links(
    result: GradeResult, sentences: Sequence[Sentence], evidence: Sequence[EvidenceItem]
) -> None:
    known = {f"E{index}" for index in range(1, len(evidence) + 1)}
    for row, sentence in zip(result.sentences, sentences, strict=True):
        if (
            set(row.cited_ids) != set(sentence.citations)
            or {doc_id for doc_id, _ in row.citation_supports}
            != set(sentence.citations)
            or not set(row.evidence_ids) <= known
        ):
            raise GradingError("grading_response_failure")


def aggregate_repeats(results: Sequence[GradeResult]) -> RowAggregate:
    if not results:
        raise GradingError("invalid grading repeats")
    ids = [s.sentence_id for s in results[0].sentences]
    # Validate dataclass inputs too, rather than silently treating missing votes as false.
    maps = []
    for result in results:
        raw = {
            "sentences": [
                {
                    "sentence_id": s.sentence_id,
                    "label": s.label,
                    "cited_ids": list(s.cited_ids),
                    "evidence_ids": list(s.evidence_ids),
                    "citation_supports": [
                        {"doc_id": doc, "supports": vote}
                        for doc, vote in s.citation_supports
                    ],
                }
                for s in result.sentences
            ]
        }
        validated = parse_grade(json.dumps(raw), ids)
        maps.append({s.sentence_id: s for s in validated.sentences})
    labels, positive, supports = {}, {}, {}
    for sid in ids:
        rows = [mapping[sid] for mapping in maps]
        counts = Counter(row.label for row in rows)
        labels[sid] = max(_TIE_ORDER, key=lambda label: counts[label])
        positive[sid] = sum(row.label in POSITIVE_LABELS for row in rows) >= 2
        pairs = [dict(row.citation_supports) for row in rows]
        if any(
            set(pair) != set(pairs[0]) or set(row.cited_ids) != set(rows[0].cited_ids)
            for pair, row in zip(pairs, rows, strict=True)
        ):
            raise GradingError("invalid grading repeats")
        for doc_id in pairs[0]:
            supports[(sid, doc_id)] = (
                sum(pair[doc_id] for pair in pairs) > len(results) / 2
            )
    return RowAggregate(labels, positive, supports, any(positive.values()))


class GroundingGrader:
    def __init__(
        self,
        *,
        ledger: CostLedger,
        spend: M8Spend,
        call: GradeCall | None = None,
        on: date | None = None,
    ):
        self.ledger = ledger
        self.spend = spend
        self.call = call
        self.on = on
        self.last_model = None

    def _settle(
        self,
        reservation: str,
        spend_reservation: str,
        task_id: str,
        usage: TokenUsage,
        cost: float,
    ) -> None:
        try:
            self.ledger.settle(
                reservation,
                purpose="grading",
                model=GRADER_MODEL,
                task_id=task_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=cost,
            )
        finally:
            self.spend.settle(spend_reservation, cost)

    async def grade(
        self,
        *,
        row_key: str,
        version: str,
        stage: str,
        request: str,
        sentences: Sequence[Sentence],
        evidence: Sequence[EvidenceItem],
        repeat_index: int,
    ) -> GradeResult:
        if (
            not isinstance(row_key, str)
            or not row_key
            or type(repeat_index) is not int
            or not 0 <= repeat_index < GRADER_REPEATS
            or not sentences
            or not evidence
        ):
            raise GradingError("invalid grading input")
        ids = [s.sentence_id for s in sentences]
        if len(set(ids)) != len(ids):
            raise GradingError("invalid grading input")
        prompt = build_prompt(version, request, sentences, evidence)
        input_upper = 2 * len(prompt.encode()) + 4096
        estimate = gemini_cost_usd(
            GRADER_MODEL,
            input_tokens=input_upper,
            output_tokens=MAX_OUTPUT_TOKENS,
            on=self.on,
        )
        task_id = f"m8:{stage}:{row_key}"
        self.spend.check(stage, estimate)
        reservation = self.ledger.reserve(
            purpose="grading",
            model=GRADER_MODEL,
            task_id=task_id,
            estimate_usd=estimate,
        )
        try:
            # Repeat the M8 check atomically after reserving the supplier budget.
            spend_reservation = self.spend.reserve(stage, estimate)
        except BaseException:
            # A competing M8 reservation can win after check(); no call was made.
            self.ledger.settle(
                reservation,
                purpose="grading",
                model=GRADER_MODEL,
                task_id=task_id,
                input_tokens=0,
                output_tokens=0,
                cost_usd=0,
            )
            raise
        usage, cost = TokenUsage(0, 0), 0.0
        failure = None
        _protect_logs()
        token = _IN_CALL.set(True)
        try:
            # Recheck immediately before the call, including a date boundary.
            for suffix in ("input", "output"):
                ensure_price_valid(f"google.{GRADER_MODEL}.{suffix}", on=self.on)
            # Unconfirmed usage remains conservatively charged at the reservation.
            usage, cost = TokenUsage(input_upper, MAX_OUTPUT_TOKENS), estimate
            try:
                raw, confirmed = await asyncio.wait_for(
                    self.call(prompt)
                    if self.call is not None
                    else self._sdk_call(prompt, input_upper),
                    timeout=60,
                )
                if not isinstance(confirmed, TokenUsage):
                    raise GradingError("grading_response_failure")
                usage = confirmed
                cost = gemini_cost_usd(
                    GRADER_MODEL,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    on=self.on,
                )
                result = parse_grade(raw, ids)
                _validate_input_links(result, sentences, evidence)
                if self.call is not None:
                    self.last_model = GRADER_MODEL
            except GradingError as exc:
                confirmed = getattr(exc, "usage", None)
                if isinstance(confirmed, TokenUsage):
                    usage = confirmed
                    cost = gemini_cost_usd(
                        GRADER_MODEL,
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        on=self.on,
                    )
                failure = GradingError("grading_response_failure")
            except Exception:  # noqa: BLE001 — never expose SDK or injected messages
                failure = GradingError("grading_call_failure")
        finally:
            try:
                await asyncio.shield(
                    asyncio.to_thread(
                        self._settle,
                        reservation,
                        spend_reservation,
                        task_id,
                        usage,
                        cost,
                    )
                )
            finally:
                _IN_CALL.reset(token)
        if failure is not None:
            raise failure
        return result

    async def _sdk_call(self, prompt: str, input_upper: int) -> tuple[str, TokenUsage]:
        from google import genai
        from google.genai import types

        key = os.getenv("GEMINI_API_KEY")
        if not key or not key.strip():
            raise RuntimeError("grading_call_failure")
        client = genai.Client(
            api_key=key,
            vertexai=False,
            http_options=types.HttpOptions(
                timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)
            ),
        )
        try:
            response = await client.aio.models.generate_content(
                model=GRADER_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=GRADER_TEMPERATURE,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    response_mime_type="application/json",
                    response_json_schema=OUTPUT_SCHEMA,
                ),
            )
            self.last_model = response.model_version or GRADER_MODEL
            raw = response.usage_metadata
            try:
                usage = TokenUsage(
                    raw.prompt_token_count,
                    raw.candidates_token_count + (raw.thoughts_token_count or 0),
                )
            except (AttributeError, TypeError, GradingError):
                usage = TokenUsage(input_upper, MAX_OUTPUT_TOKENS)
            if response.model_version and not response.model_version.startswith(
                GRADER_MODEL
            ):
                error = GradingError("grading_response_failure")
                error.usage = usage
                raise error
            return response.text, usage
        finally:
            await client.aio.aclose()
            client.close()

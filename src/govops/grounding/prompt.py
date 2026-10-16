"""Registered M8 templates and their structured-output contract."""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from govops.grounding.evidence import EvidenceItem
from govops.grounding.sentences import Sentence

PROMPT_VERSIONS: dict[str, Path] = {
    "v3.0": Path(__file__).parent / "prompts" / "v3_0.txt"
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sentence_id": {"type": "string"},
                    "label": {
                        "type": "string",
                        "enum": [
                            "supported",
                            "unsupported",
                            "contradicted",
                            "not_claim",
                        ],
                    },
                    "cited_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "citation_supports": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "doc_id": {"type": "string"},
                                "supports": {"type": "boolean"},
                            },
                            "required": ["doc_id", "supports"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": [
                    "sentence_id",
                    "label",
                    "cited_ids",
                    "evidence_ids",
                    "citation_supports",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["sentences"],
    "additionalProperties": False,
}


def _template(version: str) -> Path:
    if version not in PROMPT_VERSIONS:
        raise ValueError("unknown grounding prompt version")
    return PROMPT_VERSIONS[version]


def _schema_json() -> str:
    return json.dumps(
        OUTPUT_SCHEMA, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def prompt_sha256(version: str) -> str:
    """Bind registration to exact template bytes and canonical schema JSON."""
    return hashlib.sha256(
        _template(version).read_bytes() + _schema_json().encode()
    ).hexdigest()


def build_prompt(
    version: str,
    request: str,
    sentences: Sequence[Sentence],
    evidence: Sequence[EvidenceItem],
) -> str:
    payload = {
        "request": request,
        "sentences": [
            {
                "sentence_id": s.sentence_id,
                "text": s.text,
                "citations": list(s.citations),
            }
            for s in sentences
        ],
        "evidence": [
            {"eid": f"E{index}", "tool_name": e.tool_name, "content": e.content}
            for index, e in enumerate(evidence, 1)
        ],
    }
    return (
        _template(version).read_text(encoding="utf-8")
        + "\n출력 JSON 스키마:\n"
        + _schema_json()
        + "\n입력 JSON:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )

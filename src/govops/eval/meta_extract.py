"""One-time read-only extraction of synthetic evaluation metadata (user run)."""

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

from govops.eval.meta import MetaValidationError, validate_meta
from govops.mocks.seed_extract import (
    EXPECTED_A_COMMIT,
    _json_bytes,
    _publish,
    _resolve_commit,
)

# Preregistered before examining extraction results; overrides are for fixtures.
EXPECTED_COUNTS = {
    "doc_count": 200,
    "doc_type_counts": {
        "meeting": 50,
        "weekly": 40,
        "runbook": 30,
        "guide": 30,
        "incident": 25,
        "notice": 25,
    },
    "canary_doc_count": 30,
    "injection_doc_count": 12,
}


def _fact_ids(raw: str) -> list[str]:
    if not isinstance(raw, str):
        raise MetaValidationError("invalid ticket fact IDs")
    if raw.lstrip().startswith("["):
        return json.loads(raw)
    return [value for value in re.split(r"[,;|\s]+", raw.strip()) if value]


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _extract(a_root: Path, out: Path, expected: dict) -> dict:
    a_root, out = a_root.resolve(), out.resolve()
    names = ("meta.json", "meta.manifest.json")
    if out.is_relative_to(a_root) or any(
        (out / name).resolve().is_relative_to(a_root) for name in names
    ):
        raise MetaValidationError("output must be outside A repository")
    syn, seed = a_root / "data/synthetic", a_root / "data/seed"
    banned = yaml.safe_load((seed / "banned_names.yaml").read_text(encoding="utf-8"))
    if (
        not isinstance(banned, dict)
        or not isinstance(banned.get("names"), list)
        or any(not isinstance(n, str) or not n for n in banned["names"])
    ):
        raise MetaValidationError("invalid banned names list")
    docs = []
    for line in (syn / "plan.jsonl").read_text(encoding="utf-8").splitlines():
        raw = json.loads(line)
        doc = {
            key: raw[key]
            for key in ("doc_id", "doc_type", "team", "fact_ids", "related_doc_ids")
        }
        doc.update(
            access_level=raw["access"]["level"],
            access_teams=raw["access"]["teams"],
            access_min_grade=raw["access"]["min_grade"],
            injection_id=raw.get("injection_id"),
            canary_fact_id=raw.get("canary_fact_id"),
        )
        docs.append(doc)
    canaries = [
        {
            key: raw[key]
            for key in (
                "canary_id",
                "fact_id",
                "value",
                "doc_id",
                "owner_team",
                "min_grade",
            )
        }
        for raw in _read_json(syn / "canaries.json")["canaries"]
    ]
    injections = [
        {key: raw[key] for key in ("injection_id", "doc_id", "variant", "access_level")}
        for raw in _read_json(syn / "injections.json")["injections"]
    ]
    facts = []
    for source_kind, directory in (("incident", "incidents"), ("event", "events")):
        for path in sorted((seed / directory).glob("*.yaml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            sources = [raw] if source_kind == "incident" else raw["events"]
            for source in sources:
                for fact in source["facts"]:
                    facts.append(
                        {
                            **{key: fact[key] for key in ("fact_id", "kind", "value")},
                            "source_kind": source_kind,
                            "source_id": source["id"],
                            "owner_team": source.get("owner_team"),
                        }
                    )
    # plan.jsonl의 문서는 서비스 사실(f-service-*)도 참조한다. 서비스 설명은 복사하지 않는다.
    services_path = seed / "services.yaml"
    if not services_path.is_file():
        raise MetaValidationError("required A seed file missing: services.yaml")
    services = yaml.safe_load(services_path.read_text(encoding="utf-8"))
    for service in services["services"]:
        for fact in service["facts"]:
            facts.append(
                {
                    **{key: fact[key] for key in ("fact_id", "kind", "value")},
                    "source_kind": "service",
                    "source_id": service["name"],
                    "owner_team": service.get("owner_team"),
                }
            )
    for canary in canaries:
        facts.append(
            {
                **{key: canary[key] for key in ("fact_id", "value", "owner_team")},
                "kind": "text",
                "source_kind": "canary",
                "source_id": canary["canary_id"],
            }
        )
    # immutable prevents WAL/shared-memory sidecars as well as database writes.
    with sqlite3.connect(
        (syn / "ledger.sqlite").as_uri() + "?mode=ro&immutable=1", uri=True
    ) as db:
        tickets = [
            {"ticket_id": row[0], "incident_id": row[1], "fact_ids": _fact_ids(row[2])}
            for row in db.execute(
                "SELECT ticket_id, incident_id, fact_ids FROM tickets WHERE is_anchor=1"
            )
        ]
    payload = {
        "docs": docs,
        "facts": facts,
        "canaries": canaries,
        "injections": injections,
        "tickets": tickets,
        "a_commit": _resolve_commit(a_root),
    }
    for key, id_key in (
        ("docs", "doc_id"),
        ("facts", "fact_id"),
        ("canaries", "canary_id"),
        ("injections", "injection_id"),
        ("tickets", "ticket_id"),
    ):
        payload[key].sort(key=lambda row: row[id_key])
    meta = validate_meta(payload)
    content = _json_bytes(meta.model_dump())

    # Check decoded strings, so JSON escaping cannot conceal a banned name.
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    if any(name in value for value in strings(payload) for name in banned["names"]):
        raise MetaValidationError("banned name in metadata")
    counts = {
        f"{key[:-1]}_count": len(payload[key])
        for key in ("docs", "facts", "canaries", "injections", "tickets")
    }
    counts["canary_count"] = counts.pop("canarie_count")
    counts.update(
        doc_type_counts={
            kind: sum(d.doc_type == kind for d in meta.docs)
            for kind in sorted({d.doc_type for d in meta.docs})
        },
        canary_doc_count=sum(d.canary_fact_id is not None for d in meta.docs),
        injection_doc_count=sum(d.injection_id is not None for d in meta.docs),
    )
    checks = {
        key: {"expected": value, "actual": counts[key], "matches": counts[key] == value}
        for key, value in expected.items()
    }
    report = {
        **counts,
        "a_commit": meta.a_commit,
        "expected_a_commit": EXPECTED_A_COMMIT,
        "a_commit_matches_expected": meta.a_commit.startswith(EXPECTED_A_COMMIT),
        "meta_sha256": hashlib.sha256(content).hexdigest(),
        "expectation_checks": checks,
        "expectations_match": all(check["matches"] for check in checks.values()),
    }
    _publish(out, {"meta.json": content, "meta.manifest.json": _json_bytes(report)})
    if not report["a_commit_matches_expected"]:
        print("warning: A commit differs from expected commit", file=sys.stderr)
    return report


def extract(a_root: Path, out: Path, *, expected: dict | None = None) -> dict:
    try:
        return _extract(a_root, out, EXPECTED_COUNTS if expected is None else expected)
    except MetaValidationError:
        raise
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        sqlite3.Error,
        yaml.YAMLError,
        subprocess.SubprocessError,
    ) as error:
        # 예외 클래스 이름만 알린다(메시지에는 값이 있을 수 있다).
        raise MetaValidationError(
            f"metadata extraction failed ({type(error).__name__}); check source and output"
        ) from None


def main(argv: list[str] | None = None, *, expected: dict | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("data/eval"))
    args = parser.parse_args(argv)
    try:
        report = extract(args.a_root, args.out, expected=expected)
    except MetaValidationError as error:
        print(str(error), file=sys.stderr)
        return 1
    status = "MATCH" if report["expectations_match"] else "MISMATCH"
    print(
        f"{status}: docs={report['doc_count']} facts={report['fact_count']} canaries={report['canary_count']} injections={report['injection_count']} tickets={report['ticket_count']} sha256={report['meta_sha256']}"
    )
    return 0 if report["expectations_match"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

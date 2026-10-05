"""Deterministic access-only runtime index, generated offline."""

import argparse
import ast
import hashlib
import json
import subprocess
from pathlib import Path


def encode(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")


def build_index(meta: dict, tickets: list[dict]) -> dict:
    def project(rows, key):
        result = {}
        for row in rows:
            if row[key] in result:
                raise ValueError("duplicate access ID")
            result[row[key]] = {
                "level": row["access_level"],
                "teams": list(row["access_teams"]),
                "min_grade": row["access_min_grade"],
            }
        return result

    return {
        "generated_from": {
            "a_commit": meta["a_commit"],
            "meta_sha256": hashlib.sha256(encode(meta)).hexdigest(),
            "tickets_sha256": hashlib.sha256(encode({"tickets": tickets})).hexdigest(),
        },
        "docs": project(meta["docs"], "doc_id"),
        "tickets": project(tickets, "ticket_id"),
    }


def committed_real_docs(a_root: Path, commit: str) -> dict:
    # Only these committed paths are read; no raw/interim/index files or imports.
    def read(name):
        return subprocess.check_output(
            ["git", "-C", str(a_root), "show", f"{commit}:{name}"], text=True
        )

    source = ast.parse(read("src/kodocs/chunking/corpus.py"))
    # Verify both real-document builders still assign the same access policy.
    for name in ("_load_current_document", "_law_api_document"):
        functions = [
            n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == name
        ]
        if not functions:
            raise ValueError("A real document builder changed")
        policies = [
            n
            for n in ast.walk(functions[0])
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "Access"
        ]
        if len(policies) != 1 or [ast.literal_eval(a) for a in policies[0].args] != [
            "public",
            (),
            0,
        ]:
            raise ValueError("A real document access policy changed")
    targets = json.loads(read("data/manifest/parse_targets.json"))["targets"]
    versions = json.loads(read("data/manifest/m5_prior_versions.json"))["versions"]
    serials = {t["law_serial"] for t in targets if t["role"] == "current"}
    serials.update(v["prior_serial"] for v in versions)
    if any(not isinstance(s, str) or not s.isdigit() for s in serials):
        raise ValueError("invalid A document serial")
    return {
        f"law-{s}": {"level": "public", "teams": [], "min_grade": 0}
        for s in sorted(serials)
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta", type=Path, default=Path("data/eval/meta.json"))
    parser.add_argument("--tickets", type=Path, default=Path("data/seed/tickets.json"))
    parser.add_argument("--out", type=Path, default=Path("data/seed/access_index.json"))
    parser.add_argument("--a-root", type=Path)
    args = parser.parse_args(argv)
    meta_bytes, ticket_bytes = args.meta.read_bytes(), args.tickets.read_bytes()
    index = build_index(json.loads(meta_bytes), json.loads(ticket_bytes)["tickets"])
    index["generated_from"].update(
        meta_sha256=hashlib.sha256(meta_bytes).hexdigest(),
        tickets_sha256=hashlib.sha256(ticket_bytes).hexdigest(),
    )
    if args.a_root is not None:
        if args.out.resolve().is_relative_to(args.a_root.resolve()):
            raise ValueError("output must be outside A repository")
        docs = committed_real_docs(args.a_root, index["generated_from"]["a_commit"])
        index["docs"].update(docs)
        index["generated_from"]["non_synthetic_doc_count"] = len(docs)
    args.out.write_bytes(encode(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Read-only signed-chain verification; reports contain codes, never evidence."""

import argparse
import hashlib
import json
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from govops.audit.crypto import (
    AuditKeyError,
    canonical_json,
    load_public_keys,
    record_hash,
    verify_hash,
)
from govops.audit.log import _validate_event
from govops.env import env_or

ROOT = Path(__file__).resolve().parents[3]
LEGACY = "????-??-??.jsonl"


@dataclass(frozen=True)
class Problem:
    code: str
    seq: int | None = None


@dataclass(frozen=True)
class ChainReport:
    directory: Path
    records: int
    kid: str | None
    problems: tuple[Problem, ...]
    anchors_checked: int
    legacy_files: int
    legacy_lines: int

    @property
    def ok(self) -> bool:
        return not self.problems


def _hex(value, length):
    return (
        isinstance(value, str)
        and re.fullmatch(rf"[0-9a-f]{{{length}}}", value) is not None
    )


def _timestamp(value):
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).utcoffset() is not None
    except ValueError:
        return False


def _relative(directory, root):
    try:
        return directory.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _unique(anchors):
    seen = set()
    for row in anchors:
        identity = (row["dir"], row["seq"], row["hash"])
        if identity not in seen:
            seen.add(identity)
            yield row


def read_anchor_lines(paths: Sequence[Path]) -> list[dict]:
    anchors = []
    try:
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_bytes().splitlines():
                row = json.loads(line)
                if not isinstance(row, dict) or set(row) != {
                    "dir",
                    "seq",
                    "hash",
                    "sig",
                    "kid",
                    "label",
                    "ts",
                }:
                    raise ValueError
                directory = row["dir"]
                if (
                    not isinstance(directory, str)
                    or not directory
                    or Path(directory).is_absolute()
                    or ".." in Path(directory).parts
                    or any(c in directory for c in "\n\r\x00")
                ):
                    raise ValueError
                if (
                    type(row["seq"]) is not int
                    or row["seq"] < 1
                    or not all(
                        _hex(row[k], n)
                        for k, n in (("hash", 64), ("sig", 128), ("kid", 12))
                    )
                ):
                    raise ValueError
                if (
                    not isinstance(row["label"], str)
                    or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", row["label"])
                    or not _timestamp(row["ts"])
                ):
                    raise ValueError
                anchors.append(row)
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError("anchor_file_invalid") from None
    return list(_unique(anchors))


def _missing_anchor_dirs(anchors, root, verified):
    visited = {Path(p).resolve() for p in verified}
    for row in _unique(anchors):
        directory = (root / row["dir"]).resolve()
        if directory in visited:
            continue
        visited.add(directory)
        if _relative(directory, root) is None or not any(
            directory.glob("chain-*.jsonl")
        ):
            yield row["dir"]


def verify_anchor_dirs(
    anchors, *, repo_root: Path, verified: Collection[Path]
) -> list[Problem]:
    return [
        Problem("anchor_dir_missing")
        for _ in _missing_anchor_dirs(anchors, repo_root, verified)
    ]


def _legacy(directory):
    files = sorted(directory.glob(LEGACY))
    hashes = []
    lines = 0
    last = 0
    for path in files:
        data = path.read_bytes()
        hashes.append([path.name, hashlib.sha256(data).hexdigest()])
        entries = data.splitlines()
        lines += len(entries)
        # Only the last sequence is extracted for the seal; legacy is not verified.
        if entries:
            try:
                value = json.loads(entries[-1])["seq"]
                last = value if type(value) is int else None
            except (ValueError, KeyError, TypeError, UnicodeError):
                last = None
    return (
        len(files),
        lines,
        last,
        hashlib.sha256(canonical_json(hashes).encode()).hexdigest(),
    )


def _valid_record(row):
    if not isinstance(row, dict):
        return False
    required = {
        "seq",
        "ts",
        "actor",
        "event",
        "task_id",
        "prev_hash",
        "hash",
        "sig",
        "kid",
    }
    if (
        not required <= row.keys()
        or type(row["seq"]) is not int
        or row["seq"] < 1
        or not _timestamp(row["ts"])
    ):
        return False
    if not all(
        _hex(row[k], n)
        for k, n in (("prev_hash", 64), ("hash", 64), ("sig", 128), ("kid", 12))
    ):
        return False
    actor = row["actor"]
    if (
        not isinstance(actor, dict)
        or set(actor) != {"sub", "act"}
        or actor["act"] != "governed-ops-agent"
    ):
        return False
    try:
        _validate_event(
            row["event"],
            row["task_id"],
            actor["sub"],
            {k: v for k, v in row.items() if k not in required},
        )
        canonical_json(row)
    except (ValueError, TypeError, KeyError):
        return False
    return True


def verify_chain(
    directory: Path,
    *,
    public_keys: dict | None = None,
    anchors: Sequence[dict] = (),
    repo_root: Path | None = None,
) -> ChainReport:
    directory = Path(directory).resolve()
    root = ROOT if repo_root is None else Path(repo_root)
    keys = load_public_keys() if public_keys is None else public_keys
    problems = []
    files = sorted(directory.glob("chain-*.jsonl"))
    legacy_files, legacy_lines, legacy_last, legacy_hash = _legacy(directory)
    if not files:
        problems.append(Problem("no_chain"))
    records = []
    previous_hash = "0" * 64
    previous_stored = previous_hash
    previous_seq = None
    total = 0
    first = None
    for path in files:
        data = path.read_bytes()
        if data and not data.endswith(b"\n"):
            problems.append(Problem("unterminated_tail"))
        for line in data.splitlines():
            total += 1
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                problems.append(Problem("invalid_json"))
                continue
            if total == 1:
                first = row
            if not _valid_record(row):
                seq = row.get("seq") if isinstance(row, dict) else None
                problems.append(
                    Problem("invalid_record", seq if type(seq) is int else None)
                )
                continue
            seq = row["seq"]
            calculated = record_hash(
                {k: v for k, v in row.items() if k not in {"hash", "sig"}}
            )
            if calculated != row["hash"]:
                problems.append(Problem("hash_mismatch", seq))
            # Check both stored linkage and recalculated evidence after field tampering.
            if row["prev_hash"] != previous_hash or row["prev_hash"] != previous_stored:
                problems.append(Problem("prev_hash_mismatch", seq))
            if previous_seq is not None:
                if seq <= previous_seq:
                    problems.append(Problem("seq_order", seq))
                elif seq != previous_seq + 1:
                    problems.append(Problem("seq_gap", seq))
            if row["kid"] not in keys:
                problems.append(Problem("kid_unknown", seq))
            elif not verify_hash(keys[row["kid"]], row["hash"], row["sig"]):
                problems.append(Problem("sig_invalid", seq))
            records.append(row)
            previous_hash, previous_stored, previous_seq = calculated, row["hash"], seq
    if files:
        if (
            not isinstance(first, dict)
            or first.get("event") != "chain.genesis"
            or type(first.get("legacy_last_seq")) is not int
            or first.get("seq") != first["legacy_last_seq"] + 1
            or first.get("prev_hash") != "0" * 64
        ):
            problems.append(Problem("genesis_missing"))
        if (
            isinstance(first, dict)
            and first.get("event") == "chain.genesis"
            and tuple(
                first.get(k)
                for k in (
                    "legacy_files",
                    "legacy_lines",
                    "legacy_last_seq",
                    "legacy_sha256",
                )
            )
            != (legacy_files, legacy_lines, legacy_last, legacy_hash)
        ):
            problems.append(
                Problem(
                    "legacy_sha256_mismatch",
                    first.get("seq") if type(first.get("seq")) is int else None,
                )
            )
    checked = 0
    relative = _relative(directory, root)
    if relative is not None:
        for fixed in _unique(anchors):
            if fixed["dir"] != relative:
                continue
            checked += 1
            seq = fixed["seq"]
            row = next((r for r in records if r["seq"] == seq), None)
            if row is None:
                problems.append(
                    Problem(
                        "tail_truncated"
                        if previous_seq is None or previous_seq < seq
                        else "anchor_missing",
                        seq,
                    )
                )
            elif row["event"] != "chain.anchor" or any(
                row[k] != fixed[k] for k in ("hash", "sig", "kid")
            ):
                problems.append(Problem("anchor_hash_mismatch", seq))
            if fixed["kid"] not in keys or not verify_hash(
                keys[fixed["kid"]], fixed["hash"], fixed["sig"]
            ):
                problems.append(Problem("anchor_sig_invalid", seq))
    return ChainReport(
        directory,
        total,
        records[0]["kid"] if records else None,
        tuple(problems),
        checked,
        legacy_files,
        legacy_lines,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir", type=Path, default=env_or("AUDIT_DIR", "data/interim/audit")
    )
    parser.add_argument(
        "--public-key",
        type=Path,
        default=env_or("AUDIT_PUBLIC_KEY_FILE", "data/seed/audit_public_key.pem"),
    )
    parser.add_argument("--anchors", type=Path, action="append")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--scan", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()

    def rooted(path):
        return path if path.is_absolute() else root / path

    try:
        anchors = read_anchor_lines(
            [
                rooted(p)
                for p in (
                    args.anchors
                    if args.anchors is not None
                    else [
                        Path(
                            env_or(
                                "AUDIT_ANCHORS_PENDING_FILE",
                                "data/interim/audit/anchors-pending.jsonl",
                            )
                        ),
                        Path("docs/results/audit-anchors.jsonl"),
                    ]
                )
            ]
        )
        keys = load_public_keys(rooted(args.public_key))
    except (ValueError, AuditKeyError) as error:
        code = error.code if isinstance(error, AuditKeyError) else "anchor_file_invalid"
        print(f"FAIL code={code}")
        return 1
    if not anchors:
        print("INFO anchors=0 (꼬리 잘라내기는 고정점이 있어야 탐지된다)")
    directories = {rooted(args.dir).resolve()}
    legacy_dirs = set()
    for scan in args.scan:
        tree = rooted(scan)
        directories.update(p.parent.resolve() for p in tree.rglob("chain-*.jsonl"))
        legacy_dirs.update(
            p.parent.resolve() for p in tree.rglob(LEGACY) if p.parent.name == "audit"
        )
    passed = failed = checked = legacy_count = legacy_lines = 0
    verified = set()
    missing_selected = set()
    for directory in sorted(directories):
        report = verify_chain(
            directory, public_keys=keys, anchors=anchors, repo_root=root
        )
        name = _relative(directory, root) or "<outside-repo>"
        checked += report.anchors_checked
        verified.add(directory)
        if any(p.code == "no_chain" for p in report.problems):
            missing_selected.update(
                a["dir"] for a in anchors if a["dir"] == _relative(directory, root)
            )
        if report.ok:
            passed += 1
            print(
                f"PASS chain dir={name} records={report.records} kid={report.kid} anchors={report.anchors_checked}"
            )
        else:
            failed += 1
            for problem in report.problems:
                print(
                    f"FAIL chain dir={name} code={problem.code} seq={problem.seq if problem.seq is not None else '-'}"
                )
        if report.legacy_files:
            legacy_dirs.add(directory)
    for directory in sorted(legacy_dirs):
        count, lines, _, _ = _legacy(directory)
        legacy_count += count
        legacy_lines += lines
    anchor_failed = False
    for name in sorted(
        missing_selected | set(_missing_anchor_dirs(anchors, root, verified))
    ):
        anchor_failed = True
        print(f"FAIL anchor dir={name} code=anchor_dir_missing")
    if legacy_count:
        print(
            f"INFO legacy_unverified dirs={len(legacy_dirs)} files={legacy_count} lines={legacy_lines} (체인 없음, 미검증)"
        )
    print(
        f"SUMMARY chains_pass={passed} chains_fail={failed} legacy_unverified_files={legacy_count} anchors_checked={checked}"
    )
    return int(bool(failed or anchor_failed))


if __name__ == "__main__":
    raise SystemExit(main())

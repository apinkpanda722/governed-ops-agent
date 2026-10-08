"""Append independent anchor candidates to a private pending file."""

import argparse
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from govops.audit.crypto import AuditKeyError, canonical_json
from govops.env import env_or

ROOT = Path(__file__).resolve().parents[3]


class AuditAnchorError(Exception):
    """Expose only a fixed anchor failure code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def validate_label(label: str) -> None:
    if not isinstance(label, str) or not re.fullmatch(
        r"[a-z0-9][a-z0-9-]{0,39}", label
    ):
        raise AuditAnchorError("audit_anchor_label_invalid")


def write_pending(row: dict, pending_file: Path | None = None) -> None:
    path = (
        Path(pending_file)
        if pending_file is not None
        else Path(
            env_or(
                "AUDIT_ANCHORS_PENDING_FILE", "data/interim/audit/anchors-pending.jsonl"
            )
        )
    )
    payload = (canonical_json(row) + "\n").encode("utf-8")
    try:
        # mkdir(parents=True) does not apply mode to intermediate directories.
        missing = []
        parent = path.parent
        while not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for parent in reversed(missing):
            parent.mkdir(mode=0o700, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.fchmod(fd, 0o600)
            if os.write(fd, payload) != len(payload):
                raise OSError
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        raise AuditAnchorError("audit_anchor_write_failed") from None


def main(argv: Sequence[str] | None = None) -> int:
    from govops.audit.log import AuditLog, AuditWriteError

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        validate_label(args.label)
        row = AuditLog().anchor(args.label, repo_root=args.repo_root)
    except (AuditAnchorError, AuditKeyError, AuditWriteError) as error:
        print(f"FAIL {error.code}", file=sys.stderr)
        return 1
    print(canonical_json(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

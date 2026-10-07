"""Byte-only fingerprint of the versioned rules artifact."""

import hashlib
from pathlib import Path

RULES_FILE: Path = Path(__file__).with_name("rules_v1.json")


def rules_sha256(path: str | Path | None = None) -> str:
    return hashlib.sha256(Path(path or RULES_FILE).read_bytes()).hexdigest()

import hashlib
from pathlib import Path

import pytest

from govops.gateway.hook_digest import HOOK_FILES, compute_digests

ROOT = Path(__file__).resolve().parents[1]


def copy_hook(root):
    for name in HOOK_FILES:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())


def test_byte_digests_and_missing(tmp_path):
    copy_hook(tmp_path)
    first = compute_digests(tmp_path)
    payload = "".join(
        f"{name}\0{hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()}\n"
        for name in HOOK_FILES
    ).encode()
    assert first.code == hashlib.sha256(payload).hexdigest()[:12]
    target = tmp_path / HOOK_FILES[2]
    target.write_bytes(target.read_bytes() + b"\n")
    second = compute_digests(tmp_path)
    assert first.code != second.code and first.rules == second.rules
    rules = tmp_path / "src/govops/guardrails/rules_v1.json"
    rules.write_bytes(rules.read_bytes() + b"\n")
    assert compute_digests(tmp_path).rules != first.rules
    target.unlink()
    with pytest.raises(FileNotFoundError):
        compute_digests(tmp_path)

"""Fingerprints of the exact read-only hook deployment inputs."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from govops.guardrails.registry import rules_sha256

HOOK_FILES: tuple[str, ...] = (
    "src/govops/__init__.py",
    "src/govops/gateway/__init__.py",
    "src/govops/gateway/guardrail_hook.py",
    "src/govops/gateway/hook_digest.py",
    "src/govops/gateway/hook_header.py",
    "src/govops/guardrails/__init__.py",
    "src/govops/guardrails/injection.py",
    "src/govops/guardrails/pii.py",
    "src/govops/guardrails/registry.py",
    "src/govops/guardrails/screen.py",
    "src/govops/guardrails/rules_v1.json",
    "src/govops/obs/__init__.py",
    "src/govops/obs/mask.py",
    "data/seed/users.json",
)


@dataclass(frozen=True)
class HookDigests:
    rules: str
    code: str


def compute_digests(root: Path) -> HookDigests:
    payload = "".join(
        f"{name}\0{hashlib.sha256((root / name).read_bytes()).hexdigest()}\n"
        for name in HOOK_FILES
    ).encode()
    return HookDigests(
        rules_sha256(root / "src/govops/guardrails/rules_v1.json")[:12],
        hashlib.sha256(payload).hexdigest()[:12],
    )

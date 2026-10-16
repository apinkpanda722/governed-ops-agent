"""Hash only tracked bytes to pin the official test freeze boundary."""

import hashlib
import subprocess
from pathlib import Path

FREEZE_PATHS: tuple[str, ...] = (
    "src/govops/agent",
    "src/govops/tools",
    "src/govops/guardrails",
    "src/govops/gateway",
    "src/govops/broker",
    "src/govops/eval",
    "src/govops/evalrun",
    "src/govops/replay",
    "src/govops/mocks",
    "data/contract",
    "data/policy",
    "data/replay",
    "data/seed",
    "data/eval",
    "data/redteam",
    "docs/results/m6-redteam-run",
    "src/govops/costs.py",
    "src/govops/golden.py",
    "infra/litellm/config.yaml",
    "src/govops/audit/__init__.py",
    "src/govops/audit/log.py",
    "src/govops/audit/crypto.py",
    "src/govops/audit/anchor.py",
    "src/govops/audit/verify.py",
    "src/govops/audit/smoke.py",
    "docs/results/hypotheses.md",
    "docs/results/hypotheses-m3.md",
    "docs/results/hypotheses-m3-faults.md",
    "docs/results/hypotheses-m3-grading.md",
    "docs/results/hypotheses-m3-prompt.md",
    "docs/results/hypotheses-m4-guardrail.md",
    "docs/results/hypotheses-m4-redteam.md",
    "docs/results/hypotheses-m5-budget.md",
    "docs/results/hypotheses-m5-prompt.md",
    "docs/results/hypotheses-m6-api-authz.md",
    "docs/results/hypotheses-m6-finalize.md",
    "docs/results/hypotheses-m6-grading-r2.md",
    "docs/results/hypotheses-m6-guardrail-v2.md",
    "docs/results/hypotheses-m6-guardrail-v2-r2.md",
    "docs/results/hypotheses-m6-redteam-addendum.md",
    "docs/results/hypotheses-m6-redteam-run.md",
    "docs/results/hypotheses-test-targets.md",
)

EXCLUDED: frozenset[str] = frozenset(
    {
        "docs/results/audit-anchors.jsonl",
        "data/manifest/costs.jsonl",
        "data/manifest/costs_grading.jsonl",
    }
)


def digest_for(spec: str, root: Path) -> str:
    """Digest sorted tracked paths and their current worktree byte hashes."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", spec],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    paths = sorted(path for path in result.stdout.split("\0") if path)
    if not paths:
        raise ValueError(spec)
    rows = "".join(
        f"{path}\0{hashlib.sha256((root / path).read_bytes()).hexdigest()}\n"
        for path in paths
    )
    return hashlib.sha256(rows.encode("utf-8")).hexdigest()


def digests(root: Path) -> dict[str, str]:
    """Reject append-only paths before hashing the fixed freeze boundary."""
    overlaps = sorted(
        excluded
        for excluded in EXCLUDED
        if any(Path(excluded).is_relative_to(spec) for spec in FREEZE_PATHS)
    )
    assert not overlaps, "\n".join(overlaps)
    return {spec: digest_for(spec, root) for spec in FREEZE_PATHS}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    for spec, digest in digests(root).items():
        print(f"{spec} {digest}")


if __name__ == "__main__":
    main()

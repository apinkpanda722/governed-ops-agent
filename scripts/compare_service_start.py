"""Compare today's service.start hashes with the official N2-1 run's (read-only, hashes only).

Run after a change to a frozen path (e.g. costs.py) to show the official configuration is
unchanged: every line must print MATCH. Exits 1 if any hash differs.
"""

import hashlib
import json
import pathlib
import sys

from govops.gateway.hook_digest import compute_digests, rules_sha256_full
from govops.guardrails.policy import team_policy_sha256

OFFICIAL = pathlib.Path(
    "data/results/test/09fbcb2be83f47669800da400a401e55/attempts/T1-02-1/audit"
)


def main() -> int:
    root = pathlib.Path(".").resolve()
    digests = compute_digests(root)
    now = {
        "policy_sha256": team_policy_sha256(root / "data/policy/team_policy.json"),
        "rules_sha256": rules_sha256_full(root),
        "access_index_sha256": hashlib.sha256(
            (root / "data/seed/access_index.json").read_bytes()
        ).hexdigest(),
        "hook_rules_digest": digests.rules,
        "hook_code_digest": digests.code,
    }
    chain = sorted(OFFICIAL.glob("chain-*.jsonl"))[0]
    official = next(
        row
        for row in map(json.loads, chain.read_text().splitlines())
        if row["event"] == "service.start"
    )
    bad = [key for key, value in now.items() if official.get(key) != value]
    for key in now:
        print(("DIFF  " if key in bad else "MATCH ") + key)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

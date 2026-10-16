#!/bin/sh
set -eu

# Resolve paths from this script, even when invoked outside the repository.
root=$(CDPATH= cd "$(dirname "$0")/../.." && pwd)
cd "$root"
umask 077
work=
trap 'if [ -n "${work:-}" ]; then rm -rf -- "$work"; fi' 0
trap 'exit 1' HUP INT TERM
mkdir -p "$root/data/interim"
work=$(mktemp -d "$root/data/interim/demo-scene5-XXXXXX")
[ -n "$work" ] || exit 1

uv run python - "$root" "$work" <<'PY'
import shutil
import sys
from pathlib import Path

from govops.audit.crypto import canonical_json
from govops.audit.verify import read_anchor_lines
from govops.env import env_or


def prepare():
    root, work = (Path(value) for value in sys.argv[1:])
    source = Path(env_or("AUDIT_DIR", "data/interim/audit")).resolve()
    relative = source.relative_to(root).as_posix()
    copied = work / "chain"
    copied.mkdir()
    files = sorted(source.glob("chain-*.jsonl"))
    # chain.genesis seals the legacy files, so the copy needs them to verify.
    for path in [*sorted(source.glob("????-??-??.jsonl")), *files]:
        shutil.copyfile(path, copied / path.name)

    anchors = read_anchor_lines([
        Path(env_or("AUDIT_ANCHORS_PENDING_FILE", "data/interim/audit/anchors-pending.jsonl")),
        root / "docs/results/audit-anchors.jsonl",
    ])
    fixed = max(
        (row for row in reversed(anchors) if row["dir"] == relative),
        key=lambda row: row["seq"],
        default=None,
    )
    if fixed is None:
        print("FAIL scene5 code=anchor_not_found (고정점이 필요합니다)", file=sys.stderr)
        return 2
    if not files:
        print("FAIL scene5 code=no_chain", file=sys.stderr)
        return 1

    # Use the external anchor, not a chain.anchor found inside the chain.
    rebased = {**fixed, "dir": copied.relative_to(root).as_posix()}
    (work / "anchors.jsonl").write_text(canonical_json(rebased) + "\n", encoding="utf-8")
    return 0


try:
    status = prepare()
except (OSError, ValueError, KeyError, TypeError):
    # Never display evidence or exception messages containing file contents.
    print("FAIL scene5 code=copy_failed", file=sys.stderr)
    status = 1
raise SystemExit(status)
PY

# The copy must verify before it is cut; otherwise the cause is the copy.
uv run python -m govops.audit.verify \
    --dir "$work/chain" --anchors "$work/anchors.jsonl" --repo-root "$root" \
    > "$work/before.out" 2>&1 || :
if ! grep -q '^PASS chain' "$work/before.out" || grep -q '^FAIL' "$work/before.out"; then
    echo "FAIL scene5 code=copy_not_pass"
    grep -o 'code=[a-z0-9_]*' "$work/before.out" | sort -u
    exit 1
fi
grep '^PASS chain' "$work/before.out"

uv run python - "$root" "$work" <<'PY'
import json
import sys
from pathlib import Path

root, work = (Path(value) for value in sys.argv[1:])
fixed = json.loads((work / "anchors.jsonl").read_text(encoding="utf-8"))
copied = root / fixed["dir"]
last = sorted(copied.glob("chain-*.jsonl"))[-1]
kept = [
    line
    for line in last.read_bytes().splitlines(keepends=True)
    if json.loads(line)["seq"] < fixed["seq"]
]
last.write_bytes(b"".join(kept))
PY

# A verification failure is expected; success here requires the precise signal.
uv run python -m govops.audit.verify \
    --dir "$work/chain" --anchors "$work/anchors.jsonl" --repo-root "$root" \
    > "$work/verify.out" 2>&1 || :
cat "$work/verify.out"
if ! grep -q 'code=tail_truncated' "$work/verify.out"; then
    exit 1
fi

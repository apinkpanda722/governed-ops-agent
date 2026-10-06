"""Recompute golden TCR status from saved observations without replaying a task."""

import argparse
import json
from pathlib import Path

from govops.eval.meta import load_meta
from govops.eval.observation import Observation
from govops.eval.run_grade import REGISTERED
from govops.golden import GOLDEN_TASKS, ROOT, load_golden_specs, tcr_status

STATUS_PATH = ROOT / "data/replay/golden/status.json"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    manifest_path = args.run / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    grading_rules = manifest.get("grading_rules", REGISTERED)
    if "grading_rules" not in manifest:
        print("manifest에 grading_rules 없음 → registered로 간주")
    print(f"대상 실행의 공식 규칙으로 재계산: {grading_rules}")
    observations = {
        obs.task_id: obs
        for line in (args.run / "observations.jsonl").read_text().splitlines()
        if line.strip() and (obs := Observation.model_validate_json(line))
    }
    specs = load_golden_specs()
    meta = load_meta(ROOT / "data/eval/meta.json")
    tickets = json.loads((ROOT / "data/seed/tickets.json").read_text())["tickets"]
    users = json.loads((ROOT / "data/seed/users.json").read_text())["users"]
    rows = json.loads(STATUS_PATH.read_text())
    changes = {}
    for key in GOLDEN_TASKS:
        if key not in observations or key not in rows:
            continue
        row = rows[key]
        if not row.get("eligible", row.get("golden_eligible", False)):
            continue
        status = tcr_status(
            specs[key],
            observations[key],
            meta=meta,
            tickets=tickets,
            users=users,
            grading_rules=grading_rules,
        )
        updated = {"status": status, "recorded_fail": status == "recorded_fail"}
        before = {field: row.get(field) for field in updated}
        if before != updated:
            changes[key] = {"before": before, "after": updated}
            row.update(updated)
    print(json.dumps(changes, ensure_ascii=False, indent=2))
    if args.write and changes:
        STATUS_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

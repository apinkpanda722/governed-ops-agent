import json

import pytest
from test_eval_taskgen import generated

from govops.eval.review import ReviewFormatError, confirmed_specs, review_report
from govops.eval.spec import spec_hash


def test_reports(tmp_path):
    specs = generated(True)[0]
    path = tmp_path / "confirmed.json"
    assert confirmed_specs(specs, path) == []
    rows = {
        s.task_id: {"status": status, "spec_sha256": spec_hash(s), "note": ""}
        for s, status in zip(
            specs, ["approved", "approved", "rejected", "needs_change"]
        )
    }
    rows[specs[1].task_id]["spec_sha256"] = "0" * 64
    path.write_text(
        json.dumps({"reviewer": "검수자", "reviewed_at": "2026-10-04", "tasks": rows})
    )
    report = review_report(specs, path)
    assert report.confirmed == [specs[0].task_id]
    assert report.stale == [specs[1].task_id]
    assert report.rejected == [specs[2].task_id]
    assert report.needs_change == [specs[3].task_id]
    assert report.pending == [s.task_id for s in specs[4:]]
    assert confirmed_specs(specs, path) == specs[:1]
    path.write_text("{}")
    with pytest.raises(ReviewFormatError):
        review_report(specs, path)
    assert confirmed_specs(specs, path) == []


@pytest.mark.parametrize(
    "patch",
    [
        {"reviewer": ""},
        {"reviewed_at": "2026-99-04"},
        {
            "tasks": {
                "T1-01": {"status": "unknown", "spec_sha256": "0" * 64, "note": ""}
            }
        },
    ],
)
def test_invalid_review(tmp_path, patch):
    specs = generated(True)[0]
    path = tmp_path / "confirmed.json"
    path.write_text(
        json.dumps(
            {"reviewer": "검수자", "reviewed_at": "2026-10-04", "tasks": {}} | patch
        )
    )
    with pytest.raises(ReviewFormatError):
        review_report(specs, path)
    assert confirmed_specs(specs, path) == []

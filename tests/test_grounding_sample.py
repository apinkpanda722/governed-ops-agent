"""Synthetic M8 sampling, never evaluation files."""

from dataclasses import replace

import pytest
from test_eval_human import synthetic
from test_eval_observation import observation
from test_eval_tcr import spec

from govops.eval import human
from govops.grounding import sample
from govops.grounding.evidence import Evidence, EvidenceItem


def pool(n=30):
    return [
        sample.PoolRow(
            spec(task_id=f"T1-{i:02}", split="dev"),
            observation(task_id=f"T1-{i:02}", answer="합성 정상입니다."),
            Evidence("ok", (EvidenceItem("lookup_id", "합성 근거", 5),), 5, 0),
        )
        for i in range(n)
    ]


def test_calibration_reuses_registered_rule(monkeypatch):
    specs, obs = synthetic()
    assert sample.calibration_rows(specs, obs) == human.select_human_sample(specs, obs)
    monkeypatch.setattr(human, "sample_rule_sha256", lambda: "changed")
    with pytest.raises(ValueError, match="sample_rule"):
        sample.calibration_rows(specs, obs)


def test_pool_disjoint_unique_and_run_zero():
    specs, obs = synthetic()
    calibration = {s.task_id for s, _ in sample.calibration_rows(specs, obs)}
    dev = pool(20)
    all_specs = specs + [
        r.spec.model_copy(update={"task_id": "D" + r.spec.task_id}) for r in dev
    ]
    dev_obs = [
        r.observation.model_copy(update={"task_id": "D" + r.spec.task_id}) for r in dev
    ]
    selected = sample.heldout_pool(
        all_specs,
        obs + [obs[0].model_copy(update={"run_index": 1})],
        dev_obs,
        calibration,
    )
    assert len(selected) == 26
    assert len({r.spec.task_id for r in selected}) == 26
    assert not calibration & {r.spec.task_id for r in selected}
    with pytest.raises(ValueError):
        sample.heldout_pool(all_specs, obs, dev_obs + dev_obs[:1], calibration)
    with pytest.raises(ValueError):
        sample.heldout_pool(specs, obs, obs[:1], {obs[0].task_id})


@pytest.mark.parametrize("n,expected", [(19, None), (20, 20), (24, 24), (30, 25)])
def test_draw_minimum_seed_and_distribution(n, expected):
    rows = pool(n)
    if expected is None:
        with pytest.raises(sample.PoolInsufficient):
            sample.draw_heldout(rows)
        return
    result = sample.draw_heldout(rows)
    assert len(result) == expected
    assert result == sample.draw_heldout(list(reversed(rows)))
    assert result.type_distribution == {"T1": expected}


def test_exclusions_and_calibration_minimum():
    rows = pool(24)
    rows[0] = replace(rows[0], evidence=Evidence("evidence_missing", (), 0, 0))
    rows[1] = replace(rows[1], evidence=Evidence("evidence_oversize", (), 80001, 0))
    rows[2] = replace(
        rows[2], observation=rows[2].observation.model_copy(update={"answer": ""})
    )
    result = sample.draw_heldout(rows)
    assert len(result) == 21
    assert result.excluded == {
        "evidence_missing": 1,
        "evidence_oversize": 1,
        "deliverable_empty": 1,
    }
    with pytest.raises(sample.PoolInsufficient):
        sample.evidence_rows(rows[:20])

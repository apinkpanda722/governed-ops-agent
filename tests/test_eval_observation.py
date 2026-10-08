import json

import pytest
from pydantic import ValidationError

from govops.eval.observation import Observation, dump_observations, load_observations


def observation(**updates):
    return Observation.model_validate(
        {
            "task_id": "T1-01",
            "run_index": 0,
            "status": "completed",
            "answer": "합성 답변",
            "observed_ids": [],
            "cited_doc_ids": [],
            "tool_calls": [],
            "effects": [],
            "effect_bodies": [],
            "approvals": [],
            "policy_violations": [],
            "fault_injected": False,
            "trace_id": None,
        }
        | updates
    )


def test_roundtrip(tmp_path):
    path = tmp_path / "observations.jsonl"
    rows = [observation(), observation(run_index=1)]
    dump_observations(path, iter(rows))
    first = path.read_bytes()
    assert load_observations(path) == rows
    dump_observations(path, rows)
    assert path.read_bytes() == first
    assert list(json.loads(first.splitlines()[0])) == sorted(Observation.model_fields)


@pytest.mark.parametrize("field", ["token", "Authorization", "session_key"])
def test_no_credentials(field):
    with pytest.raises(ValidationError):
        observation(**{field: "SYN_SECRET"})


def test_safe_load_error(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text('{"answer":"SYN_SECRET"}')
    with pytest.raises(ValueError) as error:
        load_observations(path)
    assert "SYN_SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "field,record",
    [
        (
            "effects",
            {
                "kind": "slack_message",
                "target": "team-platform",
                "count": 1,
                "structure": {},
                "approval_id": None,
                "body_sha256": None,
            },
        ),
        ("tool_calls", {"name": "lookup_id", "ok": True}),
        (
            "approvals",
            {
                "approval_id": "a",
                "actor": "requester",
                "decision": "approve",
                "http_status": 200,
            },
        ),
    ],
)
def test_nested_extra_forbidden(field, record):
    with pytest.raises(ValidationError) as error:
        observation(**{field: [record | {"token": "SYN_SECRET"}]})
    assert "SYN_SECRET" not in str(error.value)


@pytest.mark.parametrize(
    "updates", [{"run_index": -1}, {"run_index": "0"}, {"status": "running"}]
)
def test_invalid_observation(updates):
    with pytest.raises(ValidationError):
        observation(**updates)


def test_stop_reason_roundtrip_and_legacy(tmp_path):
    assert observation().stop_reason is None
    row = observation(stop_reason="exploration_exhausted")
    assert Observation.model_validate_json(row.model_dump_json()) == row
    path = tmp_path / "legacy.jsonl"
    legacy = observation().model_dump()
    legacy.pop("stop_reason")
    path.write_text(json.dumps(legacy) + "\n")
    assert load_observations(path)[0].stop_reason is None
    dump_observations(path, [row])
    assert load_observations(path) == [row]


@pytest.mark.parametrize("value", [1, False, [], {}])
def test_stop_reason_strict_type(value):
    with pytest.raises(ValidationError):
        observation(stop_reason=value)

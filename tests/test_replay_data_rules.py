"""Repository recordings contain synthetic documents and no credentials."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from govops.replay.recordings import Recording, validate_recording
from govops.tools.contract import load_snapshot

ROOT = Path(__file__).parents[1]


def test_repository_recordings():
    for directory in (
        ROOT / "data" / "replay",
        ROOT / "tests" / "fixtures" / "a_recordings",
    ):
        for path in sorted(directory.rglob("*")):
            if path.suffix not in {".json", ".jsonl"}:
                continue
            content = path.read_text()
            assert "authorization" not in content.lower()
            assert "bearer " not in content.lower()
            payloads = (
                [json.loads(line) for line in content.splitlines()]
                if path.suffix == ".jsonl"
                else json.loads(content)
            )
            if path.name == "requests.json":
                payloads = [
                    {**request, "result": {}, "is_error": False}
                    for request in payloads["requests"]
                ]
            if isinstance(payloads, dict):
                payloads = [payloads]
            for payload in payloads:
                validate_recording(Recording(**payload))


def test_fixture_output_schemas():
    path = ROOT / "tests" / "fixtures" / "a_recordings" / "recordings.jsonl"
    snapshot = load_snapshot()
    for line in path.read_text().splitlines():
        entry = Recording(**json.loads(line))
        if not entry.is_error:
            Draft202012Validator(snapshot[entry.tool].output_schema).validate(
                entry.result
            )

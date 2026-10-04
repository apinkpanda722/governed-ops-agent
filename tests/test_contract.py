"""Offline checks for the immutable A MCP contract."""

import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.types import Tool

from govops.tools.contract import (
    IDENTITY_FIELD_NAMES,
    ContractMismatchError,
    diff_tools_list,
    ensure_contract,
    find_identity_fields,
    load_snapshot,
    snapshot_a_commit,
    snapshot_sha256,
)

ROOT = Path(__file__).resolve().parents[1] / "data" / "contract"
NAMES = {"search_docs", "lookup_id", "run_sql", "get_document"}


def live_dicts():
    return [
        {
            "name": tool.name,
            "description": tool.description,
            "inputSchema": deepcopy(tool.input_schema),
            "outputSchema": deepcopy(tool.output_schema),
            "_meta": {"version": tool.version},
        }
        for tool in load_snapshot().values()
    ]


def test_actual_snapshot_and_manifest():
    snapshot = load_snapshot()
    assert set(snapshot) == NAMES
    assert all(tool.version == 1 for tool in snapshot.values())
    assert snapshot_a_commit().startswith("d2eec9c")
    manifest = json.loads((ROOT / "manifest.json").read_text())
    index = json.loads((ROOT / "index.json").read_text())
    assert manifest["files"] == index["files"]
    for filename, digest in manifest["files"].items():
        assert hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("change", ["content", "delete", "manifest", "index", "count"])
def test_tampered_snapshot(tmp_path, change):
    root = tmp_path / "contract"
    shutil.copytree(ROOT, root)
    filename = "search_docs.v1.json"
    if change == "content":
        file = root / filename
        file.write_bytes(file.read_bytes().replace(b"query", b"Query", 1))
    elif change == "delete":
        (root / filename).unlink()
    else:
        file = root / ("index.json" if change == "index" else "manifest.json")
        document = json.loads(file.read_text())
        if change == "count":
            del document["files"][filename]
        else:
            document["files"][filename] = "0" * 64
        file.write_text(json.dumps(document))
    with pytest.raises(ContractMismatchError):
        load_snapshot(root)


@pytest.mark.parametrize("sdk", [False, True])
def test_equal_live_tools(sdk):
    live = live_dicts()
    # Object key order is irrelevant, including schema keys.
    live[0]["inputSchema"] = dict(reversed(list(live[0]["inputSchema"].items())))
    if sdk:
        live = [Tool(**tool) for tool in live]
    assert diff_tools_list(load_snapshot(), live) == []
    ensure_contract(load_snapshot(), live)


@pytest.mark.parametrize(
    "field",
    ["description", "inputSchema", "outputSchema", "version", "missing", "extra"],
)
@pytest.mark.parametrize("sdk", [False, True])
def test_live_differences(field, sdk):
    live = live_dicts()
    name = live[0]["name"]
    if field == "missing":
        live.pop(0)
    elif field == "extra":
        extra = deepcopy(live[0])
        name = extra["name"] = "extra_tool"
        live.append(extra)
    elif field == "version":
        live[0]["_meta"]["version"] = 2
    elif field == "description":
        live[0][field] = "changed description"
    else:
        live[0][field]["title"] = "private schema marker"
    if sdk:
        live = [Tool(**tool) for tool in live]
    differences = diff_tools_list(load_snapshot(), live)
    assert differences
    assert any(name in difference for difference in differences)
    if field not in {"missing", "extra"}:
        assert any(field in difference for difference in differences)
    assert "private schema marker" not in str(differences)
    with pytest.raises(ContractMismatchError) as error:
        ensure_contract(load_snapshot(), live)
    assert "private schema marker" not in str(error.value)


def test_identity_fields_recursive_paths():
    schema = {
        "properties": {
            "query": {"properties": {"User_ID": {"type": "string"}}},
            "sql": {"items": {"properties": {"teamName": {}}}},
            "doc_id": {"additionalProperties": {"properties": {"asUser": {}}}},
            "kind": {},
            "value": {},
            "section_key": {},
        },
        "$defs": {"record": {"properties": {"rank": {}}}},
        "anyOf": [{"properties": {"requester": {}}}],
        "oneOf": [{"properties": {"actor": {}}}],
        "allOf": [{"properties": {"grade": {}}}],
        "additionalProperties": False,
    }
    assert set(find_identity_fields(schema)) == {
        "$.properties.query.properties.User_ID: User_ID",
        "$.properties.sql.items.properties.teamName: teamName",
        "$.properties.doc_id.additionalProperties.properties.asUser: asUser",
        "$.$defs.record.properties.rank: rank",
        "$.anyOf[0].properties.requester: requester",
        "$.oneOf[0].properties.actor: actor",
        "$.allOf[0].properties.grade: grade",
    }


def test_all_identity_names_and_hyphen_normalization():
    assert IDENTITY_FIELD_NAMES == frozenset(
        [
            "user",
            "userid",
            "username",
            "team",
            "teamid",
            "teamname",
            "grade",
            "rank",
            "role",
            "asker",
            "requester",
            "actor",
            "identity",
            "asuser",
        ]
    )
    for name in IDENTITY_FIELD_NAMES | {"USER-ID", "Team_Name", "asUser"}:
        assert find_identity_fields({"properties": {name: {}}}) == [
            f"$.properties.{name}: {name}"
        ]


def test_actual_inputs_have_no_identity_fields():
    for tool in load_snapshot().values():
        assert find_identity_fields(tool.input_schema) == []


def test_snapshot_hash_is_canonical_and_deterministic():
    digest = snapshot_sha256()
    assert digest == snapshot_sha256()
    assert len(digest) == 64
    assert all(character in "0123456789abcdef" for character in digest)
    files = json.loads((ROOT / "manifest.json").read_text())["files"]
    assert (
        digest
        == hashlib.sha256(
            json.dumps(
                files, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()
    )

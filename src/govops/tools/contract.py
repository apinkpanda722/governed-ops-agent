"""Read and compare the frozen A MCP contract without network access."""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IDENTITY_FIELD_NAMES: frozenset[str] = frozenset(
    {
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
    }
)
_TOOL_NAMES = frozenset({"search_docs", "lookup_id", "run_sql", "get_document"})
_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "data" / "contract"


@dataclass(frozen=True)
class ContractTool:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    version: int


class ContractMismatchError(RuntimeError):
    """The snapshot or live tools differ from the frozen contract."""


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        raise ContractMismatchError(
            f"{path.name}: unreadable or invalid JSON"
        ) from None
    if not isinstance(value, dict):
        raise ContractMismatchError(f"{path.name}: expected an object")
    return value


def _validated_snapshot(root: Path | None) -> tuple[dict, dict[str, ContractTool]]:
    root = _DEFAULT_ROOT if root is None else root
    manifest = _read_json(root / "manifest.json")
    index = _read_json(root / "index.json")
    files = manifest.get("files")
    expected = {f"{name}.v1.json" for name in _TOOL_NAMES}
    if not isinstance(files, dict) or set(files) != expected:
        raise ContractMismatchError("manifest.json: expected the four v1 tools")
    if index.get("files") != files:
        raise ContractMismatchError("index.json: files or sha256 mismatch")
    if {path.name for path in root.glob("*.v1.json")} != expected:
        raise ContractMismatchError("snapshot: expected the four v1 files")
    commit = manifest.get("a_commit")
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(character not in "0123456789abcdef" for character in commit)
    ):
        raise ContractMismatchError("manifest.json: invalid a_commit")
    tools = {}
    for filename in sorted(files):
        path = root / filename
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            raise ContractMismatchError(f"{filename}: unreadable file") from None
        if digest != files[filename]:
            raise ContractMismatchError(f"{filename}: sha256 mismatch")
        value = _read_json(path)
        name = filename.removesuffix(".v1.json")
        if (
            value.get("name") != name
            or not isinstance(value.get("description"), str)
            or not isinstance(value.get("input_schema"), dict)
            or not isinstance(value.get("output_schema"), dict)
            or type(value.get("version")) is not int
            or value["version"] != 1
        ):
            raise ContractMismatchError(f"{filename}: invalid contract fields")
        tools[name] = ContractTool(
            name=name,
            description=value["description"],
            input_schema=value["input_schema"],
            output_schema=value["output_schema"],
            version=value["version"],
        )
    return manifest, tools


def load_snapshot(root: Path | None = None) -> dict[str, ContractTool]:
    """Validate both hash manifests against the four snapshot files."""
    return _validated_snapshot(root)[1]


def snapshot_sha256(root: Path | None = None) -> str:
    manifest, _ = _validated_snapshot(root)
    canonical = json.dumps(
        manifest["files"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def snapshot_a_commit(root: Path | None = None) -> str:
    return _validated_snapshot(root)[0]["a_commit"]


def _field(tool: Any, name: str) -> Any:
    if isinstance(tool, Mapping):
        return tool.get(name)
    # SDK versions expose either wire names or Python attribute names.
    attribute = {
        "inputSchema": "input_schema",
        "outputSchema": "output_schema",
        "_meta": "meta",
    }.get(name, name)
    return getattr(tool, name, getattr(tool, attribute, None))


def diff_tools_list(
    snapshot: Mapping[str, ContractTool], live_tools: Sequence[Any]
) -> list[str]:
    """Return field names only; never include schema or description contents."""
    differences = []
    live = {}
    for tool in live_tools:
        name = _field(tool, "name")
        if not isinstance(name, str):
            differences.append("tools/list: invalid name")
            continue
        if name in live:
            differences.append(f"{name}: duplicate name")
        live[name] = tool
    for name in sorted(snapshot.keys() - live.keys()):
        differences.append(f"{name}: missing tool")
    for name in sorted(live.keys() - snapshot.keys()):
        differences.append(f"{name}: extra tool")
    for name in sorted(snapshot.keys() & live.keys()):
        expected = snapshot[name]
        tool = live[name]
        for field, value in (
            ("description", expected.description),
            ("inputSchema", expected.input_schema),
            ("outputSchema", expected.output_schema),
        ):
            if _field(tool, field) != value:
                differences.append(f"{name}: {field} mismatch")
        meta = _field(tool, "_meta")
        version = meta.get("version") if isinstance(meta, Mapping) else None
        if type(version) is not int or version != expected.version:
            differences.append(f"{name}: version mismatch")
    return differences


def ensure_contract(
    snapshot: Mapping[str, ContractTool], live_tools: Sequence[Any]
) -> None:
    differences = diff_tools_list(snapshot, live_tools)
    if differences:
        raise ContractMismatchError("; ".join(differences))


def find_identity_fields(schema: Mapping[str, Any]) -> list[str]:
    """Inspect schema positions, without treating arbitrary metadata as fields."""
    violations = []

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, Mapping):
            return
        for keyword in ("properties", "$defs"):
            children = node.get(keyword)
            if isinstance(children, Mapping):
                for name, child in children.items():
                    child_path = f"{path}.{keyword}.{name}"
                    normalized = name.lower().replace("_", "").replace("-", "")
                    if keyword == "properties" and normalized in IDENTITY_FIELD_NAMES:
                        violations.append(f"{child_path}: {name}")
                    walk(child, child_path)
        for keyword in ("items", "additionalProperties", "anyOf", "oneOf", "allOf"):
            child = node.get(keyword)
            child_path = f"{path}.{keyword}"
            if isinstance(child, list):
                for index, item in enumerate(child):
                    walk(item, f"{child_path}[{index}]")
            else:
                walk(child, child_path)

    walk(schema, "$")
    return violations

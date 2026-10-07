"""Read the shared ledger exemption contract and capture its start state."""

import hashlib
import json
from pathlib import Path


def _ledger_paths(root: Path) -> list[str]:
    try:
        value = json.loads((root / "scripts/exempt_paths.json").read_text())
        paths = value["ledger_paths"]
        if not isinstance(value, dict) or set(value) != {"ledger_paths"}:
            raise ValueError
        if not isinstance(paths, list) or len(paths) != 1:
            raise ValueError
        if any(
            not isinstance(path, str)
            or not path
            or Path(path).is_absolute()
            or any(part in {"", ".", ".."} for part in path.split("/"))
            for path in paths
        ):
            raise ValueError
        return paths
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError("exempt_paths_invalid") from error


def dirty_entries(porcelain: str, root: Path) -> list[str]:
    paths = _ledger_paths(root)
    entries = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith('"'):
            try:
                path = json.loads(path)
            except ValueError:
                pass  # Malformed paths are never exempt.
        if path not in paths:
            entries.append(line)
    return entries


def ledger_state(root: Path) -> dict:
    path = root / _ledger_paths(root)[0]
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return {"lines": 0, "sha256": None}
    return {
        "lines": len(content.splitlines()),
        "sha256": hashlib.sha256(content).hexdigest(),
    }

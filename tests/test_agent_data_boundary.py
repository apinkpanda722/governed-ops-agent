"""Keep evaluation answers outside agent runtime code."""

import ast
from pathlib import Path

from test_architecture import find_violations


def test_agent_data_boundary():
    root = Path(__file__).resolve().parents[1] / "src/govops"
    assert not [
        v
        for v in find_violations(root)
        if v.layer == "agent" and v.target.startswith("govops.eval")
    ]
    for path in (root / "agent").rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        assert "data/eval" not in source, path
        assert "meta.json" not in source, path
        if path.suffix == ".py":
            ast.parse(source)

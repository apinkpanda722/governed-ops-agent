import ast
import sys
from pathlib import Path

ROOT = Path("src/govops/guardrails")


def test_boundary():
    for path in ROOT.glob("*"):
        if not path.is_file():
            continue
        text = path.read_text()
        assert not any(
            word in text
            for word in ("redteam", "data/eval", "meta.json", "data/replay")
        )
        if path.suffix != ".py":
            continue
        imports = [
            node
            for node in ast.walk(ast.parse(text))
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
        if path.name == "__init__.py":
            assert not imports
        for node in imports:
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names or name.startswith(
                    "govops.guardrails."
                )
                if path.name == "registry.py":
                    assert not name.startswith("govops")

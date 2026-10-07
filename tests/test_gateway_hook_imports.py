import ast
import sys
from pathlib import Path

from govops.gateway.hook_digest import HOOK_FILES

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {
    "govops.guardrails.injection",
    "govops.guardrails.pii",
    "govops.guardrails.registry",
    "govops.guardrails.screen",
    "govops.obs.mask",
    "govops.gateway.hook_digest",
    "govops.gateway.hook_header",
}


def test_import_closure_and_empty_initializers():
    for name in ("govops", "govops/gateway", "govops/obs", "govops/guardrails"):
        path = f"src/{name}/__init__.py"
        assert path in HOOK_FILES
        tree = ast.parse((ROOT / path).read_text())
        assert all(
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
            for n in tree.body
        )
    for name in ("guardrail_hook", "hook_digest", "hook_header"):
        tree = ast.parse((ROOT / f"src/govops/gateway/{name}.py").read_text())
        for node in ast.walk(tree):
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in modules:
                if module.startswith("govops."):
                    assert module in ALLOWED
                else:
                    assert module.split(".")[0] in sys.stdlib_module_names | {
                        "litellm",
                        "fastapi",
                    }
    # Adapter itself is the entry point; initializers and JSON are non-module inputs.
    modules = {
        p.removeprefix("src/").removesuffix(".py").replace("/", ".")
        for p in HOOK_FILES
        if p.endswith(".py") and not p.endswith("__init__.py")
    }
    assert modules == ALLOWED | {"govops.gateway.guardrail_hook"}
    assert len(HOOK_FILES) == len(set(HOOK_FILES))

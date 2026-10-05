"""AGENTS.md 계층 경계의 AST 검사기와 위반 탐지 회귀 테스트."""

import ast
from dataclasses import dataclass
from importlib.util import resolve_name
from pathlib import Path

import pytest

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "govops"
LLM_SDK = {"openai", "anthropic", "google.genai", "litellm"}
LAYERS = {
    "api",
    "agent",
    "tools",
    "broker",
    "gateway",
    "guardrails",
    "audit",
    "obs",
    "eval",
    "mocks",
    "replay",
    "evalrun",
}

# AGENTS.md의 계층 경계 표가 규칙의 원본이다.
FORBIDDEN_IMPORTS: dict[str, set[str]] = {
    "api": {"govops.broker", "govops.mocks", *LLM_SDK},
    "agent": {"govops.broker", "govops.mocks", "govops.eval", *LLM_SDK},
    "tools": {
        "govops.agent",
        "govops.api",
        "govops.eval",
        "govops.mocks",
        "langgraph",
        *LLM_SDK,
    },
    "broker": {
        "govops.agent",
        "govops.api",
        "govops.tools",
        "govops.gateway",
        "govops.obs",
        "govops.eval",
        "langgraph",
    },
    "gateway": {"govops.agent", "govops.tools", "govops.broker", "govops.api"},
    "guardrails": {
        "litellm",
        "langgraph",
        "fastapi",
        *(f"govops.{layer}" for layer in LAYERS - {"guardrails"}),
    },
    "audit": {"govops.agent", "govops.tools", "govops.broker", "govops.api"},
    "obs": {"govops.broker"},
    "eval": {"govops.gateway", "govops.broker"},
    "mocks": {f"govops.{layer}" for layer in LAYERS - {"mocks"}},
    "replay": {"govops.agent", "govops.api", "govops.broker"},
}

FORBIDDEN_IMPORTS["evalrun"] = {
    "govops.broker",
    "govops.mocks",
    "govops.audit",
    "govops.guardrails",
    *LLM_SDK,
}
for _layer in LAYERS - {"evalrun"}:
    FORBIDDEN_IMPORTS[_layer].add("govops.evalrun")


@dataclass(frozen=True)
class Violation:
    path: Path
    layer: str
    target: str


def find_violations(src_root: Path) -> list[Violation]:
    """계층 아래 Python 파일만 파싱하고 금지 대상과 하위 모듈을 찾는다."""
    violations = []
    for layer, forbidden in FORBIDDEN_IMPORTS.items():
        for file in sorted((src_root / layer).rglob("*.py")):
            relative = file.relative_to(src_root)
            # 일반 모듈과 __init__.py 모두 부모 디렉토리가 현재 패키지다.
            package = ".".join(("govops", *relative.parent.parts))
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        module = resolve_name("." * node.level + module, package)
                    # from govops import broker / from google import genai도 검사한다.
                    # 이미 금지된 모듈이면 가져오는 심볼 이름과 무관하게 보고한다.
                    targets = (
                        [module]
                        if _is_forbidden(module, forbidden)
                        else [f"{module}.{alias.name}" for alias in node.names]
                    )
                else:
                    continue
                for target in targets:
                    if _is_forbidden(target, forbidden):
                        violations.append(Violation(relative, layer, target))
    return violations


def _is_forbidden(target: str, forbidden: set[str]) -> bool:
    return any(
        target == module or target.startswith(module + ".") for module in forbidden
    )


def test_actual_tree_has_no_violations() -> None:
    assert find_violations(SRC_ROOT) == []


def test_rules_cover_actual_layer_directories() -> None:
    layers = {
        path.name
        for path in SRC_ROOT.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    assert set(FORBIDDEN_IMPORTS) == layers


@pytest.mark.parametrize(
    ("path", "source", "target"),
    [
        ("evalrun/dev.py", "import govops.broker", "govops.broker"),
        ("evalrun/dev.py", "import govops.mocks", "govops.mocks"),
        ("agent/graph.py", "import govops.evalrun", "govops.evalrun"),
        ("api/client.py", "import govops.broker", "govops.broker"),
        ("api/client.py", "from govops.broker import x", "govops.broker"),
        ("api/client.py", "from ..broker import z", "govops.broker"),
        ("api/client.py", "from .. import broker", "govops.broker"),
        ("api/client.py", "from govops import broker as b", "govops.broker"),
        ("api/client.py", "import openai as sdk", "openai"),
        ("api/client.py", "from google.genai import x", "google.genai"),
        ("agent/client.py", "from google import genai", "google.genai"),
        ("agent/client.py", "import anthropic.resources", "anthropic.resources"),
        ("tools/client.py", "import litellm, langgraph.graph", "litellm"),
        ("broker/client.py", "import govops.gateway.client", "govops.gateway.client"),
        ("gateway/client.py", "import govops.tools", "govops.tools"),
        ("guardrails/policy.py", "from fastapi import FastAPI", "fastapi"),
        ("guardrails/policy.py", "import govops.obs", "govops.obs"),
        ("audit/writer.py", "import govops.agent", "govops.agent"),
        ("obs/trace.py", "from ..broker import z", "govops.broker"),
        ("eval/grading.py", "import govops.gateway", "govops.gateway"),
        ("mocks/server.py", "from govops.api import x", "govops.api"),
        ("replay/server.py", "from .. import api", "govops.api"),
        ("api/nested/client.py", "from ...broker import z", "govops.broker"),
        ("api/nested/__init__.py", "from ... import broker", "govops.broker"),
        (
            "api/client.py",
            "def deferred():\n    import govops.broker\n",
            "govops.broker",
        ),
    ],
)
def test_forbidden_imports_are_detected(
    tmp_path: Path, path: str, source: str, target: str
) -> None:
    file = tmp_path / path
    file.parent.mkdir(parents=True)
    file.write_text(source, encoding="utf-8")
    assert Violation(Path(path), path.split("/")[0], target) in find_violations(
        tmp_path
    )


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("gateway/client.py", "import litellm, openai, anthropic, google.genai"),
        ("eval/grading.py", "from google import genai"),
        ("eval/grading.py", "from google.genai import Client"),
        ("api/client.py", "import govops.api.auth"),
        ("api/client.py", "from . import auth"),
        ("api/nested/client.py", "from ..auth import session"),
        ("api/nested/__init__.py", "from .. import auth"),
        ("guardrails/policy.py", "from govops.guardrails import patterns"),
        ("mocks/server.py", "from . import jira"),
        ("api/client.py", "import google.auth, openai_helpers, govops.brokerage"),
        ("api/client.py", "from govops import costs"),
        ("costs.py", "import govops.broker, openai"),
        ("api/client.py", 'message = "import govops.broker"'),
        ("api/client.py", "raise RuntimeError('must never execute')"),
    ],
)
def test_allowed_imports_and_non_imports(
    tmp_path: Path, path: str, source: str
) -> None:
    file = tmp_path / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(source, encoding="utf-8")
    assert find_violations(tmp_path) == []


def test_collects_all_imports_in_all_files(tmp_path: Path) -> None:
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "first.py").write_text(
        "import openai, anthropic\nfrom .. import broker, mocks\n",
        encoding="utf-8",
    )
    (tmp_path / "api" / "second.py").write_text("import litellm\n", encoding="utf-8")
    assert set(find_violations(tmp_path)) == {
        Violation(Path("api/first.py"), "api", "openai"),
        Violation(Path("api/first.py"), "api", "anthropic"),
        Violation(Path("api/first.py"), "api", "govops.broker"),
        Violation(Path("api/first.py"), "api", "govops.mocks"),
        Violation(Path("api/second.py"), "api", "litellm"),
    }

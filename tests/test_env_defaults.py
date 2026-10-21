"""Environment defaults must survive empty dotenv assignments."""

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def env_call(node):
    return isinstance(node, ast.Call) and ast.unparse(node.func) in {
        "os.getenv",
        "os.environ.get",
    }


def nonempty(node):
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and bool(node.value)
    )


def violations(source):
    found = []
    for node in ast.walk(ast.parse(source)):
        if env_call(node):
            defaults = node.args[1:] + [
                kw.value for kw in node.keywords if kw.arg == "default"
            ]
            if any(nonempty(value) for value in defaults):
                found.append(node.lineno)
        if (
            isinstance(node, ast.BoolOp)
            and isinstance(node.op, ast.Or)
            and any(env_call(value) for value in node.values)
            and any(nonempty(value) for value in node.values)
        ):
            found.append(node.lineno)
    return found


def test_no_unsafe_environment_defaults():
    assert {
        str(p.relative_to(ROOT)): violations(p.read_text())
        for p in (ROOT / "src/govops").rglob("*.py")
        if violations(p.read_text())
    } == {}


@pytest.mark.parametrize(
    "source",
    [
        'os.getenv("X", "value")',
        'os.environ.get("X", default="value")',
        'os.getenv("X") or "value"',
        'x or os.getenv("X") or "value"',
    ],
)
def test_scanner_detects_defaults(source):
    assert violations(source)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "fallback"),
        ("", "fallback"),
        (" \t\n", "fallback"),
        ("value", "value"),
        (" value ", "value"),
    ],
)
def test_env_or(monkeypatch, value, expected):
    from govops.env import env_or

    monkeypatch.delenv("TEST_ENV_DEFAULT", raising=False)
    if value is not None:
        monkeypatch.setenv("TEST_ENV_DEFAULT", value)
    assert env_or("TEST_ENV_DEFAULT", "fallback") == expected


@pytest.mark.parametrize(
    "relative,name,default",
    [
        ("rollout/store.py", "GOVOPS_ROLLOUT_DIR", "data/interim/rollout"),
        ("tools/smoke.py", "MOCK_JIRA_URL", "http://127.0.0.1:8081"),
        ("tools/smoke.py", "MOCK_SLACK_URL", "http://127.0.0.1:8082"),
        ("tools/smoke.py", "SMOKE_USER", "demo-platform-g3"),
        (
            "audit/anchor.py",
            "AUDIT_ANCHORS_PENDING_FILE",
            "data/interim/audit/anchors-pending.jsonl",
        ),
        ("audit/log.py", "AUDIT_DIR", "data/interim/audit"),
        ("audit/crypto.py", "AUDIT_KEY_FILE", "data/interim/audit_ed25519.pem"),
        ("audit/crypto.py", "AUDIT_PUBLIC_KEY_FILE", "data/seed/audit_public_key.pem"),
        ("evalrun/dev_m3.py", "A_ROOT", "~/PycharmProjects/ko-docs-agentic-rag"),
        ("obs/jsonl.py", "GOVOPS_TRACE_DIR", str(ROOT / "logs/traces")),
        ("mocks/__main__.py", "MOCK_JIRA_TICKETS", "data/seed/tickets.json"),
        ("mocks/__main__.py", "MOCK_JIRA_OVERLAY", ""),
    ],
)
@pytest.mark.parametrize("value", ["", " \t", " custom-value "])
def test_variable_defaults(monkeypatch, relative, name, default, value):
    from govops.env import env_or

    monkeypatch.setenv(name, value)
    tree = ast.parse((ROOT / "src/govops" / relative).read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "env_or"
        and isinstance(n.args[0], ast.Constant)
        and n.args[0].value == name
    ]
    assert calls
    for call in calls:
        actual = eval(
            compile(ast.Expression(call), relative, "eval"),
            {
                "env_or": env_or,
                "DEMO_USERS": ("demo-platform-g3",),
                "default": Path(default),
            },
        )
        assert actual == (value.strip() or default)

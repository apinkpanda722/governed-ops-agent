"""작업 트리 전체의 청결 검사는 예외 목록(`evalrun/worktree.py`)을 거쳐야 한다(FP-064)."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src/govops"


def porcelain_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and any(
            isinstance(arg, ast.Constant) and arg.value == "--porcelain"
            for arg in node.args
        ):
            yield node


def is_path_scoped(call):
    # `git status --porcelain -- <경로>`처럼 `--` 뒤에 경로를 한정한 호출.
    return any(isinstance(arg, ast.Constant) and arg.value == "--" for arg in call.args)


def uses_exemptions(tree):
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dirty_entries"
        for node in ast.walk(tree)
    )


def violations(source, name="<memory>"):
    tree = ast.parse(source)
    calls = [c for c in porcelain_calls(tree) if not is_path_scoped(c)]
    if calls and not uses_exemptions(tree):
        return [f"{name}:{c.lineno}" for c in calls]
    return []


def test_whole_tree_status_checks_apply_the_exemption_list():
    found = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "worktree.py":
            continue  # 예외 목록을 구현하는 모듈 자신
        problems = violations(path.read_text(encoding="utf-8"), str(path.name))
        if problems:
            found[str(path.relative_to(SRC))] = problems
    assert found == {}


def test_scanner_flags_an_unscoped_status_check_without_exemptions():
    source = 'git("status", "--porcelain")\n'
    assert violations(source) == ["<memory>:1"]


def test_scanner_accepts_path_scoped_and_exempted_checks():
    assert violations('git("status", "--porcelain", "--", path)\n') == []
    exempted = (
        'code, out = git(root, "status", "--porcelain")\n'
        "worktree.dirty_entries(out, root)\n"
    )
    assert violations(exempted) == []

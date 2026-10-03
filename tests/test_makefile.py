"""공통 검증 진입점의 Python·web 검사 순서."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_verify_recipe_runs_all_checks_in_order() -> None:
    lines = (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
    start = lines.index("verify:") + 1
    recipe = []
    for line in lines[start:]:
        if line and not line.startswith("\t"):
            break
        recipe.append(line.strip())

    commands = [line for line in recipe if line.startswith(("uv run ", "pnpm -C "))]
    assert commands == [
        "uv run ruff check .",
        "uv run ruff format --check .",
        "uv run pytest -q",
        "pnpm -C web typecheck",
        "pnpm -C web lint",
        "pnpm -C web test",
    ]

"""공통 검증 진입점의 Python·web 검사 순서."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_db_init_is_idempotent_and_user_run_only() -> None:
    text = (REPO_ROOT / "Makefile").read_text()
    assert ".PHONY: db-init" in text
    recipe = text.split("\ndb-init:\n", 1)[1].split("\n\n", 1)[0]
    assert (
        "docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres"
        in recipe
    )
    assert "CREATE DATABASE govops" in recipe
    assert "WHERE NOT EXISTS" in recipe
    assert "pg_database" in recipe
    assert "\\gexec" in recipe
    assert "ON_ERROR_STOP=1" in recipe
    verify = text.split("verify:\n", 1)[1].split("\n\n", 1)[0]
    assert "db-init" not in verify


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


def test_user_run_targets_are_separate():
    text = (REPO_ROOT / "Makefile").read_text()
    live = [
        "record-a",
        "smoke-contract",
        "smoke-identity",
        "smoke-visibility",
        "smoke-mock",
        "smoke-host-header",
    ]
    for name in ["mock-tokens", "seed-extract", "eval-meta-extract", *live]:
        assert "\n" + name + ":\n" in text
        recipe = text.split("\n" + name + ":\n", 1)[1].splitlines()[0]
        assert "uv run " in recipe
        if name in live:
            assert "uv run --env-file .env " in recipe
    verify = text.split("verify:\n", 1)[1].split("\n\n", 1)[0]
    assert all(name not in verify for name in [*live, "eval-meta-extract"])
    assert "eval-meta-extract" in text.split(".PHONY:", 2)[-1]
    assert ".PHONY: verify\n" in text
    assert "SMOKE_USER ?= demo-platform-g3" in text


def test_agent_key_is_user_run_only():
    text = (REPO_ROOT / "Makefile").read_text()
    assert (
        "\nagent-key:\n\tuv run --env-file .env python -m govops.gateway.agent_key\n"
        in text
    )
    verify = text.split("verify:\n", 1)[1].split("\n\n", 1)[0]
    assert "agent-key" not in verify


def test_serve_and_ask_are_user_run_only():
    text = (REPO_ROOT / "Makefile").read_text()
    assert "# compose·live A 기동 후, DEMO_LOGIN=true 필요\nserve:\n" in text
    assert "uv run --env-file .env python -m govops.api\n" in text
    assert (
        'uv run --env-file .env python -m govops.api.cli ask --as $(AS) "$(Q)"' in text
    )
    verify = text.split("verify:\n", 1)[1].split("\n\n", 1)[0]
    assert "serve" not in verify and "ask" not in verify


def test_api_smokes_are_user_run_only():
    text = (REPO_ROOT / "Makefile").read_text()
    for name in ("checkpointer", "agent", "obs"):
        assert (
            f"\nsmoke-{name}:\n\tuv run --env-file .env python -m govops.api.smoke {name}\n"
            in text
        )
    verify = text.split("verify:\n", 1)[1].split("\n\n", 1)[0]
    assert "smoke-checkpointer" not in verify and "smoke-agent" not in verify


def test_every_docker_compose_command_passes_the_env_file() -> None:
    # compose가 .env의 변수를 읽지 못해 내는 WARN을 막는다.
    text = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    commands = [line for line in text.splitlines() if "docker compose" in line]
    assert commands
    assert all("docker compose --env-file .env " in line for line in commands)


def test_eval_gen_is_user_run_only():
    text = (REPO_ROOT / "Makefile").read_text()
    assert ".PHONY: eval-gen" in text
    assert (
        "# meta.json 필요, 사용자 실행\neval-gen:\n\tuv run python -m govops.eval.taskgen\n"
        in text
    )
    assert "eval-gen" not in text.split("verify:\n", 1)[1].split("\n\n", 1)[0]


def test_review_targets_are_user_only():
    text = (REPO_ROOT / "Makefile").read_text()
    for target, comment, command in [
        (
            "eval-cards",
            "# 읽기 전용 A 저장소(A_ROOT) 필요, 사용자 실행",
            "cards --a-root $(A_ROOT)",
        ),
        ("eval-review-status", "# 사용자 실행", "status"),
    ]:
        assert (
            f"{comment}\n{target}:\n\tuv run python -m govops.eval.review_cli {command}"
            in text
        )
        assert target in text.split(".PHONY:")[-1]
        assert target not in text.split("verify:\n")[1].split("\n\n")[0]


def test_grading_targets_user_only():
    text = (REPO_ROOT / "Makefile").read_text()
    for name, command in [
        ("eval-human-sheet", "human-sheet"),
        ("eval-human-register", "register-human"),
        ("eval-gemini-grade", "grade"),
        ("smoke-gemini", "smoke"),
    ]:
        assert (
            f"# 사용자 실행\n{name}:\n\tuv run --env-file .env python -m govops.eval.grading_cli {command}"
            in text
        )
        assert name in text.split(".PHONY:")[-1]
        assert name not in text.split("verify:\n")[1].split("\n\n")[0]


def test_obs_targets_user_only():
    text = (REPO_ROOT / "Makefile").read_text()
    for name in ("obs-up", "obs-down"):
        assert f"# 사용자 실행\n{name}:" in text
        assert name not in text.split("verify:\n")[1].split("\n\n")[0]
    assert (
        "LITELLM_CONFIG_FILE=./litellm/config.obs.yaml docker compose --env-file .env"
        in text
    )
    assert (
        "--profile obs stop langfuse-web langfuse-worker clickhouse redis minio" in text
    )
    assert "up -d litellm" in text
    # config.obs.yaml의 내용이 바뀌어도 LiteLLM이 새 설정을 읽도록 obs-up은 항상 재생성한다.
    recipe = text.split("\nobs-up:\n", 1)[1].split("\n\n", 1)[0]
    assert (
        "--force-recreate" in recipe
        and "litellm" in recipe.split("--force-recreate")[1]
    )


def test_cost_report_is_read_only_without_env_file():
    text = (REPO_ROOT / "Makefile").read_text()
    assert ".PHONY: cost-report" in text
    assert (
        "# 원장 읽기 전용, 네트워크 불필요\ncost-report:\n\tuv run python -m govops.costs report\n"
        in text
    )
    recipe = text.split("\ncost-report:\n")[1].split("\n\n")[0]
    assert "--env-file" not in recipe  # 키 없는 user-run 타깃의 명시적 예외
    assert "cost-report" not in text.split("verify:\n")[1].split("\n\n")[0]


def test_smoke_obs_phony_and_user_only():
    text = (REPO_ROOT / "Makefile").read_text()
    assert ".PHONY: smoke-obs" in text
    assert "smoke-obs" not in text.split("verify:\n")[1].split("\n\n")[0]
    assert (
        "# compose 핵심 스택 + make obs-up + live A + Langfuse 키 필요, 사용자 실행"
        in text
    )


def test_eval_run_dev_user_only():
    text = (REPO_ROOT / "Makefile").read_text()
    assert ".PHONY: eval-run-dev" in text
    assert (
        "# live A·LiteLLM·make agent-key·hypotheses.md 커밋 필요, 사용자 실행\neval-run-dev:\n\tuv run --env-file .env python -m govops.evalrun.dev"
        in text
    )
    assert "eval-run-dev" not in text.split("verify:\n")[1].split("\n\n")[0]
    assert "record-llm:" not in text

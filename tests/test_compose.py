"""compose의 로컬 바인드·비밀 참조·DB 분리를 기동 없이 검사한다."""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "infra/docker-compose.yml"
CORE_SERVICES = {"postgres", "litellm", "mock-jira", "mock-slack"}
OBS_SERVICES = {"langfuse-web", "langfuse-worker", "clickhouse", "redis", "minio"}


@pytest.fixture
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_core_services_exist(compose: dict) -> None:
    assert compose["name"] == "govops"
    assert {"postgres", "litellm"} <= compose["services"].keys()


@pytest.mark.parametrize("name", sorted(OBS_SERVICES))
def test_observability_services_require_obs_profile(compose: dict, name: str) -> None:
    assert "obs" in compose["services"][name]["profiles"]


@pytest.mark.parametrize("name", sorted(CORE_SERVICES))
def test_core_services_are_independent_of_obs(compose: dict, name: str) -> None:
    service = compose["services"][name]
    assert "profiles" not in service
    assert not OBS_SERVICES.intersection(service.get("depends_on", {}))


def test_only_one_postgres_service_exists(compose: dict) -> None:
    services = compose["services"]
    assert set(services) == CORE_SERVICES | OBS_SERVICES
    postgres_services = {
        name
        for name, service in services.items()
        if "postgres" in name.lower() or "postgres" in service.get("image", "").lower()
    }
    assert postgres_services == {"postgres"}


@pytest.mark.parametrize("name", ["langfuse-web", "langfuse-worker"])
def test_langfuse_uses_existing_postgres_database(compose: dict, name: str) -> None:
    service = compose["services"][name]
    assert service["environment"]["DATABASE_URL"] == (
        "postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/langfuse"
    )
    assert service["depends_on"]["postgres"]["condition"] == "service_healthy"


def test_only_langfuse_web_publishes_obs_ports(compose: dict) -> None:
    assert compose["services"]["langfuse-web"]["ports"] == ["127.0.0.1:3000:3000"]
    for name in OBS_SERVICES - {"langfuse-web"}:
        assert not compose["services"][name].get("ports"), name


def test_all_ports_bind_to_loopback(compose: dict) -> None:
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            assert isinstance(port, str) and port.startswith("127.0.0.1:"), name
    assert compose["services"]["postgres"]["ports"] == ["127.0.0.1:5432:5432"]
    assert compose["services"]["litellm"]["ports"] == ["127.0.0.1:4000:4000"]


def test_secret_environment_values_are_references(compose: dict) -> None:
    for name, service in compose["services"].items():
        environment = service.get("environment", {})
        if isinstance(environment, list):
            environment = dict(entry.partition("=")[::2] for entry in environment)
        for variable, value in environment.items():
            if re.search(r"PASSWORD|KEY|SECRET|SALT|TOKEN|AUTH$", variable):
                # 실패 시 값 대신 서비스·변수 이름만 보고한다.
                assert isinstance(value, str), (name, variable)
                assert re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*(?::[-?][^}]*)?\}", value), (
                    name,
                    variable,
                )


def test_images_have_fixed_tags_and_verification_comments(compose: dict) -> None:
    for name, service in compose["services"].items():
        if "build" in service:
            continue
        image = service["image"]
        if "@sha256:" in image:
            # 태그를 제공하지 않는 이미지는 digest로 고정한다.
            assert re.fullmatch(r"[^@:]+(:[^@]+)?@sha256:[0-9a-f]{64}", image), name
            continue
        assert ":" in image.rsplit("/", 1)[-1], name
        assert image.rsplit(":", 1)[-1] not in {"", "latest"}, name
    lines = COMPOSE_PATH.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if re.match(r"\s*image:", line):
            assert index > 0
            assert lines[index - 1].lstrip().startswith("#")
            assert "공식 릴리스·보안 공지 확인(" in lines[index - 1]


def test_all_compose_variables_are_declared_in_env_example() -> None:
    variables = set(
        re.findall(
            r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::[-?][^}]*)?\}",
            COMPOSE_PATH.read_text(encoding="utf-8"),
        )
    )
    declared = set(
        re.findall(
            r"^([A-Z][A-Z0-9_]*)=",
            (REPO_ROOT / ".env.example").read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    )
    assert variables <= declared, f".env.example 누락 변수: {variables - declared}"


def test_postgres_storage_healthcheck_and_credentials(compose: dict) -> None:
    postgres = compose["services"]["postgres"]
    assert postgres["environment"] == {
        "POSTGRES_USER": "${POSTGRES_USER}",
        "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}",
    }
    volumes = postgres["volumes"]
    data_volume = next(v for v in volumes if v.endswith(":/var/lib/postgresql/data"))
    assert data_volume.partition(":")[0] in compose["volumes"]
    assert "./postgres/init:/docker-entrypoint-initdb.d:ro" in volumes
    assert "pg_isready" in " ".join(postgres["healthcheck"]["test"])


def test_litellm_config_mount_dependencies_and_environment(compose: dict) -> None:
    litellm = compose["services"]["litellm"]
    assert (
        "${LITELLM_CONFIG_FILE:-./litellm/config.yaml}:/app/config.yaml:ro"
        in litellm["volumes"]
    )
    assert litellm["command"] == ["--config", "/app/config.yaml", "--port", "4000"]
    assert litellm["depends_on"]["postgres"]["condition"] == "service_healthy"
    variables = {
        "LITELLM_MASTER_KEY",
        "LITELLM_DATABASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    }
    assert litellm["environment"] == {
        "PYTHONPATH": "/app/src",
        "PYTHONDONTWRITEBYTECODE": "1",
        **{v: "${" + v + "}" for v in variables},
        "LANGFUSE_HOST": "http://langfuse-web:3000",
        "LANGFUSE_PUBLIC_KEY": "${LANGFUSE_PUBLIC_KEY:-}",
        "LANGFUSE_SECRET_KEY": "${LANGFUSE_SECRET_KEY:-}",
    }


def test_init_script_creates_litellm_langfuse_and_govops_databases() -> None:
    script = (REPO_ROOT / "infra/postgres/init/01-create-databases.sh").read_text(
        encoding="utf-8"
    )
    assert re.findall(r"CREATE DATABASE\s+([a-z_]+)\s*;", script) == [
        "litellm",
        "langfuse",
        "govops",
    ]
    assert '"$POSTGRES_USER"' in script
    assert "ON_ERROR_STOP=1" in script
    assert "checkpointer" not in script.lower()


def test_litellm_has_primary_model_and_environment_settings() -> None:
    path = REPO_ROOT / "infra/litellm/config.yaml"
    text = path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    assert len(config["model_list"]) == 4
    model = config["model_list"][0]
    assert model["model_name"]
    assert model["litellm_params"] == {
        "model": "openai/gpt-6-luna",
        "api_key": "os.environ/OPENAI_API_KEY",
        "timeout": 15,
        "num_retries": 0,
    }
    assert config["general_settings"] == {
        "master_key": "os.environ/LITELLM_MASTER_KEY",
        "database_url": "os.environ/LITELLM_DATABASE_URL",
    }
    assert "Guardrail은 M4" not in text
    assert len(config["guardrails"]) == 2


@pytest.mark.parametrize(("name", "port"), [("jira", 8081), ("slack", 8082)])
def test_mock_build_command_and_ports(compose: dict, name: str, port: int) -> None:
    service = compose["services"][f"mock-{name}"]
    assert service["build"] == {
        "context": "..",
        "dockerfile": "infra/mocks/Dockerfile",
    }
    assert service["command"] == ["python", "-m", "govops.mocks", name]
    assert service["ports"] == [f"127.0.0.1:{port}:8000"]
    assert "9000" in service["expose"]
    assert not service.get("environment")


def test_no_service_publishes_admin_port(compose: dict) -> None:
    for name, service in compose["services"].items():
        for port in service.get("ports", []):
            target = port.rsplit(":", 1)[-1].split("/", 1)[0]
            assert target != "9000", name


def test_mock_dockerfile_has_pinned_images_and_frozen_install() -> None:
    text = (REPO_ROOT / "infra/mocks/Dockerfile").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert re.search(r"^FROM python:3\.12\.\d+-slim-trixie$", text, re.MULTILINE)
    assert re.search(r"--from=ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+", text)
    for index, line in enumerate(lines):
        if line.startswith("FROM ") or "--from=ghcr.io/astral-sh/uv:" in line:
            assert index > 0
            assert lines[index - 1].startswith("#")
            assert "공식 릴리스 확인(" in lines[index - 1]
        assert not line.startswith(("CMD ", "ENTRYPOINT "))
    assert "uv sync --frozen --no-dev" in text
    assert "/app/.venv/bin" in text


def test_dockerignore_excludes_private_and_unneeded_directories() -> None:
    patterns = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert {".venv", "web/", "data/", ".git", ".env*"} <= set(patterns)


def test_jira_seed_is_mounted_read_only(compose: dict) -> None:
    assert (
        "../data/seed:/app/data/seed:ro" in compose["services"]["mock-jira"]["volumes"]
    )


def test_slack_seed_is_mounted_read_only(compose: dict) -> None:
    assert (
        "../data/seed:/app/data/seed:ro" in compose["services"]["mock-slack"]["volumes"]
    )


def test_obs_config_and_env():
    base = yaml.safe_load((REPO_ROOT / "infra/litellm/config.yaml").read_text())
    obs = yaml.safe_load((REPO_ROOT / "infra/litellm/config.obs.yaml").read_text())
    assert obs["model_list"] == base["model_list"]
    assert "litellm_settings" not in base
    assert obs["litellm_settings"] == {
        "success_callback": ["langfuse"],
        "failure_callback": ["langfuse"],
        "turn_off_message_logging": True,
    }
    text = (REPO_ROOT / ".env.example").read_text()
    for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        assert f"{key}=\n" in text


def test_gateway_fallback_configs_match():
    from pathlib import Path

    import yaml

    configs = [
        yaml.safe_load(Path(f"infra/litellm/{name}").read_text())
        for name in ("config.yaml", "config.obs.yaml")
    ]
    assert configs[0]["model_list"] == configs[1]["model_list"]
    expected = [
        {"agent-primary": ["agent-fallback"]},
        {"agent-primary-down": ["agent-fallback"]},
    ]
    for config in configs:
        assert config["router_settings"]["fallbacks"] == expected
        models = {m["model_name"]: m["litellm_params"] for m in config["model_list"]}
        assert set(models) == {
            "agent-primary",
            "agent-fallback",
            "agent-primary-only",
            "agent-primary-down",
        }
        for name in ("agent-primary", "agent-primary-only", "agent-primary-down"):
            assert models[name]["model"] == "openai/gpt-6-luna"
            assert models[name]["timeout"] == 15
            assert models[name]["num_retries"] == 0
        assert models["agent-fallback"]["model"] == "anthropic/claude-sonnet-5-5"
        assert models["agent-fallback"]["api_key"] == "os.environ/ANTHROPIC_API_KEY"
        assert models["agent-fallback"]["timeout"] == 30
        assert models["agent-primary-down"]["api_base"] == "http://127.0.0.1:9"
        assert models["agent-primary-only"] == models["agent-primary"]


def test_guardrail_mounts_and_configs(compose):
    service = compose["services"]["litellm"]
    for mount in (
        "../src:/app/src:ro",
        "../data/seed/users.json:/app/data/seed/users.json:ro",
    ):
        assert mount in service["volumes"]
        assert (REPO_ROOT / "infra" / mount.split(":")[0]).exists()
    assert not any("data/policy" in mount for mount in service["volumes"])
    configs = [
        yaml.safe_load((REPO_ROOT / "infra/litellm" / name).read_text())
        for name in ("config.yaml", "config.obs.yaml")
    ]
    for key in ("guardrails", "model_list", "router_settings"):
        assert configs[0][key] == configs[1][key]
    assert len(configs[0]["guardrails"]) == 2
    assert {g["litellm_params"]["mode"] for g in configs[0]["guardrails"]} == {
        "pre_call",
        "post_call",
    }
    for guardrail in configs[0]["guardrails"]:
        assert (
            guardrail["litellm_params"]["guardrail"]
            == "govops.gateway.guardrail_hook.GovopsGuardrail"
        )
        assert guardrail["litellm_params"]["default_on"] is True

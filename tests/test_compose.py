"""compose의 로컬 바인드·비밀 참조·DB 분리를 기동 없이 검사한다."""

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "infra/docker-compose.yml"


@pytest.fixture
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


def test_core_services_exist(compose: dict) -> None:
    assert compose["name"] == "govops"
    assert {"postgres", "litellm"} <= compose["services"].keys()


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
            if re.search(r"PASSWORD|KEY|SECRET|SALT|TOKEN", variable):
                # 실패 시 값 대신 서비스·변수 이름만 보고한다.
                assert isinstance(value, str), (name, variable)
                assert re.fullmatch(r"\$\{[A-Z][A-Z0-9_]*(?::[-?][^}]*)?\}", value), (
                    name,
                    variable,
                )


def test_images_have_fixed_tags_and_verification_comments(compose: dict) -> None:
    for name, service in compose["services"].items():
        image = service["image"]
        assert ":" in image.rsplit("/", 1)[-1], name
        assert image.rsplit(":", 1)[-1] not in {"", "latest"}, name
    lines = COMPOSE_PATH.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if re.match(r"\s*image:", line):
            assert index > 0
            assert lines[index - 1].lstrip().startswith("#")
            assert "확인 필요" in lines[index - 1]


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
    assert "./litellm/config.yaml:/app/config.yaml:ro" in litellm["volumes"]
    assert litellm["command"] == ["--config", "/app/config.yaml", "--port", "4000"]
    assert litellm["depends_on"]["postgres"]["condition"] == "service_healthy"
    variables = {
        "LITELLM_MASTER_KEY",
        "LITELLM_DATABASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    }
    assert litellm["environment"] == {v: "${" + v + "}" for v in variables}


def test_init_script_creates_only_litellm_and_langfuse_databases() -> None:
    script = (REPO_ROOT / "infra/postgres/init/01-create-databases.sh").read_text(
        encoding="utf-8"
    )
    assert re.findall(r"CREATE DATABASE\s+([a-z_]+)\s*;", script) == [
        "litellm",
        "langfuse",
    ]
    assert '"$POSTGRES_USER"' in script
    assert "ON_ERROR_STOP=1" in script
    assert "checkpointer" not in script.lower()


def test_litellm_has_one_primary_model_and_environment_settings() -> None:
    path = REPO_ROOT / "infra/litellm/config.yaml"
    text = path.read_text(encoding="utf-8")
    config = yaml.safe_load(text)
    assert len(config["model_list"]) == 1
    model = config["model_list"][0]
    assert model["model_name"]
    assert model["litellm_params"] == {
        "model": "openai/gpt-6-luna",
        "api_key": "os.environ/OPENAI_API_KEY",
    }
    assert config["general_settings"] == {
        "master_key": "os.environ/LITELLM_MASTER_KEY",
        "database_url": "os.environ/LITELLM_DATABASE_URL",
    }
    assert "확인 필요(구현 시 공식 문서로 확인): 모델 ID·가격" in text
    assert "Guardrail은 M4" in text

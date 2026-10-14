"""Read-only environment checks; external operations use injected adapters.

LiteLLM GET /model/info returns {"data": [{"model_info": {"id": ...}}]}.
Authorization is Bearer auth; an LLM-only virtual key can receive 403.
Sources: https://docs.litellm.ai/docs/proxy/model_management
https://github.com/BerriAI/litellm/issues/20581
Compose's build-only image name is <project>-<service>; this config names
its project govops, hence govops-mock-jira and govops-mock-slack.
"""

import re
from datetime import datetime

from govops.costs import CLASSIFIER_DEPLOYMENTS, DEPLOYMENT_MODELS
from govops.env import env_or
from govops.gateway.hook_digest import compute_digests
from govops.gateway.hook_header import GuardrailInfo, parse_header
from govops.gateway.team_keys import TeamKeyResolver, load_teams, team_env_name


def judge_guardrail_probe(status, headers, expected) -> bool:
    info = parse_header(headers.get("x-govops-guardrail"))
    return bool(
        status == 400
        and info is not None
        and info.rules == expected.rules
        and info.code == expected.code
        and info.team == expected.team
        and info.pre == "probe"
        and info.c == "probe"
    )


def required_deployments(*, v2: bool = False) -> set[str]:
    # The deliberately dead deployment is not a cost-bearing response mapping.
    primary = next(
        key
        for key in DEPLOYMENT_MODELS
        if key.startswith("openai-") and not key.endswith("-only")
    )
    return (
        set(DEPLOYMENT_MODELS)
        | {primary + "-down"}
        | (set(CLASSIFIER_DEPLOYMENTS) if v2 else set())
    )


def mock_images(root, env=env_or) -> list[str]:
    config = (root / "infra/docker-compose.yml").read_text()
    project = re.search(r"^name:\s*(\S+)", config, re.MULTILINE)
    if not project:
        raise ValueError("compose_project_missing")
    name = env("COMPOSE_PROJECT_NAME", project.group(1))
    images = []
    for service in ("mock-jira", "mock-slack"):
        block = re.search(
            rf"^  {service}:\n(.*?)(?=^  \S|\Z)", config, re.MULTILINE | re.DOTALL
        )
        if not block or not re.search(r"^    build:", block.group(1), re.MULTILINE):
            raise ValueError("mock_build_missing")
        image = re.search(r"^    image:\s*(\S+)", block.group(1), re.MULTILINE)
        images.append(image.group(1) if image else f"{name}-{service}")
    return images


def run_preflight(*, root, runner, http, env=env_or, mock_admin, a_check) -> list[str]:
    """Return only safe failure codes; never retain HTTP bodies or exceptions.

    runner(['postgres', 'SELECT 1']) is a logical DB probe, implemented by the
    live adapter with psycopg (the DSN never enters a command or its output).
    """
    failures = []
    try:
        commit = runner(
            ["git", "log", "-1", "--format=%cI", "--", "src/govops/mocks/"], cwd=root
        )
        if commit.returncode:
            raise ValueError()
        changed = datetime.fromisoformat(commit.stdout.strip())
        for image in mock_images(root, env):
            result = runner(
                ["docker", "image", "inspect", "--format", "{{.Created}}", image],
                cwd=root,
            )
            if (
                result.returncode
                or datetime.fromisoformat(result.stdout.strip()) <= changed
            ):
                raise ValueError()
    except Exception:  # noqa: BLE001 — command output can contain secrets
        failures.append("stale_mock_image")
    base = env("LITELLM_BASE_URL", "http://127.0.0.1:4000").rstrip("/")
    down = False
    try:
        teams = load_teams(root / "data/seed/users.json")
        resolver = TeamKeyResolver(
            {team: team for team in teams},
            {team_env_name(team): env(team_env_name(team), "") for team in teams},
        )
        missing = not teams or bool(resolver.missing())
    except Exception:  # noqa: BLE001 — seed and environment errors are private
        missing = True
    if missing:
        failures.append("team_keys_missing")
    else:
        headers = {"Authorization": "Bearer " + resolver.key_for(teams[0])}
        try:
            health = http.get(base + "/health/liveliness", timeout=10)
            down = health.status_code != 200
            response = http.get(base + "/model/info", headers=headers, timeout=10)
            if response.status_code == 404:
                response = http.get(
                    base + "/v1/model/info", headers=headers, timeout=10
                )
            if response.status_code != 200:
                down = True
            else:
                try:
                    ids = {row["model_info"]["id"] for row in response.json()["data"]}
                    if (
                        not required_deployments(
                            v2=env("GOVOPS_GUARDRAIL_V2", "off") == "on"
                        )
                        <= ids
                    ):
                        failures.append("litellm_config_stale")
                except (ValueError, TypeError, KeyError):
                    failures.append("litellm_config_stale")
        except Exception:  # noqa: BLE001 — HTTP exceptions include request headers
            down = True
        if down:
            failures = [code for code in failures if code != "litellm_config_stale"]
            failures.append("service_down:litellm")
        else:
            try:
                digests = compute_digests(root)
                expected = GuardrailInfo(digests.rules, digests.code, team=teams[0])
                response = http.post(
                    base + "/v1/chat/completions",
                    headers=headers,
                    timeout=10,
                    json={
                        "model": "agent-primary",
                        "metadata": {"govops_probe": "digest"},
                        "messages": [{"role": "user", "content": "합성 점검"}],
                    },
                )
                valid = judge_guardrail_probe(
                    response.status_code, response.headers, expected
                )
            except Exception:  # noqa: BLE001 — no response or headers are retained
                valid = False
            if not valid:
                failures.append("guardrail_hook_stale")
    try:
        if runner(["postgres", "SELECT 1"], cwd=root).returncode:
            raise ValueError()
    except Exception:  # noqa: BLE001 — DB details are private
        failures.append("service_down:postgres")
    try:
        health = mock_admin.health()
    except Exception:  # noqa: BLE001
        health = {}
    for name in ("mock-jira", "mock-slack"):
        if not health.get(name):
            failures.append("service_down:" + name)
    try:
        if a_check() is False:
            raise ValueError()
    except Exception:  # noqa: BLE001 — contract errors may contain responses
        failures.append("service_down:a-mcp")
    return failures

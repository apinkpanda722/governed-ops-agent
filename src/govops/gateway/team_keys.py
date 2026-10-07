"""사용자 실행용 팀 키 관리. 비밀은 env 파일과 요청 헤더에만 둔다."""

import argparse
import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

import httpx

from govops.env import env_or
from govops.gateway.agent_key import AGENT_KEY_MODELS, _empty, _write_atomic
from govops.gateway.client import _IN_CALL, GatewayError, _protect_transport_logs

# 사용량 귀속용 비구속 예산. 실제 상한은 ADR-007의 B 총액 원장이 강제한다.
TEAM_MAX_BUDGET_USD = 1000.0


def load_teams(path: str | Path = "data/seed/users.json") -> tuple[str, ...]:
    return tuple(
        sorted({row["team"] for row in json.loads(Path(path).read_bytes())["users"]})
    )


def team_env_name(team: str) -> str:
    return "LITELLM_TEAM_KEY_" + team.upper().replace("-", "_")


class TeamKeyResolver:
    def __init__(self, users: Mapping[str, str], env: Mapping[str, str] = os.environ):
        self._users = dict(users)
        self._env = env

    def key_for(self, user_id: str) -> str:
        team = self._users.get(user_id)
        key = self._env.get(team_env_name(team)) if team else None
        if not key or not key.strip():
            raise GatewayError("team_key_missing")
        return key.strip()

    def missing(self) -> list[str]:
        return sorted(
            team
            for team in set(self._users.values())
            if not team or not (self._env.get(team_env_name(team)) or "").strip()
        )


def _assignment(name: str):
    return re.compile(
        rb"^([ \t]*(?:export[ \t]+)?" + name.encode() + rb"[ \t]*=)([^\r\n]*)(\r?\n)?$"
    )


def _file_key(content: bytes, name: str) -> str | None:
    for line in content.splitlines(keepends=True):
        match = _assignment(name).fullmatch(line)
        if match and not _empty(match[2]):
            value = match[2].decode().strip()
            if value.startswith('"'):
                value = json.loads(value)
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            if value.strip():
                return value
    return None


def _store(content: bytes, name: str, key: str) -> bytes:
    encoded = (
        key
        if re.fullmatch(r"[A-Za-z0-9_-]+", key)
        else json.dumps(key, ensure_ascii=False)
    ).encode()
    lines = content.splitlines(keepends=True)
    matches = [_assignment(name).fullmatch(line) for line in lines]
    if any(matches):
        return b"".join(
            match[1] + encoded + (match[3] or b"") if match else line
            for line, match in zip(lines, matches, strict=True)
        )
    return (
        content
        + (b"" if not content or content.endswith(b"\n") else b"\n")
        + name.encode()
        + b"="
        + encoded
        + b"\n"
    )


def _fail(code: str) -> int:
    print("FAIL " + code)
    return 1


def main(
    argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--update", action="store_true")
    parser.add_argument("--rotate", action="store_true")
    parser.add_argument(
        "--base-url", default=env_or("LITELLM_BASE_URL", "http://127.0.0.1:4000")
    )
    args = parser.parse_args(argv)
    master = os.getenv("LITELLM_MASTER_KEY")
    if not master or not master.strip():
        return _fail("missing_master_key")
    path = Path(args.env_file)
    stage = "env_read"
    _protect_transport_logs()
    token = _IN_CALL.set(True)
    try:
        original = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
        content = original
        with httpx.Client(
            base_url=args.base_url.rstrip("/"),
            headers={"Authorization": "Bearer " + master},
            transport=transport,
            timeout=30,
            trust_env=False,
        ) as client:
            for team in load_teams():
                stage = "team_info"
                response = client.get("/team/info", params={"team_id": team})
                if response.status_code == 404:
                    stage = "team_new"
                    response = client.post(
                        "/team/new",
                        json={
                            "team_id": team,
                            "team_alias": team,
                            "models": list(AGENT_KEY_MODELS),
                            "max_budget": TEAM_MAX_BUDGET_USD,
                        },
                    )
                    if response.status_code != 200:
                        return _fail("team_new_http")
                elif response.status_code != 200:
                    return _fail("team_info_http")
                name = team_env_name(team)
                key = (os.getenv(name) or "").strip() or _file_key(content, name)
                if args.update:
                    if not key:
                        return _fail("team_key_missing:" + team)
                    for stage, payload in (
                        (
                            "team_update",
                            {"team_id": team, "models": list(AGENT_KEY_MODELS)},
                        ),
                        ("key_update", {"key": key, "models": list(AGENT_KEY_MODELS)}),
                    ):
                        if (
                            client.post(
                                "/" + stage.replace("_", "/"), json=payload
                            ).status_code
                            != 200
                        ):
                            return _fail(stage + "_http")
                elif not key:
                    alias = "team-" + team
                    if args.rotate:
                        stage = "key_delete"
                        if (
                            client.post(
                                "/key/delete", json={"key_aliases": [alias]}
                            ).status_code
                            != 200
                        ):
                            return _fail("key_delete_http")
                    stage = "key_generate"
                    response = client.post(
                        "/key/generate",
                        json={
                            "team_id": team,
                            "models": list(AGENT_KEY_MODELS),
                            "key_alias": alias,
                        },
                    )
                    if response.status_code == 400:
                        body = response.json()
                        error = (
                            body.get("error", body) if isinstance(body, dict) else {}
                        )
                        if (
                            isinstance(error, dict)
                            and error.get("param") == "key_alias"
                        ):
                            return _fail("key_exists_unrecoverable:" + team)
                    if response.status_code != 200:
                        return _fail("key_generate_http")
                    body = response.json()
                    key = body.get("key") if isinstance(body, dict) else None
                    if not isinstance(key, str) or not key.strip():
                        return _fail("key_generate_format")
                    content = _store(content, name, key)
                    stage = "env_write"
                    _write_atomic(path, content, mode)
                print("PASS " + team + (" update" if args.update else " ready"))
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        httpx.HTTPError,
        httpx.InvalidURL,
    ):
        return _fail(stage)
    finally:
        _IN_CALL.reset(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

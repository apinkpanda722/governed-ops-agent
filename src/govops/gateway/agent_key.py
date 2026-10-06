"""사용자 실행 전용 가상 키 발급 CLI. 비밀은 환경 변수와 env 파일에만 둔다."""

import argparse
import json
import os
import re
import stat
import tempfile
from pathlib import Path

import httpx

from govops.env import env_or
from govops.gateway.client import _IN_CALL, _protect_transport_logs

AGENT_KEY_MODELS = (
    "agent-primary",
    "agent-primary-only",
    "agent-primary-down",
    "agent-fallback",
)

_ASSIGNMENT = re.compile(
    rb"^([ \t]*(?:export[ \t]+)?LITELLM_AGENT_KEY[ \t]*=)([^\r\n]*)(\r?\n)?$"
)


def _fail(message: str) -> int:
    print(message)
    return 1


def _empty(value: bytes) -> bool:
    value = value.strip()
    return value in (b"", b'""', b"''") or value.startswith(b"#")


def _write_atomic(path: Path, content: bytes, mode: int) -> None:
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".agent-key-")
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
            os.fchmod(file.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def _update(master: str, base_url: str, transport: httpx.BaseTransport | None) -> int:
    key = os.getenv("LITELLM_AGENT_KEY")
    if not key or not key.strip():
        return _fail("FAIL missing_agent_key")
    _protect_transport_logs()
    token = _IN_CALL.set(True)
    try:
        with httpx.Client(transport=transport, timeout=30, trust_env=False) as client:
            response = client.post(
                base_url.rstrip("/") + "/key/update",
                headers={"Authorization": "Bearer " + master},
                json={"key": key, "models": list(AGENT_KEY_MODELS)},
            )
        if response.status_code != 200:
            return _fail(f"FAIL http_{response.status_code}")
    except (httpx.HTTPError, httpx.InvalidURL):
        return _fail("FAIL connection")
    finally:
        _IN_CALL.reset(token)
    print("PASS agent-key-update models=" + ",".join(AGENT_KEY_MODELS))
    return 0


def main(
    argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None
) -> int:
    parser = argparse.ArgumentParser(description="에이전트 가상 키를 env 파일에 기록")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--update", action="store_true", help="기존 키의 허용 모델 갱신"
    )
    parser.add_argument(
        "--base-url", default=env_or("LITELLM_BASE_URL", "http://127.0.0.1:4000")
    )
    args = parser.parse_args(argv)
    master = os.getenv("LITELLM_MASTER_KEY")
    if not master or not master.strip():
        return _fail(
            "FAIL missing_master_key"
            if args.update
            else "마스터 키 환경 변수가 없습니다"
        )
    if args.update:
        return _update(master, args.base_url, transport)
    if os.getenv("LITELLM_AGENT_KEY"):
        return _fail("LITELLM_AGENT_KEY가 이미 설정됨")
    path = Path(args.env_file)
    try:
        original = path.read_bytes()
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return _fail("env 파일 없음 또는 읽기 실패")
    lines = original.splitlines(keepends=True)
    matches = [_ASSIGNMENT.fullmatch(line) for line in lines]
    if any(match and not _empty(match[2]) for match in matches):
        return _fail("LITELLM_AGENT_KEY가 이미 설정됨")

    _protect_transport_logs()
    token = _IN_CALL.set(True)
    try:
        with httpx.Client(transport=transport, timeout=30, trust_env=False) as client:
            response = client.post(
                args.base_url.rstrip("/") + "/key/generate",
                headers={"Authorization": "Bearer " + master},
                json={"models": list(AGENT_KEY_MODELS), "key_alias": "govops-agent"},
            )
        if response.status_code != 200:
            return _fail(f"HTTP 상태 코드 {response.status_code}")
        body = response.json()
        key = body.get("key") if isinstance(body, dict) else None
        if not isinstance(key, str) or not key:
            return _fail("응답 형식 오류")
    except (httpx.HTTPError, httpx.InvalidURL):
        return _fail("연결 실패")
    except (ValueError, TypeError):
        return _fail("응답 형식 오류")
    finally:
        _IN_CALL.reset(token)

    # Quote unusual keys so control characters cannot create env assignments.
    encoded = (
        key
        if re.fullmatch(r"[A-Za-z0-9_-]+", key)
        else json.dumps(key, ensure_ascii=False)
    ).encode("utf-8")
    if any(matches):
        content = b"".join(
            match[1] + encoded + (match[3] or b"") if match else line
            for line, match in zip(lines, matches, strict=True)
        )
    else:
        separator = b"" if not original or original.endswith(b"\n") else b"\n"
        content = original + separator + b"LITELLM_AGENT_KEY=" + encoded + b"\n"
    try:
        _write_atomic(path, content, mode)
    except OSError:
        return _fail("env 파일 저장 실패")
    print("LITELLM_AGENT_KEY를 .env에 기록했습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

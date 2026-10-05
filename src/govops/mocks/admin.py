"""docker compose exec <mock> python -m govops.mocks.admin health."""

import argparse
import json
import sys
from pathlib import Path

import httpx


def main(
    argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9000")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="관리 포트의 healthz 조회")
    state = commands.add_parser("state", help="생성된 부작용의 해시·메타데이터 조회")
    state.add_argument("--bodies", action="store_true")
    faults = commands.add_parser("faults", help="결정론 장애 규칙 관리")
    fault_commands = faults.add_subparsers(dest="fault_command", required=True)
    fault_set = fault_commands.add_parser("set")
    fault_set.add_argument("--file", type=Path, required=True)
    fault_commands.add_parser("clear")
    fault_commands.add_parser("stats")
    commands.add_parser(
        "reset", help="서비스 초기 상태로 복원(jira 시드 / slack 빈 메시지)"
    )
    args = parser.parse_args(argv)

    try:
        with httpx.Client(transport=transport, timeout=5.0, trust_env=False) as client:
            path = "healthz" if args.command == "health" else args.command
            method = "POST" if args.command == "reset" else "GET"
            kwargs = {}
            if args.command == "state" and args.bodies:
                path += "?bodies=true"
            if args.command == "faults":
                path = "faults"
                if args.fault_command == "set":
                    method = "POST"
                    kwargs["json"] = json.loads(args.file.read_text(encoding="utf-8"))
                elif args.fault_command == "clear":
                    method = "DELETE"
                else:
                    path = "faults/stats"
            response = client.request(
                method, f"{args.url.rstrip('/')}/{path}", **kwargs
            )
            response.raise_for_status()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(f"관리 {args.command} 요청 실패: {exc}", file=sys.stderr)
        return 1
    print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

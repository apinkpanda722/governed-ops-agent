"""docker compose exec <mock> python -m govops.mocks.admin health."""

import argparse
import sys

import httpx


def main(
    argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:9000")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("health", help="관리 포트의 healthz 조회")
    args = parser.parse_args(argv)

    try:
        with httpx.Client(transport=transport, timeout=5.0, trust_env=False) as client:
            response = client.get(f"{args.url.rstrip('/')}/healthz")
            response.raise_for_status()
    except httpx.HTTPError as exc:
        print(f"관리 health 조회 실패: {exc}", file=sys.stderr)
        return 1
    print(response.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

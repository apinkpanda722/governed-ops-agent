"""컨테이너 한 프로세스에서 REST·관리 서버를 함께 실행한다."""

import argparse
import asyncio
import sys
from pathlib import Path

import uvicorn

from govops.env import env_or
from govops.mocks.auth import TokenHashStore
from govops.mocks.faults import FaultH11Protocol
from govops.mocks.jira import JiraState
from govops.mocks.seed import load_tickets, merge_overlay
from govops.mocks.service import create_admin_app, create_rest_app
from govops.mocks.slack import SlackState


async def _serve(
    service: str,
    state: JiraState | SlackState | None = None,
    token_store: TokenHashStore | None = None,
) -> None:
    # 0.0.0.0 바인드는 컨테이너 안에서만 사용한다.
    rest = uvicorn.Server(
        uvicorn.Config(
            create_rest_app(service, state=state, token_store=token_store),
            host="0.0.0.0",
            port=8000,
            http=FaultH11Protocol,
        )
    )
    admin = uvicorn.Server(
        uvicorn.Config(
            create_admin_app(service, state=state), host="0.0.0.0", port=9000
        )
    )
    await asyncio.gather(rest.serve(), admin.serve())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("jira", "slack"))
    args = parser.parse_args(argv)
    state = None
    token_store = None
    if args.service == "jira":
        try:
            token_store = TokenHashStore.load(
                Path(
                    env_or(
                        "MOCK_JIRA_TOKEN_HASHES",
                        "data/seed/mock_jira_token_hashes.json",
                    )
                )
            )
            tickets = load_tickets(
                Path(env_or("MOCK_JIRA_TICKETS", "data/seed/tickets.json"))
            )
            overlay = env_or("MOCK_JIRA_OVERLAY", "")
            tickets = merge_overlay(tickets, Path(overlay) if overlay else None)
            state = JiraState(tickets)
        except (OSError, ValueError):
            print(
                "mock-jira startup failed: missing or invalid MOCK_JIRA_TOKEN_HASHES / MOCK_JIRA_TICKETS / MOCK_JIRA_OVERLAY files",
                file=sys.stderr,
            )
            return 1
    if args.service == "slack":
        try:
            token_store = TokenHashStore.load(
                Path(
                    env_or(
                        "MOCK_SLACK_TOKEN_HASHES",
                        "data/seed/mock_slack_token_hashes.json",
                    )
                )
            )
            state = SlackState()
        except (OSError, ValueError):
            print(
                "mock-slack startup failed: missing or invalid MOCK_SLACK_TOKEN_HASHES file",
                file=sys.stderr,
            )
            return 1
    asyncio.run(_serve(args.service, state, token_store))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

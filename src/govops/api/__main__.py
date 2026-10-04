"""Loopback-only session service CLI."""

import argparse
import os

import uvicorn

from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.startup import verify_contract
from govops.api.users import load_users
from govops.tools.a_mcp import AMcpClient


def build_server_config(port: int) -> uvicorn.Config:
    client = AMcpClient(os.environ["KODOCS_MCP_URL"])

    async def contract_check():
        await verify_contract(client)

    app = create_app(
        users=load_users(),
        sessions=SessionStore(),
        demo_login=os.environ.get("DEMO_LOGIN", "").lower() == "true",
        startup_checks=(contract_check,),
    )
    return uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback session API")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.Server(build_server_config(args.port)).run()


if __name__ == "__main__":
    main()

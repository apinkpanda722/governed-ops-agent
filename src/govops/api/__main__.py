"""Loopback-only session service CLI."""

import argparse
import os
from contextlib import asynccontextmanager

import uvicorn

from govops.api.app import create_app
from govops.api.sessions import SessionStore
from govops.api.tasks import TaskService
from govops.api.users import load_users
from govops.api.wiring import open_task_service


def build_server_config(port: int) -> uvicorn.Config:
    app = create_app(
        users=load_users(),
        sessions=SessionStore(),
        demo_login=os.environ.get("DEMO_LOGIN", "").lower() == "true",
        service=TaskService(None),
    )

    @asynccontextmanager
    async def lifespan(app):
        async with open_task_service() as service:
            app.state.task_service = service
            # 인증 실패 감사(record_auth_failure)도 실제 서비스의 감사 로그를 쓴다(FP-070).
            app.state.audit = getattr(service, "audit", None)
            yield

    app.router.lifespan_context = lifespan
    return uvicorn.Config(app, host="127.0.0.1", port=port, access_log=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback session API")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    uvicorn.Server(build_server_config(args.port)).run()


if __name__ == "__main__":
    main()

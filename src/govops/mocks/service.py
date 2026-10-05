"""Mock REST·Compose 내부 관리 앱 팩토리."""

from fastapi import FastAPI

from govops.mocks import slack
from govops.mocks.auth import TokenHashStore
from govops.mocks.faults import FaultMiddleware, FaultState
from govops.mocks.faults import add_admin_routes as add_fault_routes
from govops.mocks.jira import JiraState, add_admin_routes, add_rest_routes
from govops.mocks.slack import SlackState


def _create_health_app(service: str) -> FastAPI:
    if service not in {"jira", "slack"}:
        raise ValueError("service must be jira or slack")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": f"mock-{service}"}

    return app


def create_rest_app(
    service: str,
    *,
    state: JiraState | SlackState | None = None,
    token_store: TokenHashStore | None = None,
    faults: FaultState | None = None,
) -> FastAPI:
    """호스트의 에이전트 서비스가 접근하는 REST 앱."""
    app = _create_health_app(service)
    if state is not None and faults is not None:
        state.faults = faults
    if service == "jira" and state is not None:
        if token_store is None:
            raise ValueError("jira business REST requires a token hash store")
        add_rest_routes(app, state, token_store)
    if service == "slack" and state is not None:
        if token_store is None:
            raise ValueError("slack business REST requires a token hash store")
        slack.add_rest_routes(app, state, token_store)
    if state is not None:
        app.add_middleware(
            FaultMiddleware, faults=faults if faults is not None else state.faults
        )
    return app


def create_admin_app(
    service: str,
    *,
    state: JiraState | SlackState | None = None,
    faults: FaultState | None = None,
) -> FastAPI:
    """Compose 내부 전용 관리 앱."""
    app = _create_health_app(service)
    if state is not None:
        if faults is not None:
            state.faults = faults
        add_fault_routes(app, state.faults)
    if service == "jira" and state is not None:
        add_admin_routes(app, state)
    if service == "slack" and state is not None:
        slack.add_admin_routes(app, state)
    return app

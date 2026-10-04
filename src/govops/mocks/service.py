"""Mock REST·Compose 내부 관리 앱 팩토리."""

from fastapi import FastAPI

from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState, add_admin_routes, add_rest_routes


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
    state: JiraState | None = None,
    token_store: TokenHashStore | None = None,
) -> FastAPI:
    """호스트의 에이전트 서비스가 접근하는 REST 앱."""
    app = _create_health_app(service)
    if service == "jira" and state is not None:
        if token_store is None:
            raise ValueError("jira business REST requires a token hash store")
        add_rest_routes(app, state, token_store)
    return app


def create_admin_app(service: str, *, state: JiraState | None = None) -> FastAPI:
    """Compose 내부 전용 관리 앱."""
    app = _create_health_app(service)
    if service == "jira" and state is not None:
        add_admin_routes(app, state)
    return app

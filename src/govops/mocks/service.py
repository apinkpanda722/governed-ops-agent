"""업무 API 구현 전 REST·관리 앱의 healthz 골격."""

from fastapi import FastAPI


def _create_health_app(service: str) -> FastAPI:
    if service not in {"jira", "slack"}:
        raise ValueError("service must be jira or slack")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "service": f"mock-{service}"}

    return app


def create_rest_app(service: str) -> FastAPI:
    """호스트의 에이전트 서비스가 접근하는 REST 앱."""
    return _create_health_app(service)


def create_admin_app(service: str) -> FastAPI:
    """Compose 내부 전용 관리 앱. 현재는 healthz만 제공한다."""
    return _create_health_app(service)

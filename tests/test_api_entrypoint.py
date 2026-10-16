"""실제 서버 진입점(`api/__main__.build_server_config`)을 거친 라우트 연결 검사.

진입점은 자리표시자 서비스(`TaskService(None)`)로 앱을 만들고 lifespan에서 `app.state.task_service`를
실제 서비스로 바꾼다. 라우트가 create_app 클로저의 `service`를 붙잡으면 생성 시점의 자리표시자를 쓰게 된다.
`create_app(service=실제)`로만 만든 앱에서는 클로저와 app.state가 같아서 이 결함이 드러나지 않았다(FP-070).
"""

import ast
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from starlette.testclient import TestClient

import govops.api.__main__ as entry
from govops.audit.log import MemoryAuditLog

APP_SOURCE = Path(entry.__file__).with_name("app.py")
ID = "0123456789abcdef0123456789abcdef"
USER = "demo-platform-g3"


class FakeService:
    """진입점 lifespan이 app.state에 넣는 실제 서비스를 대신한다. 호출된 메서드를 기록한다."""

    def __init__(self):
        self.calls = []
        self.audit = MemoryAuditLog()

    async def start(self, user_id, request):
        self.calls.append("start")
        return ID

    async def start_conversation(self, user_id, request):
        self.calls.append("start_conversation")
        return {"conversation_id": ID, "turn_id": ID}

    async def add_turn(self, user_id, conversation_id, request):
        self.calls.append("add_turn")
        return {"conversation_id": conversation_id, "turn_id": ID}

    async def conversation_view(self, user_id, conversation_id):
        self.calls.append("conversation_view")
        return {"conversation_id": conversation_id}

    async def delete_conversation(self, user_id, conversation_id):
        self.calls.append("delete_conversation")
        return {"conversation_id": conversation_id, "deleted": True}

    async def audit_view(self, user_id, conversation_id):
        self.calls.append("audit_view")
        return {"conversation_id": conversation_id, "turns": [], "truncated": False}

    async def audit_verify(self):
        self.calls.append("audit_verify")
        return {"status": "pass"}

    async def conversations(self, user_id, limit):
        self.calls.append("conversations")
        return {"conversations": []}

    async def approvals(self, user_id):
        self.calls.append("approvals")
        return {"approvals": []}

    async def approve(self, user_id, task_id, **kwargs):
        self.calls.append("approve")
        return 200, {"task_id": task_id}

    async def get(self, user_id, task_id):
        self.calls.append("get")
        return {"task_id": task_id, "status": "completed"}

    async def progress(self, user_id, conversation_id):
        self.calls.append("progress")  # None = 대화 없음(SSE는 404)


# (메서드, 경로 형태) -> (요청 본문, 기대하는 서비스 메서드)
EXPECTED = {
    ("GET", "/conversations"): (None, "conversations"),
    ("POST", "/tasks"): ({"request": "q"}, "start"),
    ("POST", "/tasks/{task_id}/approval"): (
        {"decision": "approve", "draft_sha256": "a" * 64},
        "approve",
    ),
    ("GET", "/tasks/{task_id}"): (None, "get"),
    ("POST", "/conversations"): ({"request": "q"}, "start_conversation"),
    ("POST", "/conversations/{conversation_id}/turns"): (
        {"request": "q"},
        "add_turn",
    ),
    ("GET", "/conversations/{conversation_id}"): (None, "conversation_view"),
    ("DELETE", "/conversations/{conversation_id}"): (None, "delete_conversation"),
    ("GET", "/conversations/{conversation_id}/events"): (None, "progress"),
    ("GET", "/conversations/{conversation_id}/audit"): (None, "audit_view"),
    ("GET", "/audit/verify"): (None, "audit_verify"),
    ("GET", "/approvals"): (None, "approvals"),
}


def service_routes(app):
    return {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.path.startswith(("/tasks", "/conversations", "/approvals", "/audit"))
        for method in route.methods
    }


def test_every_service_route_uses_the_service_installed_by_lifespan(monkeypatch):
    fake = FakeService()

    @asynccontextmanager
    async def fake_open_task_service():
        yield fake

    monkeypatch.setenv("DEMO_LOGIN", "true")
    monkeypatch.setattr(entry, "open_task_service", fake_open_task_service)
    config = entry.build_server_config(0)
    app = config.app
    routes = service_routes(app)
    # 새 라우트가 생기면 이 표에 같이 넣어야 한다(실제 진입점 경유 호출이 없는 라우트를 허용하지 않는다).
    assert routes == set(EXPECTED)
    token, _ = app.state.sessions.issue(USER)
    headers = {"Authorization": "Bearer " + token}
    with TestClient(app, raise_server_exceptions=False) as client:
        for (method, path), (body, called) in EXPECTED.items():
            before = len(fake.calls)
            url = path.replace("{task_id}", ID).replace("{conversation_id}", ID)
            response = client.request(method, url, headers=headers, json=body)
            assert response.status_code != 500, (method, path)
            assert called in fake.calls[before:], (method, path)


def violations(source):
    """create_app 안의 중첩 함수가 클로저 이름 `service`를 읽는 위치. lifespan의 aclose는 허용한다."""
    tree = ast.parse(source)
    create_app = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "create_app"
    )
    found = []
    for node in ast.walk(create_app):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node is not create_app
            and node.name != "lifespan"
        ):
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "service":
                    found.append((node.name, child.lineno))
    return found


def test_route_handlers_never_read_the_create_app_closure_service():
    assert violations(APP_SOURCE.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        (
            "def create_app(service=None):\n"
            "    @app.get('/x')\n"
            "    async def route():\n"
            "        return await service.get()\n"
        ),
        (
            "def create_app(service=None):\n"
            "    @app.get('/x')\n"
            "    async def route(tasks=Depends(lambda: service)):\n"
            "        return tasks\n"
        ),
    ],
)
def test_guard_detects_a_route_that_captures_the_closure_service(source):
    assert violations(source)


def test_guard_allows_the_closure_service_in_lifespan_and_create_app_body():
    source = (
        "def create_app(service=None):\n"
        "    async def lifespan(app):\n"
        "        await service.aclose()\n"
        "    app.state.task_service = service\n"
        "    if service is not None:\n"
        "        @app.get('/x')\n"
        "        async def route(request):\n"
        "            return request.app.state.task_service\n"
    )
    assert violations(source) == []


def test_auth_failure_audit_uses_the_audit_log_of_the_service_installed_by_lifespan(
    monkeypatch,
):
    # create_app이 자리표시자 서비스에서 복사해 둔 `app.state.audit`(None)이 남으면
    # 실제 서버에서 `auth.failed`가 하나도 기록되지 않는다.
    fake = FakeService()

    @asynccontextmanager
    async def fake_open_task_service():
        yield fake

    monkeypatch.setenv("DEMO_LOGIN", "true")
    monkeypatch.setattr(entry, "open_task_service", fake_open_task_service)
    app = entry.build_server_config(0).app
    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/sessions/me").status_code == 401
    assert [r["event"] for r in fake.audit.records] == ["auth.failed"]
    assert fake.audit.records[0]["reason"] == "missing_credentials"

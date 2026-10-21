import asyncio

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from govops.api import smoke_conversation as smoke


def stub(fault=None):
    app = FastAPI()
    turns = {"p": 1, "f": 1, "b": 1}
    approved = set()

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def route(path: str, request: Request):
        finance = request.headers.get("authorization", "").endswith("finance")
        body = await request.json() if request.method == "POST" else {}
        if path == "sessions":
            return {
                "session_token": "synthetic-finance"
                if "finance" in body["user"]
                else "synthetic-platform"
            }
        if path == "conversations" and request.method == "POST":
            cid = "b" if smoke.SENTINEL in body["request"] else "f" if finance else "p"
            return JSONResponse({"conversation_id": cid, "turn_id": cid + "1"}, 202)
        if path == "approvals":
            return {
                "approvals": []
                if finance or turns["p"] in approved
                else [{"task_id": "p2"}]
            }
        if path.startswith("conversations/"):
            cid = path.split("/")[1]
            n = turns[cid]
            if path.endswith("/turns"):
                if n == 2 and n not in approved:
                    return JSONResponse(
                        {"detail": "approval_pending"},
                        202 if fault == "pending" else 409,
                    )
                turns[cid] += 1
                return JSONResponse({"turn_id": cid + str(turns[cid])}, 202)
            if finance and cid == "p":
                return JSONResponse({}, 200 if fault == "owner" else 404)
            state = {
                "task_id": cid + str(n),
                "status": "completed",
                "answer": "synthetic-answer",
                "observed_doc_ids": [smoke.EXPECTED_DOC]
                if cid == "p" or fault == "finance_leak"
                else [],
                "cited_doc_ids": [],
                "request": None,
            }
            if cid == "f" and fault == "finance_running":
                state["status"] = "failed"
            if cid == "b":
                state.update(
                    status="blocked_input",
                    block={"code": "pii_input", "message": "blocked"},
                )
            if cid == "p" and n > 1:
                state.update(
                    status="completed" if n in approved else "awaiting_approval",
                    draft={
                        "body": "synthetic-draft",
                        "args": {
                            "channel": "team-finance"
                            if fault == "target"
                            else "team-platform"
                        },
                    },
                    draft_sha256=("a" if n == 2 or fault == "inherited" else "b") * 64,
                )
                if n in approved:
                    state["effect"] = {"mock_id": str(n)}
            result = {"turn": state, "history": []}
            if fault == "sentinel":
                result["nested"] = [smoke.SENTINEL]
            if fault == "timeout":
                state["status"] = "running"
            return result
        if path.startswith("tasks/"):
            tid = path.split("/")[1]
            if tid == "p1" or tid == "p2" and turns["p"] == 3:
                return JSONResponse({}, 404)
            n = int(tid[-1])
            approved.add(n)
            return {
                "status": "completed",
                "effect": None if fault == "effect" else {"mock_id": str(n)},
            }
        return JSONResponse({}, 404)

    return app


@pytest.mark.parametrize(
    "fault,name",
    [
        (None, None),
        ("target", "share_targets_own_team_channel"),
        ("pending", "approval_pending_409"),
        ("effect", "approve_sends_once"),
        ("owner", "other_user_404"),
        ("finance_leak", "finance_no_leak_no_send"),
        ("inherited", "second_write_separate_effect"),
        ("sentinel", "no_leak_in_views"),
        ("finance_running", "finance_no_leak_no_send"),
        ("timeout", "rag_turn1"),
    ],
)
def test_contract(fault, name, capsys):
    code = asyncio.run(
        smoke.run(
            transport=httpx.ASGITransport(app=stub(fault)),
            timeout=0.01 if fault == "timeout" else 1,
            interval=0.001,
        )
    )
    output = capsys.readouterr().out
    assert code == (0 if fault is None else 1)
    assert ("RESULT pass" if fault is None else "RESULT fail") in output
    if fault is None:
        assert "PASS share_targets_own_team_channel" in output
    if name:
        assert f"FAIL {name} " in output
    assert all(
        value not in output
        for value in [
            "synthetic-platform",
            "synthetic-finance",
            "synthetic-answer",
            smoke.REQUEST,
            smoke.SENTINEL,
        ]
    )


class Boom(Exception):
    pass


@pytest.mark.parametrize(
    "handler,exception_name",
    [
        (
            lambda request: (_ for _ in ()).throw(
                httpx.ConnectError("http://127.0.0.1:8000 secret-token-probe")
            ),
            "ConnectError",
        ),
        (lambda request: httpx.Response(200, json={}), "KeyError"),
        (lambda request: (_ for _ in ()).throw(Boom("secret-body-probe")), "Boom"),
    ],
)
def test_no_views_is_a_failure_and_names_only_the_exception_class(
    handler, exception_name, capsys
):
    # 받은 view가 0개이면 `all([])`로 통과하면 안 된다(공허한 PASS).
    code = asyncio.run(
        smoke.run(transport=httpx.MockTransport(handler), timeout=1, interval=0.001)
    )
    output = capsys.readouterr().out
    assert code == 1
    assert f"FAIL rag_turn1 request_failed:{exception_name}" in output
    assert "FAIL no_leak_in_views no_views" in output
    assert "PASS no_leak_in_views" not in output
    assert "RESULT fail" in output
    # 예외 클래스 이름 외에 본문·URL·토큰·예외 문장이 출력에 없다.
    for value in ("127.0.0.1", "secret-token-probe", "secret-body-probe", "8000"):
        assert value not in output


def test_leak_check_passes_only_with_views_and_no_forbidden_content():
    assert smoke.safe({"turn": {"status": "completed"}}) is True
    assert smoke.safe({"turn": {"stop_reason": "x"}}) is False
    assert smoke.safe({"history": [smoke.SENTINEL]}) is False


def blocked_stub(
    *, running_views, blocked_request=None, other=None, finance_sees=False
):
    """차단 요청(SENTINEL) 대화가 running 화면을 running_views번 보인 뒤 blocked_input이 된다."""
    app = stub()
    seen = {"b": 0}
    base = app.router.routes[-1].endpoint

    probe = FastAPI()

    @probe.api_route("/{path:path}", methods=["GET", "POST"])
    async def route(path: str, request: Request):
        finance = request.headers.get("authorization", "").endswith("finance")
        if request.method == "GET" and path == "conversations/b":
            if finance:
                body = {"detail": "conversation_not_found"}
                if finance_sees:
                    body["echo"] = smoke.SENTINEL
                return JSONResponse(body, 404)
            seen["b"] += 1
            if seen["b"] <= running_views:
                turn = {"task_id": "b1", "status": "running"}
                turn["request"] = smoke.SENTINEL + " 010-0000-0000"
                if other:
                    turn.update(other)
                return {"turn": turn, "history": []}
            turn = {
                "task_id": "b1",
                "status": "blocked_input",
                "block": {"code": "pii_input", "message": "blocked"},
                "request": blocked_request,
            }
            return {"turn": turn, "history": []}
        return await base(path, request)

    return probe


def run_blocked(capsys, **kwargs):
    code = asyncio.run(
        smoke.run(
            transport=httpx.ASGITransport(app=blocked_stub(**kwargs)),
            timeout=1,
            interval=0.001,
        )
    )
    return code, capsys.readouterr().out


def test_sentinel_only_in_running_request_of_own_blocked_conversation_passes(capsys):
    code, output = run_blocked(capsys, running_views=2)
    assert code == 0
    assert "PASS no_leak_in_views" in output and "RESULT pass" in output
    assert smoke.SENTINEL not in output


def test_sentinel_in_blocked_view_request_fails(capsys):
    code, output = run_blocked(
        capsys, running_views=1, blocked_request=smoke.SENTINEL + " 010-0000-0000"
    )
    assert code == 1
    assert "FAIL no_leak_in_views contract_mismatch" in output


def test_missing_blocked_view_is_a_failure(capsys):
    # 차단 확정 화면을 한 번도 보지 못하면 running 허용만으로 통과하면 안 된다(공허 통과).
    code, output = run_blocked(capsys, running_views=10_000)
    assert code == 1
    assert "FAIL no_leak_in_views blocked_view_not_observed" in output
    assert "PASS no_leak_in_views" not in output


@pytest.mark.parametrize(
    "other",
    [
        {"stop_reason": "synthetic"},
        {"answer": smoke.SENTINEL},
        {"nested": {"guardrail_block": {"code": "pii_input"}}},
    ],
)
def test_running_allowance_is_limited_to_the_request_string(capsys, other):
    code, output = run_blocked(capsys, running_views=1, other=other)
    assert code == 1
    assert "FAIL no_leak_in_views contract_mismatch" in output


RUNNING = {"turn": {"status": "running", "request": smoke.SENTINEL}}
BLOCKED = {"turn": {"status": "blocked_input", "request": None}}
OWN = "/conversations/b"


@pytest.mark.parametrize(
    "views,expected",
    [
        ([], "no_views"),
        ([("platform", OWN, RUNNING), ("platform", OWN, BLOCKED)], None),
        ([("platform", OWN, RUNNING)], "blocked_view_not_observed"),
        (
            [("platform", "/conversations/p", RUNNING), ("platform", OWN, BLOCKED)],
            "contract_mismatch",
        ),
        ([("finance", OWN, RUNNING), ("platform", OWN, BLOCKED)], "contract_mismatch"),
        (
            [
                ("finance", "/approvals", {"approvals": [smoke.SENTINEL]}),
                ("platform", OWN, BLOCKED),
            ],
            "contract_mismatch",
        ),
        (
            [
                ("platform", "/approvals", {"approvals": [smoke.SENTINEL]}),
                ("platform", OWN, BLOCKED),
            ],
            "contract_mismatch",
        ),
        (
            [("finance", OWN, {"detail": smoke.SENTINEL}), ("platform", OWN, BLOCKED)],
            "contract_mismatch",
        ),
        (
            [
                (
                    "platform",
                    OWN,
                    {"turn": {"status": "blocked_input", "request": smoke.SENTINEL}},
                )
            ],
            "contract_mismatch",
        ),
        (
            [
                (
                    "platform",
                    OWN,
                    {
                        "turn": {"status": "running", "request": smoke.SENTINEL},
                        "history": [smoke.SENTINEL],
                    },
                ),
                ("platform", OWN, BLOCKED),
            ],
            "contract_mismatch",
        ),
        (
            [
                (
                    "platform",
                    OWN,
                    {
                        "turn": {
                            "status": "running",
                            "request": smoke.SENTINEL,
                            "stop_reason": 1,
                        }
                    },
                ),
                ("platform", OWN, BLOCKED),
            ],
            "contract_mismatch",
        ),
        (
            [
                ("platform", OWN, BLOCKED),
                (
                    "platform",
                    OWN,
                    {"turn": {"status": "completed", "request": smoke.SENTINEL}},
                ),
            ],
            "contract_mismatch",
        ),
    ],
)
def test_judge_views_allows_sentinel_only_in_owner_running_request(views, expected):
    # 허용은 차단 요청 대화 경로·소유자 토큰·running 화면의 turn.request 값 하나뿐이다.
    assert smoke.judge_views(views, blocked_path=OWN, owner="platform") == expected


def test_judge_views_without_blocked_conversation_requires_observation():
    views = [("platform", "/conversations/p", {"turn": {"status": "completed"}})]
    assert smoke.judge_views(views, blocked_path=None, owner="platform") == (
        "blocked_view_not_observed"
    )


def test_safe_keeps_forbidden_and_sentinel_rules():
    assert smoke.safe({"turn": {"request": smoke.SENTINEL}}) is False
    assert smoke.safe({"turn": {"status": "running", "stop_reason": 1}}) is False

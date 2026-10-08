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
                    draft={"body": "synthetic-draft"},
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

"""User-run conversation checks through HTTP only; print fixed verdict codes."""

import argparse
import asyncio
import json

import httpx

# Same synthetic question and expected platform document as api.smoke.
REQUEST = "INC-2026-012 장애를 요약해 주세요. 근거로 쓴 문서 ID를 본문에 표기해 주세요."
EXPECTED_DOC = "syn-incident-022"
SHARE = "INC-2026-012 장애보고 요약해서 팀 채널에 공유해줘"
SENTINEL = "conversation-smoke-private-probe"
FORBIDDEN = {
    "stop_reason",
    "guardrail_block",
    "guardrail_signals",
    "guardrail_post",
    "guardrail_output_flagged",
    "rules",
    "patterns",
    "digest",
    "output_warnings",
    "output_alert",
    "signals",
    "reasoning",
}


def safe(value):
    if isinstance(value, dict):
        return not (FORBIDDEN & value.keys()) and all(safe(v) for v in value.values())
    if isinstance(value, list):
        return all(safe(v) for v in value)
    return not isinstance(value, str) or (
        SENTINEL not in value and "guardrail.output_flagged" not in value
    )


def judge_views(views, *, blocked_path, owner):
    """Return None when views are clean, else a fixed reason code.

    The only exemption is the owner's own blocked request echoed in
    ``turn.request`` while that conversation is still ``running``; every other
    view, including the same conversation once ``blocked_input``, stays strict.
    """
    if not views:
        # 받은 view가 없으면 `all([])`가 참이 되어 아무것도 확인하지 않고 통과한다.
        return "no_views"
    leaked, observed = False, False
    for who, path, body in views:
        turn = body.get("turn") if isinstance(body, dict) else None
        own = blocked_path is not None and path == blocked_path and who == owner
        status = turn.get("status") if isinstance(turn, dict) else None
        if own and status == "blocked_input":
            observed = True
        if own and status == "running" and isinstance(turn.get("request"), str):
            body = {
                **body,
                "turn": {k: v for k, v in turn.items() if k != "request"},
            }
        leaked |= not safe(body)
    if leaked:
        return "contract_mismatch"
    return None if observed else "blocked_view_not_observed"


def mock_id(state):
    effect = state.get("effect")
    return effect.get("mock_id") if isinstance(effect, dict) else None


async def run(
    *, base_url="http://127.0.0.1:8000", timeout=180, transport=None, interval=0.2
):
    failed = False
    views = []
    owners = {}
    blocked_path = None
    stage = "rag_turn1"

    def check(name, ok, reason="contract_mismatch"):
        nonlocal failed
        failed |= not ok
        print(f"PASS {name}" if ok else f"FAIL {name} {reason}")

    try:
        if timeout <= 0:
            raise TimeoutError
        async with httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            trust_env=False,
            follow_redirects=False,
        ) as client:

            async def send(method, path, token=None, **kwargs):
                async with asyncio.timeout(timeout):
                    response = await client.request(
                        method,
                        path,
                        headers={"Authorization": "Bearer " + token} if token else {},
                        timeout=timeout,
                        **kwargs,
                    )
                if method == "GET" and (
                    path.startswith("/conversations") or path == "/approvals"
                ):
                    views.append((owners.get(token), path, response.json()))
                return response

            async def data(method, path, token=None, **kwargs):
                response = await send(method, path, token, **kwargs)
                response.raise_for_status()
                return response.json()

            async def poll(cid, token, wanted):
                deadline = asyncio.get_running_loop().time() + timeout
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        raise TimeoutError
                    async with asyncio.timeout(remaining):
                        state = (await data("GET", "/conversations/" + cid, token))[
                            "turn"
                        ]
                    if state.get("status") != "running":
                        return state
                    await asyncio.sleep(
                        min(
                            interval,
                            max(0, deadline - asyncio.get_running_loop().time()),
                        )
                    )

            platform = (
                await data("POST", "/sessions", json={"user": "demo-platform-g3"})
            )["session_token"]
            finance = (
                await data("POST", "/sessions", json={"user": "demo-finance-g3"})
            )["session_token"]
            owners.update({platform: "platform", finance: "finance"})
            first = await data(
                "POST", "/conversations", platform, json={"request": REQUEST}
            )
            cid, first_id = first["conversation_id"], first["turn_id"]
            state = await poll(cid, platform, "completed")
            check(
                stage,
                state.get("status") == "completed"
                and bool(state.get("answer"))
                and bool(state.get("observed_doc_ids")),
            )
            stage = "share_draft"
            second = await data(
                "POST", f"/conversations/{cid}/turns", platform, json={"request": SHARE}
            )
            state = await poll(cid, platform, "awaiting_approval")
            if not (
                state.get("status") == "awaiting_approval"
                and state.get("draft")
                and state.get("draft_sha256")
            ):
                check(stage, False, "draft_missing")
                raise ValueError
            check(stage, True)
            check(
                "share_targets_own_team_channel",
                state["draft"].get("args", {}).get("channel") == "team-platform",
            )
            tid, digest = second["turn_id"], state["draft_sha256"]
            response = await send(
                "POST", f"/conversations/{cid}/turns", platform, json={"request": SHARE}
            )
            check(
                "approval_pending_409",
                response.status_code == 409
                and response.json().get("detail") == "approval_pending",
            )
            inbox = await data("GET", "/approvals", platform)
            check(
                "inbox_lists_pending",
                any(row.get("task_id") == tid for row in inbox["approvals"]),
            )
            check(
                "other_user_404",
                (await send("GET", "/conversations/" + cid, finance)).status_code
                == 404,
            )
            inbox = await data("GET", "/approvals", finance)
            check(
                "inbox_hides_from_others",
                all(row.get("task_id") != tid for row in inbox["approvals"]),
            )
            approval = {"decision": "approve", "draft_sha256": digest}
            stage = "approve_sends_once"
            receipt = await data(
                "POST", f"/tasks/{tid}/approval", platform, json=approval
            )
            state = await poll(cid, platform, "completed")
            effect = state.get("effect")
            check(
                stage,
                state.get("status") == "completed"
                and bool(mock_id(state))
                and (
                    receipt.get("status") != "completed"
                    or receipt.get("effect") == effect
                ),
            )
            response = await send(
                "POST", f"/tasks/{tid}/approval", platform, json=approval
            )
            check(
                "duplicate_approval_idempotent",
                response.status_code == 200
                and bool(effect)
                and response.json().get("effect") == effect,
            )
            stage = "second_write_separate_effect"
            third = await data(
                "POST",
                f"/conversations/{cid}/turns",
                platform,
                json={
                    "request": "INC-2026-012 장애보고 정리해서 team-platform에 공유해줘"
                },
            )
            initial = await data("GET", "/conversations/" + cid, platform)
            clean = (
                not initial["turn"].get("effect")
                and initial["turn"].get("draft_sha256") != digest
            )
            state = await poll(cid, platform, "awaiting_approval")
            clean &= (
                state.get("status") == "awaiting_approval"
                and bool(state.get("draft"))
                and bool(state.get("draft_sha256"))
                and state.get("draft_sha256") != digest
                and not state.get("effect")
            )
            if state.get("draft_sha256"):
                await data(
                    "POST",
                    f"/tasks/{third['turn_id']}/approval",
                    platform,
                    json={"decision": "approve", "draft_sha256": state["draft_sha256"]},
                )
                state = await poll(cid, platform, "completed")
            check(
                stage,
                clean
                and state.get("status") == "completed"
                and bool(mock_id(state))
                and mock_id(state) != (effect or {}).get("mock_id"),
            )
            check(
                "late_duplicate_404",
                (
                    await send(
                        "POST", f"/tasks/{tid}/approval", platform, json=approval
                    )
                ).status_code
                == 404,
            )
            check(
                "past_turn_404",
                (await send("GET", "/tasks/" + first_id, platform)).status_code == 404,
            )
            stage = "finance_no_leak_no_send"
            other = await data(
                "POST", "/conversations", finance, json={"request": REQUEST}
            )
            state = await poll(other["conversation_id"], finance, "completed")
            inbox = await data("GET", "/approvals", finance)
            check(
                stage,
                state.get("status") == "completed"
                and all(
                    EXPECTED_DOC not in state.get(key, [])
                    for key in ("observed_doc_ids", "cited_doc_ids")
                )
                and not state.get("draft")
                and not state.get("effect")
                and inbox["approvals"] == [],
            )
            stage = "blocked_card_shape"
            blocked = await data(
                "POST",
                "/conversations",
                platform,
                json={"request": SENTINEL + " 010-0000-0000"},
            )
            blocked_path = "/conversations/" + blocked["conversation_id"]
            state = await poll(blocked["conversation_id"], platform, "blocked_input")
            if state.get("status") == "blocked_input":
                check(
                    stage,
                    set(state.get("block", {})) == {"code", "message"}
                    and state.get("request") is None
                    and SENTINEL not in json.dumps(state),
                )
            else:
                print("INFO blocked_card_shape not_blocked")
    except TimeoutError:
        check(
            stage,
            False,
            "draft_missing" if stage == "share_draft" else "deadline_exceeded",
        )
    except Exception as error:  # noqa: BLE001 — never render response bodies or credentials
        # 예외 클래스 이름만 남긴다(문장·URL·토큰·본문은 출력하지 않는다).
        check(stage, False, f"request_failed:{type(error).__name__}")
    reason = judge_views(views, blocked_path=blocked_path, owner="platform")
    check("no_leak_in_views", reason is None, reason or "contract_mismatch")
    print("RESULT fail" if failed else "RESULT pass")
    return int(failed)


def main(argv=None, *, transport=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    return asyncio.run(
        run(base_url=args.base_url, timeout=args.timeout, transport=transport)
    )


if __name__ == "__main__":
    raise SystemExit(main())

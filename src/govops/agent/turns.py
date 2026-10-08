"""대화 범위 근거를 보존하고 모든 턴 채널을 새 입력으로 초기화한다."""

from collections.abc import Mapping

MAX_TURNS = 10
CARRY_STATUSES = frozenset({"completed", "rejected", "expired", "blocked"})
CONVERSATION_KEYS: frozenset[str] = frozenset(
    {
        "user_id",
        "created_at",
        "messages",
        "evidence_ids",
        "conversation_id",
        "history",
        "turn_index",
    }
)
TURN_KEYS: frozenset[str] = frozenset(
    {
        "task_id",
        "request",
        "steps",
        "status",
        "answer",
        "error",
        "stop_reason",
        "quarantined_ids",
        "quarantine_unmatched",
        "masked_total",
        "guardrail_truncated",
        "guardrail_block",
        "observed_ids",
        "cited_doc_ids",
        "draft",
        "draft_sha256",
        "check",
        "approvals",
        "awaiting_since",
        "expires_at",
        "effect",
        "resume_value",
        "edit_approver",
        "approval_receipt",
        "edit_original",
        "rejected_edit",
        "effect_result",
        "turn_cost_usd",
        "explored_ids",
        "no_new_info_streak",
    }
)


class TurnLimitError(ValueError):
    """대화당 허용된 턴 수를 초과했다."""


def turn_defaults() -> dict:
    """각 호출마다 독립적인 턴 초기값을 반환한다."""
    return {
        "task_id": None,
        "request": None,
        "steps": 0,
        "status": "running",
        "quarantined_ids": [],
        "quarantine_unmatched": 0,
        "masked_total": 0,
        "guardrail_truncated": False,
        "guardrail_block": None,
        "answer": None,
        "error": None,
        "observed_ids": [],
        "cited_doc_ids": [],
        "draft": None,
        "draft_sha256": None,
        "check": None,
        "approvals": [],
        "awaiting_since": None,
        "expires_at": None,
        "effect": None,
        "resume_value": None,
        "edit_approver": None,
        "approval_receipt": None,
        "edit_original": None,
        "rejected_edit": None,
        "effect_result": None,
        "turn_cost_usd": 0.0,
        "explored_ids": [],
        "no_new_info_streak": 0,
        "stop_reason": None,
    }


def next_history(previous: Mapping) -> list[dict[str, str]]:
    history = [dict(message) for message in previous.get("history", [])]
    request, answer = previous.get("request"), previous.get("answer")
    if previous.get("status") in CARRY_STATUSES and request and answer:
        history.extend(
            [
                {"role": "user", "content": request},
                {"role": "assistant", "content": answer},
            ]
        )
    return history


def build_turn_state(
    *,
    task_id: str,
    user_id: str,
    request: str,
    system_prompt: str,
    created_at: str,
    previous: Mapping | None = None,
    conversation_id: str | None = None,
) -> dict:
    if previous is None:
        conversation_id = conversation_id or task_id
        history = []
        evidence_ids = []
        turn_index = 0
    else:
        if user_id != previous["user_id"]:
            raise ValueError("owner_mismatch")
        history = next_history(previous)
        evidence_ids = sorted(previous.get("evidence_ids", []))
        turn_index = previous.get("turn_index", 0) + 1
        if turn_index >= MAX_TURNS:
            raise TurnLimitError("turn_limit")
        created_at = previous["created_at"]
        conversation_id = (
            previous.get("conversation_id") or conversation_id or previous["task_id"]
        )
    return {
        **turn_defaults(),
        "task_id": task_id,
        "request": request,
        "user_id": user_id,
        "created_at": created_at,
        "conversation_id": conversation_id,
        "history": history,
        "evidence_ids": evidence_ids,
        "turn_index": turn_index,
        "messages": [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": request},
        ],
    }

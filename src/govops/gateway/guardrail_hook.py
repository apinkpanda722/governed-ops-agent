"""LiteLLM adapter: bounded pre-check, fail-open output flags, safe headers."""

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path
from time import monotonic

from fastapi import HTTPException
from litellm.integrations.custom_guardrail import CustomGuardrail

from govops.gateway.hook_digest import compute_digests
from govops.gateway.hook_header import GuardrailInfo, format_header
from govops.guardrails.screen import scan_output, screen_request

ROOT = Path(__file__).resolve().parents[3]
DIGESTS = compute_digests(ROOT)
TEAMS = frozenset(
    u["team"] for u in json.loads((ROOT / "data/seed/users.json").read_bytes())["users"]
)
PRE_CALL_TIMEOUT_S = 2.0
_INFO = "govops_guardrail_info"
# LiteLLM logs a pre-call rejection's request body (optional_params.messages) to
# the failure callbacks and turn_off_message_logging does not cover that field.
_REDACTED = "[govops:redacted]"


def _redact(data):
    data["messages"] = [{"role": "user", "content": _REDACTED}]


class GovopsGuardrail(CustomGuardrail):
    async def async_pre_call_hook(self, user_api_key_dict, cache, data, call_type):
        if not isinstance(data.get("metadata"), dict):
            data["metadata"] = {}
        started = monotonic()
        info = GuardrailInfo(DIGESTS.rules, DIGESTS.code)

        def record(pre, code="-", **kwargs):
            nonlocal info
            info = replace(
                info, pre=pre, c=code, ms=int((monotonic() - started) * 1000), **kwargs
            )
            data.setdefault("metadata", {})[_INFO] = asdict(info)

        def block(code, pre, error="guardrail_blocked"):
            record(pre, code)
            _redact(data)
            raise HTTPException(400, detail={"error": error, "code": code})

        async def check():
            nonlocal info
            team = getattr(user_api_key_dict, "team_id", None)
            if not isinstance(team, str) or team not in TEAMS:
                block("team_unknown", "team_unknown")
            info = replace(info, team=team)
            probe = data.get("metadata", {}).get("govops_probe")
            if probe == "digest":
                block("probe", "probe")
            if probe == "slow":
                await asyncio.sleep(PRE_CALL_TIMEOUT_S + 1)
            if probe == "raise":
                raise RuntimeError("diagnostic failure")
            result = await asyncio.to_thread(screen_request, data["messages"])
            if result.verdict.action == "block":
                block(result.verdict.code, "block")
            data["messages"] = result.messages
            items = [item for item in result.items if item.quarantined]
            ids = tuple(
                item.tool_call_id for item in items if item.tool_call_id is not None
            )
            record(
                "allow",
                q=len(items),
                m=result.masked,
                ids=ids[:64],
                bad=sum(item.tool_call_id is None for item in items),
                trunc=int(len(ids) > 64),
            )
            return data

        try:
            return await asyncio.wait_for(check(), PRE_CALL_TIMEOUT_S)
        except HTTPException as exc:
            if (
                exc.status_code == 400
                and isinstance(exc.detail, dict)
                and exc.detail.get("code")
                in {"injection_direct", "pii_input", "team_unknown", "probe"}
            ):
                raise
            record("unavailable", "guardrail_unavailable")
        except Exception:  # noqa: BLE001 — prevent LiteLLM from exposing exception text
            record("unavailable", "guardrail_unavailable")
        _redact(data)
        raise HTTPException(
            400,
            detail={"error": "guardrail_unavailable", "code": "guardrail_unavailable"},
        ) from None

    async def async_post_call_success_hook(self, data, user_api_key_dict, response):
        post = "error"
        try:
            if data.get("metadata", {}).get("govops_probe") == "post_raise":
                raise RuntimeError("diagnostic failure")
            flagged = False
            choices = (
                response.get("choices", [])
                if isinstance(response, dict)
                else response.choices
            )
            for choice in choices:
                message = (
                    choice.get("message", {})
                    if isinstance(choice, dict)
                    else choice.message
                )
                content = (
                    message.get("content")
                    if isinstance(message, dict)
                    else message.content
                )
                flags = scan_output(content or "")
                flagged |= bool(flags.pii_kinds or flags.injection)
            post = "flag" if flagged else "ok"
        except Exception:  # noqa: BLE001 — prevent LiteLLM from exposing exception text
            post = "error"
        try:
            metadata = data["metadata"]
            metadata[_INFO] = asdict(
                replace(GuardrailInfo(**metadata[_INFO]), post=post)
            )
        except Exception:  # noqa: BLE001 — prevent LiteLLM from exposing exception text
            return response  # Missing metadata yields no trusted header.
        return response

    async def async_post_call_response_headers_hook(
        self, data, user_api_key_dict=None, response=None, **kwargs
    ):
        try:
            return {
                "x-govops-guardrail": format_header(
                    GuardrailInfo(**data["metadata"][_INFO])
                )
            }
        except Exception:  # noqa: BLE001 — prevent LiteLLM from exposing exception text
            return None

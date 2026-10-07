"""에이전트 전용 LiteLLM 비동기 호출과 호출 전 비용 상한 검사."""

import asyncio
import json
import logging
import os
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from govops.costs import (
    BudgetExceededError,
    CostLedger,
    anthropic_cost_usd,
    estimate_call_usd,
    resolve_deployment,
    resolve_model,
    response_cost_usd,
)
from govops.env import env_or
from govops.gateway.faults import ModelFaultScript, model_fault_alias
from govops.gateway.hook_digest import HookDigests
from govops.gateway.hook_header import GuardrailInfo, parse_header
from govops.obs.trace import current_span_id, set_attributes

_MODEL = "gpt-6-luna"
_IN_CALL: ContextVar[bool] = ContextVar("gateway_model_call", default=False)


class _CallLogFilter(logging.Filter):
    """SDK와 HTTP DEBUG의 본문·헤더 출력을 현재 호출 context에서 차단한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _IN_CALL.get()


_LOG_FILTER = _CallLogFilter()


def _protect_transport_logs() -> None:
    # Logger filters do not propagate: attach to each emitting child logger.
    names = {
        "openai._base_client",
        "httpx",
        "httpx2",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    }
    names.update(
        name
        for name in logging.Logger.manager.loggerDict
        if name.split(".")[0] in {"openai", "httpx", "httpx2", "httpcore"}
    )
    for name in names:
        logging.getLogger(name).addFilter(_LOG_FILTER)


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ChatResult:
    message: dict
    model: str
    usage: Usage
    cost_usd: float
    fallback_used: bool = False
    unmapped_model: bool = False
    deployment_id: str | None = None
    body_model: str | None = None
    model_group: str | None = None
    attempted_fallbacks: int | None = None
    guardrail: GuardrailInfo | None = None


class GatewayError(Exception):
    """HTTP 상태 또는 오류 종류만 담는 안전한 호출 오류."""


class GuardrailBlocked(GatewayError):
    def __init__(self, code: str, info: GuardrailInfo | None):
        if code not in {"injection_direct", "pii_input"}:
            raise ValueError("invalid guardrail code")
        self.code = code
        self.info = info
        super().__init__(code)


class GuardrailUnavailable(GatewayError):
    def __init__(self, code: str):
        if code not in {
            "guardrail_unavailable",
            "team_unknown",
            "guardrail_header_missing",
            "guardrail_stale",
        }:
            raise ValueError("invalid guardrail code")
        self.code = code
        super().__init__(code)


class ChatClient(Protocol):
    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        task_id: str,
        user_id: str,
        max_output_tokens: int = 1024,
    ) -> ChatResult: ...


class LiteLLMChatClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        key_resolver: Callable[[str], str] | None = None,
        expected_digests: HookDigests | None = None,
        require_guardrail: bool = False,
        ledger: CostLedger | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 60.0,
        fault_script: ModelFaultScript | None = None,
        fault_mode: str | None = None,
    ):
        if fault_mode not in {None, "down_alias"}:
            raise ValueError("Invalid fault_mode")
        if fault_script is not None and fault_mode is None:
            raise ValueError("fault_script requires fault_mode")
        self._fault_script = fault_script
        self._fault_mode = fault_mode
        self._key_resolver = key_resolver
        self._expected_digests = expected_digests
        self._require_guardrail = require_guardrail
        self._api_key = (
            None
            if key_resolver is not None
            else (api_key if api_key is not None else os.getenv("LITELLM_AGENT_KEY"))
        )
        self._base_url = base_url or env_or("LITELLM_BASE_URL", "http://127.0.0.1:4000")
        self._ledger = ledger if ledger is not None else CostLedger()
        self._http_client = http_client
        self._timeout_s = timeout_s

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        task_id: str,
        user_id: str,
        max_output_tokens: int = 1024,
        guardrail_probe: Literal["post_raise"] | None = None,
    ) -> ChatResult:
        api_key = (
            self._key_resolver(user_id)
            if self._key_resolver is not None
            else self._api_key
        )
        if not api_key or not api_key.strip():
            raise GatewayError("LITELLM_AGENT_KEY가 설정되지 않았습니다")
        metadata = {
            "task_id": task_id,
            "trace_user_id": user_id,
            "generation_name": "agent-primary",
            "tags": ["govops"],
        }
        if guardrail_probe is not None:
            metadata["govops_probe"] = guardrail_probe
        if re.fullmatch(r"[0-9a-f]{32}", task_id):
            metadata["existing_trace_id"] = task_id
        # LiteLLM이 만드는 기본 generation ID(응답 ID 기반의 긴 base64)는 Langfuse가
        # MinIO 객체 이름으로 쓸 때 제한에 걸려 업로드가 실패한다. 짧은 hex를 직접 정한다.
        generation_id = secrets.token_hex(8)
        metadata["generation_id"] = generation_id
        # generation을 에이전트의 모델 호출 span 아래에 붙인다(span ID = Langfuse observation ID).
        parent = current_span_id()
        if parent is not None:
            metadata["parent_observation_id"] = parent
        set_attributes(generation_id=generation_id)
        body = {
            "model": "agent-primary",
            "messages": list(messages),
            "tools": list(tools),
            "user": user_id,
            "metadata": metadata,
            "max_completion_tokens": max_output_tokens,
        }
        body["model"] = model_fault_alias(self._fault_script, task_id)
        # UTF-8 bytes bound token count conservatively; doubled serialization
        # plus fixed framing allowance covers per-message/tool protocol overhead.
        input_upper = (
            2 * len(json.dumps(body, ensure_ascii=True).encode("utf-8")) + 1024
        )
        estimate = estimate_call_usd(
            _MODEL, input_tokens_upper=input_upper, max_output_tokens=max_output_tokens
        )
        # 상한 검사와 예약 기록은 잠금 안에서 한 번에 한다(동시 호출의 합산 초과 방지).
        reservation = await asyncio.to_thread(
            self._ledger.reserve,
            purpose="agent_primary",
            model=_MODEL,
            task_id=task_id,
            estimate_usd=estimate,
        )
        reservations = [(reservation, "agent_primary", _MODEL, "openai")]
        usage = Usage(0, 0, 0)
        cost = 0.0
        response_model = _MODEL
        supplier = None
        unmapped = False
        fallback_used = False
        deployment_id = model_group = attempted_fallbacks = None
        try:
            try:
                fallback_reservation = await asyncio.to_thread(
                    self._ledger.reserve,
                    purpose="agent_fallback",
                    model="claude-sonnet-5-5",
                    task_id=task_id,
                    estimate_usd=anthropic_cost_usd(
                        "claude-sonnet-5-5",
                        input_tokens=input_upper,
                        output_tokens=max_output_tokens,
                    ),
                )
            except BudgetExceededError:
                body["model"] = "agent-primary-only"
            else:
                reservations.append(
                    (
                        fallback_reservation,
                        "agent_fallback",
                        "claude-sonnet-5-5",
                        "anthropic",
                    )
                )
            (
                completion,
                deployment_id,
                model_group,
                attempted_fallbacks,
                guardrail,
            ) = await self._send(
                body,
                api_key=api_key,
                user_id=user_id,
                task_id=task_id,
                max_output_tokens=max_output_tokens,
            )
            response_model = getattr(completion, "model", None)
            if not isinstance(response_model, str):
                raise GatewayError("gateway malformed response")
            body_model = response_model
            resolved = (
                resolve_deployment(deployment_id)
                if deployment_id is not None
                else resolve_model(body_model)
            )
            if deployment_id is not None and resolved.mapped:
                response_model = resolved.family
            fallback_used = resolved.supplier == "anthropic" or (
                attempted_fallbacks is not None and attempted_fallbacks > 0
            )
            supplier = resolved.supplier
            unmapped = not resolved.mapped
            usage, cost = _usage_and_cost(
                completion,
                input_upper,
                max_output_tokens,
                response_model if resolved.mapped else "unregistered",
            )
            if self._require_guardrail and guardrail is None:
                raise GuardrailUnavailable("guardrail_header_missing")
            if (
                self._expected_digests is not None
                and guardrail is not None
                and (
                    guardrail.rules != self._expected_digests.rules
                    or guardrail.code != self._expected_digests.code
                )
            ):
                raise GuardrailUnavailable("guardrail_stale")
            return _result(
                completion,
                usage,
                cost,
                model=response_model,
                body_model=body_model,
                deployment_id=deployment_id,
                model_group=model_group,
                attempted_fallbacks=attempted_fallbacks,
                fallback_used=fallback_used,
                unmapped_model=unmapped,
                guardrail=guardrail,
            )
        finally:
            set_attributes(
                usage={
                    "input": usage.input_tokens,
                    "cached_input": usage.cached_input_tokens,
                    "output": usage.output_tokens,
                },
                cost_usd=cost,
                unmapped_model=unmapped,
                fallback_used=fallback_used,
                deployment_id=deployment_id,
                model_group=model_group,
                attempted_fallbacks=attempted_fallbacks,
            )
            for reserved, purpose, model, reserved_supplier in reservations:
                charged = unmapped or supplier == reserved_supplier
                settled_usage = usage if charged else Usage(0, 0, 0)
                await asyncio.shield(
                    asyncio.to_thread(
                        self._ledger.settle,
                        reserved,
                        purpose=purpose,
                        model=response_model if charged else model,
                        task_id=task_id,
                        input_tokens=settled_usage.input_tokens,
                        cached_input_tokens=settled_usage.cached_input_tokens,
                        output_tokens=settled_usage.output_tokens,
                        cost_usd=cost if charged else 0.0,
                    )
                )

    async def _send(
        self,
        body: dict,
        *,
        api_key: str,
        user_id: str,
        task_id: str,
        max_output_tokens: int,
    ):
        _protect_transport_logs()
        token = _IN_CALL.set(True)
        error = None
        try:
            sdk = AsyncOpenAI(
                api_key=api_key,
                base_url=self._base_url,
                http_client=self._http_client,
                timeout=self._timeout_s,
                max_retries=0,
                organization="",
                project="",
            )
            try:
                raw = await sdk.chat.completions.with_raw_response.create(
                    model=body["model"],
                    messages=body["messages"],
                    tools=body["tools"],
                    user=user_id,
                    extra_body={"metadata": body["metadata"]},
                    max_completion_tokens=max_output_tokens,
                )
                deployment_id = raw.headers.get("x-litellm-model-id")
                model_group = raw.headers.get("x-litellm-model-group")
                try:
                    attempted_fallbacks = int(
                        raw.headers.get("x-litellm-attempted-fallbacks")
                    )
                except (ValueError, TypeError):
                    attempted_fallbacks = None
                guardrail = parse_header(raw.headers.get("x-govops-guardrail"))
                completion = raw.parse()
            finally:
                # An injected HTTP client remains owned by its caller.
                if self._http_client is None:
                    await sdk.close()
        except APITimeoutError:
            error = GatewayError("gateway timeout")
        except APIConnectionError:
            error = GatewayError("gateway connection failure")
        except APIStatusError as exc:
            error = GatewayError(f"gateway HTTP {exc.status_code}")
            if exc.status_code == 400:
                body = exc.body
                details = body.get("error", body) if isinstance(body, dict) else {}
                fields = (
                    details.get("provider_specific_fields", {})
                    if isinstance(details, dict)
                    else {}
                )
                code = fields.get("code") if isinstance(fields, dict) else None
                if code in {"injection_direct", "pii_input"}:
                    error = GuardrailBlocked(
                        code,
                        parse_header(exc.response.headers.get("x-govops-guardrail")),
                    )
                elif code in {"guardrail_unavailable", "team_unknown"}:
                    error = GuardrailUnavailable(code)
        except OpenAIError:
            error = GatewayError("gateway SDK failure")
        finally:
            _IN_CALL.reset(token)
        # Raise outside the except block so SDK exceptions retaining requests
        # are not attached as exception context.
        if error is not None:
            raise error
        return completion, deployment_id, model_group, attempted_fallbacks, guardrail


def _usage_and_cost(
    completion, input_upper: int, max_output_tokens: int, pricing_model: str
) -> tuple[Usage, float]:
    raw_usage = getattr(completion, "usage", None)
    estimate, _ = response_cost_usd(
        pricing_model,
        input_tokens=input_upper,
        cached_input_tokens=0,
        output_tokens=max_output_tokens,
    )
    estimated = (Usage(input_upper, 0, max_output_tokens), estimate)
    if (
        raw_usage is None
        or raw_usage.prompt_tokens is None
        or raw_usage.completion_tokens is None
    ):
        return estimated
    details = raw_usage.prompt_tokens_details
    usage = Usage(
        raw_usage.prompt_tokens,
        (details.cached_tokens or 0) if details else 0,
        raw_usage.completion_tokens,
    )
    try:
        cost, _ = response_cost_usd(
            pricing_model,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
        )
    except (ValueError, TypeError):
        return (
            estimated  # 해석할 수 없는 사용량은 과소 계상하지 않고 추정액으로 정산한다
        )
    return usage, cost


def _result(completion, usage: Usage, cost: float, **metadata) -> ChatResult:
    failure = None
    try:
        message = completion.choices[0].message
        result = ChatResult(
            message={
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    }
                    for call in message.tool_calls or []
                ],
            },
            usage=usage,
            cost_usd=cost,
            **metadata,
        )
    except (IndexError, AttributeError, TypeError):
        failure = GatewayError("gateway malformed response")
    if failure is not None:
        raise failure
    return result

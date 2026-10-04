"""에이전트 전용 LiteLLM 비동기 호출과 호출 전 비용 상한 검사."""

import json
import logging
import os
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    OpenAIError,
)

from govops.costs import CostLedger, estimate_call_usd, token_cost_usd

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


class GatewayError(Exception):
    """HTTP 상태 또는 오류 종류만 담는 안전한 호출 오류."""


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
        ledger: CostLedger | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 60.0,
    ):
        self._api_key = (
            api_key if api_key is not None else os.getenv("LITELLM_AGENT_KEY")
        )
        self._base_url = (
            base_url or os.getenv("LITELLM_BASE_URL") or "http://127.0.0.1:4000"
        )
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
    ) -> ChatResult:
        if not self._api_key or not self._api_key.strip():
            raise GatewayError("LITELLM_AGENT_KEY가 설정되지 않았습니다")
        body = {
            "model": "agent-primary",
            "messages": list(messages),
            "tools": list(tools),
            "user": user_id,
            "metadata": {"task_id": task_id},
            "max_completion_tokens": max_output_tokens,
        }
        # UTF-8 bytes bound token count conservatively; doubled serialization
        # plus fixed framing allowance covers per-message/tool protocol overhead.
        input_upper = (
            2 * len(json.dumps(body, ensure_ascii=True).encode("utf-8")) + 1024
        )
        estimate = estimate_call_usd(
            _MODEL, input_tokens_upper=input_upper, max_output_tokens=max_output_tokens
        )
        self._ledger.check_before_call(purpose="agent_primary", estimate_usd=estimate)
        _protect_transport_logs()
        token = _IN_CALL.set(True)
        error = None
        try:
            sdk = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                http_client=self._http_client,
                timeout=self._timeout_s,
                max_retries=0,
                organization="",
                project="",
            )
            try:
                completion = await sdk.chat.completions.create(
                    model="agent-primary",
                    messages=body["messages"],
                    tools=body["tools"],
                    user=user_id,
                    extra_body={"metadata": {"task_id": task_id}},
                    max_completion_tokens=max_output_tokens,
                )
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
        except OpenAIError:
            error = GatewayError("gateway SDK failure")
        finally:
            _IN_CALL.reset(token)
        # Raise outside the except block so SDK exceptions retaining requests
        # are not attached as exception context.
        if error is not None:
            raise error

        raw_usage = completion.usage
        if (
            raw_usage is None
            or raw_usage.prompt_tokens is None
            or raw_usage.completion_tokens is None
        ):
            usage = Usage(input_upper, 0, max_output_tokens)
            cost = estimate
        else:
            details = raw_usage.prompt_tokens_details
            usage = Usage(
                raw_usage.prompt_tokens,
                (details.cached_tokens or 0) if details else 0,
                raw_usage.completion_tokens,
            )
            cost = token_cost_usd(
                _MODEL,
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
            )
        self._ledger.record(
            purpose="agent_primary",
            model=_MODEL,
            task_id=task_id,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=cost,
        )
        message = completion.choices[0].message
        return ChatResult(
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
            model=completion.model,
            usage=usage,
            cost_usd=cost,
        )

"""Credential-free LLM recordings at the ChatClient boundary."""

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from govops.gateway.client import ChatClient, ChatResult, Usage
from govops.obs.mask import scrub_secrets
from govops.replay.recordings import RecordingRejected, _canonical


class RerecordRequired(RuntimeError):
    """The frozen prompt or recorded call no longer matches."""

    def __init__(self):
        super().__init__("재기록 필요: 프롬프트·모델·도구 정의·파라미터 변경")


def prompt_hash(
    *,
    system_prompt: str,
    tools: Sequence[Mapping],
    model: str,
    params: Mapping[str, Any],
) -> str:
    return _hash(
        {
            "system_prompt": system_prompt,
            "tools": list(tools),
            "model": model,
            "params": dict(params),
        }
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


_SECRET = re.compile(
    r"authorization|bearer\s|api_key|\bsk-|\b(?:password|secret|token)\s*[\"']?\s*[:=]",
    re.IGNORECASE,
)
_DOC = re.compile(r"\b(?:doc_id|document_id)\s*[\"']?\s*[:=]\s*[\"']?([\w.-]+)")
_DOC_FORM = re.compile(
    r"\b(?:syn-)?(?:doc|document|incident|policy|runbook|guide|faq|manual)-[\w.-]+\b"
)


def _safe(value: Any, *, tool_schema: bool = False) -> None:
    text = _canonical(value)
    if _SECRET.search(text) or scrub_secrets(text) != text:
        raise RecordingRejected("credential in LLM recording")
    if any(not m.group(1).startswith("syn-") for m in _DOC.finditer(text)) or any(
        not m.group().startswith("syn-") for m in _DOC_FORM.finditer(text)
    ):
        raise RecordingRejected("non-synthetic document in LLM recording")
    if isinstance(value, Mapping):
        for key, child in value.items():
            if (
                not tool_schema
                and key in {"doc_id", "document_id"}
                and (not isinstance(child, str) or not child.startswith("syn-"))
            ):
                raise RecordingRejected("non-synthetic document in LLM recording")
            _safe(child, tool_schema=tool_schema)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _safe(child, tool_schema=tool_schema)
    elif isinstance(value, str):
        # Tool-result messages contain JSON as an opaque string.
        try:
            nested = json.loads(value)
        except ValueError:
            return
        if isinstance(nested, (dict, list)):
            _safe(nested, tool_schema=tool_schema)


def _order_free(message):
    """도구 결과 메시지는 JSON 문자열이다. 키 순서는 live(A 응답 순서)와 기록(키 정렬 저장)이 달라도
    같은 내용이므로, 요청 키에서는 정렬한 형태로 본다. 파싱되지 않는 문자열은 그대로 둔다."""
    content = message.get("content") if isinstance(message, Mapping) else None
    if (
        isinstance(message, Mapping)
        and message.get("role") == "tool"
        and isinstance(content, str)
    ):
        try:
            nested = json.loads(content)
        except ValueError:
            return message
        if isinstance(nested, (dict, list)):
            return {**message, "content": _canonical(nested)}
    return message


def _request(messages, tools, max_output_tokens) -> dict:
    return {
        "messages": [_order_free(m) for m in messages],
        "tools": list(tools),
        "max_completion_tokens": max_output_tokens,
    }


class RecordingChatClient:
    def __init__(
        self,
        inner: ChatClient,
        *,
        task_id: str,
        directory: Path,
        system_prompt: str,
        tools: Sequence[Mapping],
        model: str,
        params: Mapping,
    ):
        if not re.fullmatch(r"[\w-]+", task_id):
            raise RecordingRejected("invalid task id")
        self._inner = inner
        self._directory = directory
        self._header = {
            "prompt_hash": prompt_hash(
                system_prompt=system_prompt, tools=tools, model=model, params=params
            ),
            "model": model,
            "params": dict(params),
            "task_id": task_id,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        _safe(self._header)
        self._header = json.loads(_canonical(self._header))
        self._calls: list[dict] = []
        self._rejected = False
        self._finished = False

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        task_id: str,
        user_id: str,
        max_output_tokens: int = 1024,
    ) -> ChatResult:
        if self._rejected or self._finished:
            raise RecordingRejected("recording is closed")
        request = _request(messages, tools, max_output_tokens)
        try:
            _safe(request["messages"])
            # Schema property names identify inputs, not returned document IDs.
            # Credentials and synthetic string values are still screened.
            _safe(request["tools"], tool_schema=True)
        except RecordingRejected:
            self._rejected = True
            raise
        key = _hash(request)
        result = await self._inner.complete(
            messages=messages,
            tools=tools,
            task_id=task_id,
            user_id=user_id,
            max_output_tokens=max_output_tokens,
        )
        row = {
            "request_key": key,
            "response_message": result.message,
            "response_model": result.model,
            "usage": asdict(result.usage),
            "cost_usd": result.cost_usd,
        }
        try:
            _safe(row)
        except RecordingRejected:
            self._rejected = True
            raise
        # Snapshot without changing any response fields or opaque JSON strings.
        self._calls.append(json.loads(_canonical(row)))
        return result

    def finish(self) -> Path:
        if self._rejected:
            raise RecordingRejected("recording rejected")
        _safe([self._header, *self._calls])
        content = "".join(
            _canonical(row) + "\n"
            for row in [dict(self._header, call_count=len(self._calls)), *self._calls]
        )
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{self._header['task_id']}.jsonl"
        descriptor, temporary = tempfile.mkstemp(prefix=".llm-", dir=self._directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        self._finished = True
        return path


class ReplayChatClient:
    def __init__(self, calls: list[dict]):
        self._calls = calls
        self._index = 0

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        system_prompt: str,
        tools: Sequence[Mapping],
        model: str,
        params: Mapping,
    ) -> "ReplayChatClient":
        try:
            header, *calls = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
            ]
            expected = prompt_hash(
                system_prompt=system_prompt, tools=tools, model=model, params=params
            )
            if header["prompt_hash"] != expected or header["call_count"] != len(calls):
                raise RerecordRequired()
        except (OSError, ValueError, KeyError, TypeError):
            raise RerecordRequired() from None
        return cls(calls)

    async def complete(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        task_id: str,
        user_id: str,
        max_output_tokens: int = 1024,
    ) -> ChatResult:
        if self._index >= len(self._calls):
            raise RerecordRequired()
        row = self._calls[self._index]
        if row["request_key"] != _hash(_request(messages, tools, max_output_tokens)):
            raise RerecordRequired()
        result = ChatResult(
            message=json.loads(_canonical(row["response_message"])),
            model=row["response_model"],
            usage=Usage(**row["usage"]),
            cost_usd=row["cost_usd"],
        )
        self._index += 1
        return result

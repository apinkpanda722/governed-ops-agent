"""LLM 기록의 요청 키 이전(키 순서 결함 보정).

결함: 도구 결과는 LLM 메시지에 JSON 문자열로 들어가는데, 기록 파일은 A 응답을 키 정렬로 저장해
재생 메시지의 키 순서가 live와 달라 요청 키가 맞지 않았다. 이 모듈은 이전에 쓰던 키 방식(`legacy`)이
**기록된 `request_key`와 정확히 일치함을 모든 호출에서 확인할 때만** `request_key`를 새 방식(키 순서와
무관)으로 바꾼다. 응답·사용량·비용 등 다른 필드는 건드리지 않는다. 하나라도 불일치하면 아무것도 쓰지
않고 `RekeyAborted`로 중단한다.

live 키 순서는 A 응답 모델의 필드 순서이며 계약 스냅샷 출력 스키마의 `required` 배열에서 가져온다
(스냅샷의 `properties`는 키 정렬로 저장돼 순서 정보가 없다). 손으로 적은 순서는 없다.
mock-jira·mock-slack 응답은 인프로세스 mock이 돌려주는 순서를 그대로 쓴다.
"""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from govops.agent.graph import run_task
from govops.gateway.client import ChatResult, Usage
from govops.replay.a_replay import ReplayToolExecutor
from govops.replay.a_through import A_TOOLS
from govops.replay.llm import _canonical, _hash, _request
from govops.tools.registry import ToolOutcome


class RekeyAborted(RuntimeError):
    """불일치가 있어 아무 파일도 바꾸지 않았다(값은 담지 않는다)."""


def legacy_request(messages, tools, max_output_tokens) -> dict:
    """키 순서 결함 수정 전의 요청 키 입력(도구 결과 문자열을 그대로 둔다)."""
    return {
        "messages": list(messages),
        "tools": list(tools),
        "max_completion_tokens": max_output_tokens,
    }


def _resolve(schema: dict, defs: dict) -> dict:
    if "$ref" in schema:
        return _resolve(defs[schema["$ref"].rsplit("/", 1)[-1]], defs)
    for key in ("anyOf", "oneOf"):
        if key in schema:
            branches = [b for b in schema[key] if b.get("type") != "null"]
            if len(branches) == 1:
                return _resolve(branches[0], defs)
    return schema


def live_order(value: Any, schema: dict, defs: dict) -> Any:
    """응답 값을 계약 스키마의 `required` 순서(= 응답 모델 필드 순서)로 정렬한다."""
    schema = _resolve(schema, defs)
    if isinstance(value, dict) and schema.get("type") == "object":
        properties = schema.get("properties", {})
        order = [k for k in schema.get("required", []) if k in value]
        if set(order) != set(value):
            raise RekeyAborted("response keys not covered by the contract")
        return {k: live_order(value[k], properties.get(k, {}), defs) for k in order}
    if isinstance(value, list) and schema.get("type") == "array":
        return [live_order(v, schema.get("items", {}), defs) for v in value]
    return value


def contract_schemas(directory: Path) -> dict[str, dict]:
    result = {}
    for tool in A_TOOLS:
        data = json.loads((directory / f"{tool}.v1.json").read_text(encoding="utf-8"))
        result[tool] = data["output_schema"]
    return result


class LiveOrderExecutor:
    """기록한 A 응답을 live 키 순서로 돌려주는 재생 실행기(순서만 바꾼다)."""

    def __init__(self, inner: ReplayToolExecutor, schemas: dict[str, dict], delegate):
        self._inner = inner
        self._schemas = schemas
        self._delegate = delegate

    async def call(self, name, arguments, *, user_id):
        outcome = await self._inner.call(name, arguments, user_id=user_id)
        if name not in self._schemas:
            return outcome
        schema = self._schemas[name]
        payload = live_order(outcome.payload, schema, schema.get("$defs", {}))
        return ToolOutcome(payload, outcome.is_error, outcome.observed_ids)


@dataclass
class TaskReport:
    task_id: str
    calls: int = 0
    legacy_matches: int = 0
    new_keys: list[str] = field(default_factory=list)
    changed: int = (
        0  # 새 방식 키가 기존 키와 달라 바뀌는 호출 수(도구 결과가 없는 호출은 같다)
    )


class _Probe:
    """기록한 응답을 그대로 돌려주면서 호출마다 기존·새 키를 계산한다."""

    def __init__(self, rows: list[dict]):
        self._rows = rows
        self.report = TaskReport("")

    async def complete(
        self, *, messages, tools, task_id, user_id, max_output_tokens=1024
    ) -> ChatResult:
        index = self.report.calls
        if index >= len(self._rows):
            raise RekeyAborted("more calls than recorded")
        row = self._rows[index]
        self.report.calls += 1
        legacy = _hash(legacy_request(messages, tools, max_output_tokens))
        self.report.legacy_matches += legacy == row["request_key"]
        self.report.new_keys.append(_hash(_request(messages, tools, max_output_tokens)))
        return ChatResult(
            message=json.loads(_canonical(row["response_message"])),
            model=row["response_model"],
            usage=Usage(**row["usage"]),
            cost_usd=row["cost_usd"],
        )


async def check_task(
    spec, *, llm_path: Path, a_path: Path, mock_executor, graph_factory, schemas
) -> TaskReport:
    lines = llm_path.read_text(encoding="utf-8").splitlines()
    if any(line != _canonical(json.loads(line)) for line in lines):
        raise RekeyAborted("recording is not in canonical form")
    _header, *rows = [json.loads(line) for line in lines]
    probe = _Probe(rows)
    probe.report.task_id = spec.task_id
    tools = LiveOrderExecutor(
        ReplayToolExecutor.load(a_path, inner=mock_executor), schemas, mock_executor
    )
    await run_task(
        graph_factory(probe, tools, InMemorySaver()),
        task_id=spec.task_id,
        user_id=spec.user,
        request=spec.request,
    )
    return probe.report


def rewrite(llm_path: Path, new_keys: list[str]) -> str:
    """헤더와 각 호출의 다른 필드는 그대로 두고 `request_key`만 바꾼 파일 내용."""
    header, *rows = [
        json.loads(line) for line in llm_path.read_text(encoding="utf-8").splitlines()
    ]
    if len(rows) != len(new_keys):
        raise RekeyAborted("call count mismatch")
    return "".join(
        _canonical(row) + "\n"
        for row in [
            header,
            *({**r, "request_key": k} for r, k in zip(rows, new_keys, strict=True)),
        ]
    )


async def rekey(
    specs,
    *,
    llm_dir: Path,
    a_dir: Path,
    mock_executor,
    graph_factory,
    schemas,
    apply: bool,
) -> list[TaskReport]:
    """모든 task의 모든 호출이 기존 키와 일치할 때만 키를 바꾼다."""
    reports, contents = [], {}
    for spec in specs:
        path = llm_dir / f"{spec.task_id}.jsonl"
        report = await check_task(
            spec,
            llm_path=path,
            a_path=a_dir / f"{spec.task_id}.jsonl",
            mock_executor=mock_executor,
            graph_factory=graph_factory,
            schemas=schemas,
        )
        reports.append(report)
        recorded = len(path.read_text(encoding="utf-8").splitlines()) - 1
        if report.calls != recorded or report.legacy_matches != report.calls:
            raise RekeyAborted(
                f"{spec.task_id}: legacy key match {report.legacy_matches}/{recorded}"
            )
        old_keys = [
            json.loads(line)["request_key"]
            for line in path.read_text(encoding="utf-8").splitlines()[1:]
        ]
        report.changed = sum(
            o != n for o, n in zip(old_keys, report.new_keys, strict=True)
        )
        contents[path] = rewrite(path, report.new_keys)
    if apply:
        staged = []
        try:
            for path, text in contents.items():
                fd, temporary = tempfile.mkstemp(prefix=".rekey-", dir=path.parent)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                staged.append((Path(temporary), path))
            for temporary, path in staged:
                os.replace(temporary, path)
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)
    return reports

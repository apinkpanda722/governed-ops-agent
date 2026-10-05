# Step 0: llm-record-replay

## 읽어야 할 파일

- `/AGENTS.md` (계층 경계 표의 `gateway`·`replay`·`agent` 행, 토큰 비노출, 기록 재생 CRITICAL 규칙)
- `/docs/ARCHITECTURE.md` ("디렉토리 구조"의 `replay/`, golden trace), `/docs/ADR.md` (ADR-004, ADR-009), `/docs/EVAL.md` ("golden trace")
- `/src/govops/gateway/client.py` (`ChatClient` Protocol, `ChatResult`, `Usage`, `LiteLLMChatClient`가 요청에 싣는 모델 ID·`max_output_tokens`·파라미터), `/src/govops/agent/prompts.py` (`SYSTEM_PROMPT`), `/src/govops/agent/graph.py` (`agent_tool_definitions` 사용처), `/src/govops/tools/registry.py` (도구 정의)
- `/src/govops/replay/recordings.py` (비밀·합성 문서 검사 방식), `/tests/test_replay_recordings.py`, `/tests/test_gateway_client.py`, `/tests/test_architecture.py`

## 작업

에이전트의 모델 호출(`ChatClient`)을 **기록하고 그대로 재생**하는 두 클라이언트를 `replay/`에 만든다. TDD로 테스트를 먼저 쓴다.

### 만들 것 — `src/govops/replay/llm.py`

```python
def prompt_hash(*, system_prompt: str, tools: Sequence[Mapping], model: str, params: Mapping[str, Any]) -> str
class RecordingChatClient:   # ChatClient. 안쪽 실제 클라이언트에 위임하고 응답을 기록한다
    def __init__(self, inner: ChatClient, *, task_id: str, directory: Path, system_prompt: str, tools, model: str, params: Mapping)
    async def complete(...) -> ChatResult
    def finish(self) -> Path          # 기록 파일 확정(원자적 쓰기)
class ReplayChatClient:      # ChatClient. 기록을 순서·키대로 돌려주고 네트워크를 쓰지 않는다
    @classmethod
    def load(cls, path: Path, *, system_prompt: str, tools, model: str, params) -> "ReplayChatClient"
class RerecordRequired(RuntimeError): ...   # 메시지에 "재기록 필요"를 넣는다
```

기록 파일은 `data/replay/llm/<task_id>.jsonl`이다(커밋 대상). 첫 줄은 헤더(`prompt_hash`, `model`, `params`, `task_id`, 기록 시각 ISO, 호출 수), 이후 한 줄이 한 호출이다(`request_key`, `response_message`, `response_model`, `usage`, `cost_usd`).

핵심 규칙:

1. **프롬프트 해시**는 `SYSTEM_PROMPT`, 도구 정의 전체, **모델 ID**, **호출 파라미터**(`temperature`, `max_completion_tokens`(= `max_output_tokens`), 그 밖에 요청에 실리는 설정)를 정규화 JSON(키 정렬)으로 직렬화해 SHA-256을 낸다. 하나라도 바뀌면 해시가 바뀐다.
2. **재생 검증**: `load`가 헤더 해시와 현재 해시를 비교해 다르면 `RerecordRequired`("재기록 필요: 프롬프트·모델·도구 정의·파라미터 변경")를 던진다. 재생 중 요청 키(직전까지의 메시지 이력과 도구 정의의 해시)가 기록과 다르거나 기록이 모자라면 같은 예외를 던진다. 재생은 응답을 편집하지 않는다.
3. **호출마다 실제 응답 모델을 기록**한다(`response_model`). 현재 LiteLLM 설정에 fallback이 없으므로 M2 dev는 주 모델만 쓴다. 재생 때 `ChatResult.model`에 기록한 응답 모델을 그대로 돌려준다.
4. **기록 안전**: 기록 직전 응답·요청 키 전체를 검사해 `Authorization`·`Bearer `·`api_key`·`sk-` 형태 값·`.env` 비밀로 보이는 값이 있으면 `RecordingRejected`로 거부한다(기록 파일에 쓰지 않는다). 메시지에 포함된 문서 ID가 `syn-`으로 시작하지 않는 문서 ID 형태이면 거부한다(도구 결과는 A 응답이므로 이 클라이언트가 보는 메시지에 비합성 문서가 들어 있으면 기록하지 않는다). 거부 사유에는 값을 담지 않는다.
5. 계층: `replay/`는 `govops.agent`·`govops.api`·`govops.broker`를 import할 수 없다. `ChatClient`·`ChatResult`는 `gateway`에서 import해도 되는지 `tests/test_architecture.py`로 확인하고(`replay`의 금지 목록에는 `gateway`가 없다), 시스템 프롬프트·도구 정의는 **인자로 받는다**(replay가 agent를 import하지 않도록).

### 테스트 (`tests/test_replay_llm.py`)

- 가짜 `ChatClient`로 기록 → 같은 입력으로 재생해 메시지·모델·사용량이 같다. 재생은 안쪽 클라이언트를 호출하지 않는다.
- 해시 입력 5가지(시스템 프롬프트, 도구 정의, 모델 ID, `temperature`, `max_completion_tokens`) 각각을 바꾸면 해시가 달라지고 `load`가 "재기록 필요"로 실패한다. 같은 입력은 같은 해시다.
- 재생 중 메시지 이력이 다르면(요청 키 불일치)·기록보다 호출이 많으면 `RerecordRequired`.
- 응답 모델이 호출마다 기록되고(`response_model`) 재생에서 그대로 나온다.
- 응답에 `Authorization`·`Bearer `·`sk-` 값이 있으면 기록이 거부되고 파일에 쓰이지 않는다. 비합성 문서 ID가 있으면 거부된다.
- 파일 쓰기는 원자적(임시 파일 후 교체)이고 `finish` 전에는 최종 파일이 없다.
- 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙(토큰 비노출, 실제 API 호출 금지, 합성 문서 `syn-*`만 기록)을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 안쪽 `LiteLLMChatClient`나 `gateway/`를 수정하지 마라. 이유: 기록은 `ChatClient` 경계 바깥에서만 한다.
- 응답 메시지를 편집·정규화해 기록하지 마라. 이유: 재생은 live 응답 그대로여야 한다.
- 프롬프트 해시 입력에서 모델 ID나 파라미터를 빼지 마라. 이유: 모델이나 설정이 바뀌면 기록이 무효여야 한다.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(AGENTS.md CRITICAL).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/tasks.json`, `data/eval/meta.json`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

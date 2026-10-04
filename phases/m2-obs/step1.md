# Step 1: obs-instrument

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표, 토큰 비노출·신원 규칙), `/docs/ARCHITECTURE.md` ("trace 구조"), `/docs/ADR.md` (ADR-009)
- 이전 step 산출물: `/src/govops/obs/trace.py`(`trace`·`span`·`set_attributes`·`configure`), `/src/govops/obs/mask.py`, `/src/govops/obs/jsonl.py`, `/src/govops/guardrails/pii.py`, `/tests/test_obs_trace.py`
- 계측 대상: `/src/govops/api/tasks.py`(`TaskService._run`), `/src/govops/api/wiring.py`(운영 조립), `/src/govops/agent/graph.py`(`model`·`read_tools` 노드, `run_task`), `/src/govops/gateway/client.py`(`LiteLLMChatClient.complete`), `/src/govops/tools/registry.py`(`ToolRuntime.call`), `/tests/test_agent_graph.py`, `/tests/test_api_tasks.py`, `/tests/test_e2e_isolation.py`, `/tests/test_architecture.py`

## 작업

한 task = 한 trace가 되도록 **API·그래프·도구·모델 호출에 span을 단다.** 계측은 동작을 바꾸지 않는다. TDD로 테스트를 먼저 쓴다.

### 만들 것

1. **trace 시작·종료 — `api/tasks.py`**: `TaskService._run`이 `obs.trace(task_id, user_id=user_id)`로 루트 TASK span을 연다. 루트 span 속성: 요청 문장(`role=request`), 종료 시 최종 상태·단계 수·오류 코드·최종 답변(`role=answer`)·인용 문서 ID. task가 취소·실패해도 trace는 닫힌다(`status="error"`). `get`의 응답 형식은 바꾸지 않는다.
2. **그래프 노드 — `agent/graph.py`**: `model` 노드는 `GRAPH_NODE` span(이름 `model`, 속성: 단계 번호), `read_tools` 노드는 `GRAPH_NODE` span(이름 `read_tools`). 모델 호출은 `MODEL_CALL` span(이름 `agent-primary`, 속성: 입력·출력·캐시 토큰 수, 비용 USD, 지연, 도구 호출 개수, 오류 코드). **프롬프트·응답 메시지 원문을 속성으로 넣지 않는다**(모델 호출 내용은 최종 답변 span과 도구 span으로 충분하다). 도구 호출은 `TOOL_CALL` span(이름 = 도구 이름, 속성: 인자(`role=tool_args`), 결과 ID 목록과 앞부분 발췌(`role=tool_result`), 성공 여부, 오류 코드). 도구 결과 전체 본문은 span에 넣지 않는다(`mask`가 ID와 300자 발췌만 남긴다).
3. **게이트웨이 — `gateway/client.py`**: `complete`를 `MODEL_CALL` span이 아니라 **그래프의 `MODEL_CALL` span에 속성만 추가**하는 방식으로 연결한다(`set_attributes`로 토큰·비용). 게이트웨이가 span을 새로 만들지 않아 모델 호출이 한 번만 센다. (LiteLLM에 trace ID를 넘기는 일은 다음 step에서 한다.)
4. **도구 런타임 — `tools/registry.py`**: 도구 호출 span은 그래프(위 2)에서 만든다. 도구 클라이언트 내부 재시도·서킷은 M3 범위이므로 이 step에서는 건드리지 않는다.
5. **운영 조립 — `api/wiring.py`**: 서비스 시작 시 `obs.configure([JsonlSink()])`를 호출하고, 환경 변수의 비밀 값(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `LITELLM_MASTER_KEY`, `LITELLM_AGENT_KEY`, `POSTGRES_PASSWORD`, `LANGFUSE_SECRET_KEY` 등 이름에 `KEY`·`SECRET`·`PASSWORD`·`TOKEN`이 들어가는 변수)를 `register_secret_values`로 등록한다. 값은 로그·출력에 남기지 않는다. broker가 읽는 토큰 파일 경로·내용은 여기서 읽지 않는다(`obs`·`api`가 `broker`를 import하지 않는다).
6. **Make 변화 없음.** 문서는 마지막 step에서 한다.

### 설계 규칙

- span 속성에 사용자·팀·직급을 새로 만들지 않는다: `user_id`는 루트 span에만 있다(라벨 `demo-<team>-g<grade>`는 합성 데이터).
- 계측 코드는 `try/except`로 task 본문을 가리지 않는다: `obs`가 sink 오류를 삼키므로 호출부는 단순해야 한다.
- 에이전트의 상태·checkpoint에 trace 정보를 넣지 않는다(trace_id = task_id가 이미 있다).

### 테스트 (`tests/test_obs_instrument.py` + 기존 테스트 보강)

- 가짜 `ChatClient`·가짜 `ToolExecutor`·`InMemorySaver`로 그래프를 실행해 span 구조를 검증한다: 루트 TASK 하나, 그 아래 `model`·`read_tools` GRAPH_NODE, 모델 호출·도구 호출 span의 부모-자식, 단계 수와 span 수 일치, 루트 span 속성에 요청·최종 답변(PII 센티널이 마스킹됨)이 있다.
- 도구 결과 본문 센티널이 300자 이후에 있을 때 trace에 나타나지 않는다. 도구 인자의 비밀·PII 센티널이 마스킹된다.
- 프롬프트·응답 메시지 원문이 span 속성 어디에도 없다.
- 실패 경로(`max_steps`, 게이트웨이 오류)에서도 루트 span이 닫히고 `status="error"`이며 task 결과 형식은 이전과 같다.
- `TaskService`: 두 사용자 task가 서로 다른 trace_id(= 각 task_id)를 가지고 한 trace의 span에 다른 사용자 task의 데이터가 없다. API 응답 본문에 trace 정보가 없다.
- 저장된 JSONL을 전체 스캔해 등록한 비밀 값·`Authorization`·`Bearer`가 없다(e2e 격리 테스트 확장: 실제 API·그래프·ToolRuntime·replay A 경로).
- 기존 테스트가 그대로 통과한다(동작 불변). 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - trace 하나 = task 하나이고 trace_id가 task_id인가? 모델 호출이 한 번만 span으로 세어지는가?
   - 프롬프트·응답 원문과 도구 결과 전체 본문이 span에 없고, 요청·최종 답변은 PII만 마스킹된 원문인가?
   - 토큰·키가 trace에 없고(스캔 테스트), `obs`·`api`가 `broker`를 import하지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 프롬프트·응답 메시지 원문을 span 속성에 넣지 마라. 이유: 모델 호출 내용은 LiteLLM 콜백에서 끄고 에이전트 span은 요청·답변·도구 발췌만 남긴다.
- 에이전트 상태·checkpoint·API 응답에 trace 정보를 추가하지 마라. 이유: trace_id = task_id이며 상태 스키마는 M3 승인 설계와 맞물린다.
- 도구 클라이언트의 재시도·서킷·타임아웃을 구현하지 마라. 이유: M3 범위다.
- 계측 때문에 에이전트 동작(도구 선택·종료 조건·오류 코드)을 바꾸지 마라. 이유: 기존 e2e 격리 테스트가 보장하는 동작이다.
- 실제 LiteLLM·Langfuse·A MCP에 접속하는 테스트를 만들지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

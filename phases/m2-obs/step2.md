# Step 2: obs-langfuse

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약 — 실제 API 호출·docker 기동은 사용자가 한다), `/docs/ARCHITECTURE.md` ("trace 구조", 배치도), `/docs/ADR.md` (ADR-009)
- 이전 step 산출물: `/src/govops/obs/trace.py`(`SpanRecord`·`TraceSink`·`configure`), `/src/govops/obs/mask.py`, `/src/govops/obs/jsonl.py`, `/src/govops/api/wiring.py`(sink 구성)
- `/src/govops/gateway/client.py`(`LiteLLMChatClient.complete`의 요청 본문 `metadata`·`user`), `/tests/test_gateway_client.py`
- `/infra/docker-compose.yml`(litellm 서비스, profile `obs`의 Langfuse 서비스들), `/infra/litellm/config.yaml`, `/tests/test_compose.py`, `/Makefile`, `/tests/test_makefile.py`, `/.env.example`
- 설치된 `langfuse` 패키지(≥ 4.16)의 공개 API: trace ID를 지정해 observation(span)을 만들고 flush하는 방법(`.venv`의 `site-packages`에서 **읽기만**. 이름을 추측하지 말고 실제 시그니처를 확인한다. 4.x는 OpenTelemetry 기반이며 trace ID는 32자리 소문자 hex다)

## 사전 확인된 사실 (공식 문서 기준, LiteLLM Langfuse 연동)

- LiteLLM이 만드는 Langfuse generation을 **이미 존재하는 trace에 붙이려면** 요청 `metadata`에 `existing_trace_id`를 넣는다. 새 trace를 만들 때 쓰는 `trace_id`와는 함께 쓰지 않는다. 32자리 hex가 아닌 값은 해시되고, UUID 형식은 하이픈이 제거된다. 우리 `task_id`는 이미 32자리 hex다.
- 기타 trace 메타데이터 키: `trace_user_id`, `tags`, `generation_name`, `session_id`.
- LiteLLM 프록시 설정에서 `litellm_settings: {success_callback: ["langfuse"], turn_off_message_logging: true}`를 쓰면 메시지·응답 내용은 Langfuse로 가지 않고 메타데이터(토큰·비용·지연)만 간다. `LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`·`LANGFUSE_HOST` 환경 변수가 필요하다.
- **이 문서 내용은 최신판 기준이다.** 고정한 LiteLLM `v1.103.3`에서 같은 키가 동작하는지는 이 step에서 확정하지 않고 사용자 스모크(`make smoke-obs`)에서 확인한다.

## 작업

(1) 같은 span을 Langfuse로도 내보내는 **Langfuse sink**, (2) LiteLLM 호출에 **trace ID 메타데이터**를 넘기는 변경, (3) LiteLLM의 Langfuse 콜백을 **obs 프로필에서만** 켜는 compose 구성을 만든다. 핵심 스택은 Langfuse 없이 뜬다(ADR-009). TDD로 테스트를 먼저 쓴다. 실제 접속·docker 기동은 하지 않는다.

### 만들 것

1. **`src/govops/obs/langfuse.py`** — `LangfuseSink`(`TraceSink` 구현).
   ```python
   class LangfuseSink:
       def __init__(self, client: Any) -> None            # 테스트에서는 가짜 client를 주입한다
   def langfuse_sink_from_env(env: Mapping[str, str] | None = None) -> LangfuseSink | None
   ```
   - `langfuse_sink_from_env`는 `LANGFUSE_HOST`·`LANGFUSE_PUBLIC_KEY`·`LANGFUSE_SECRET_KEY`가 **모두 있을 때만** SDK 클라이언트를 만들어 sink를 돌려주고, 하나라도 없으면 `None`이다(그러면 JSONL만 쓴다). SDK import는 이 함수 안에서 한다(키 없이 `make verify`가 통과해야 한다).
   - span → Langfuse 매핑: trace ID = `SpanRecord.trace_id`(= task_id, 32 hex). 루트 TASK span → trace(이름 `task`, 사용자 ID, 입력 = 마스킹된 요청, 출력 = 마스킹된 최종 답변, 태그 `govops`). `TOOL_CALL` → 도구 observation, 그 밖 → 일반 span. 부모-자식 관계·시작/종료 시각·상태(`error` → 오류 수준)를 유지한다. **sink가 받는 `SpanRecord`는 이미 마스킹되어 있으므로 속성을 그대로 전송한다**(추가 가공으로 원문을 되살리지 않는다).
   - `close_trace`에서 flush한다. 네트워크·SDK 오류는 삼키고 `obs`의 `sink_error_count` 경로로 센다(task를 깨뜨리지 않는다. fail-open). Langfuse가 꺼져 있어도 JSONL은 항상 쓴다.
2. **`api/wiring.py`**: `configure`에 `[JsonlSink(), *([langfuse_sink] if langfuse_sink_from_env() else [])]`를 넘긴다. Langfuse 변수가 없으면 JSONL만이다.
3. **`gateway/client.py`**: 요청 `metadata`에 LiteLLM 키를 추가한다(요청 본문 `body["metadata"]`와 SDK 호출의 `extra_body["metadata"]` **둘 다** 같은 값):
   ```python
   {"task_id": task_id, "existing_trace_id": task_id, "trace_user_id": user_id,
    "generation_name": "agent-primary", "tags": ["govops"]}
   ```
   `user` 필드와 기존 `task_id` 키는 그대로 둔다. `parent_observation_id`는 쓰지 않는다(중첩 연결은 M2 범위 밖). 비용 예약·정산·오류 처리 동작은 바꾸지 않는다. `task_id`가 32자리 소문자 hex가 아니면 `existing_trace_id`를 넣지 않는다(해시 변환으로 trace가 갈라지는 일을 막는다).
4. **compose·LiteLLM 구성** (`infra/`): 핵심 스택(litellm·postgres·mock)은 **Langfuse 변수 없이 지금처럼 뜬다**. LiteLLM 콜백은 obs 사용 시에만 켠다.
   - `infra/litellm/config.obs.yaml` 신규: 기존 `config.yaml`과 같은 내용 + `litellm_settings: {success_callback: ["langfuse"], failure_callback: ["langfuse"], turn_off_message_logging: true}`. 주석에 "프롬프트·응답 원문은 obs 마스킹을 거치지 않으므로 끈다(요청·답변은 에이전트 span이 PII 마스킹 후 보낸다)"를 적는다.
   - `infra/docker-compose.yml`: litellm 서비스의 설정 파일 마운트를 `${LITELLM_CONFIG_FILE:-./litellm/config.yaml}:/app/config.yaml:ro`로 바꾸고, litellm의 환경에 `LANGFUSE_HOST: http://langfuse-web:3000`, `LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY:-}`, `LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY:-}`를 추가한다(키가 없으면 빈 값 → 기본 설정에서는 사용되지 않는다). litellm이 `langfuse-web`에 `depends_on`을 걸지 않는다(핵심 스택이 Langfuse 없이 떠야 한다).
   - `Makefile`: `obs-up`(사용자 실행) = `LITELLM_CONFIG_FILE=./litellm/config.obs.yaml docker compose --env-file .env -f infra/docker-compose.yml --profile obs up -d`, `obs-down`(사용자 실행) = obs 프로필 서비스를 정지하고 litellm을 기본 설정으로 되돌린다(`docker compose --env-file .env -f infra/docker-compose.yml --profile obs stop <Langfuse 계열 서비스 이름들>` 후 `docker compose --env-file .env -f infra/docker-compose.yml up -d litellm`). 서비스 이름은 compose 파일에서 읽는다. 각각 위에 "사용자 실행" 주석. `verify`가 호출하지 않는다. 모든 `docker compose` 줄은 `--env-file .env`를 쓴다(기존 테스트).
   - `.env.example`: `LANGFUSE_HOST`(호스트의 에이전트가 쓰는 값. 형식 `http://127.0.0.1:3000`), `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`를 **이름만** 추가한다(주석: "Langfuse 첫 기동 후 UI에서 프로젝트·API 키를 만들어 .env에 넣는다"). 값은 비워 둔다.

### 테스트 (`tests/test_obs_langfuse.py`, `tests/test_gateway_client.py`·`tests/test_compose.py`·`tests/test_makefile.py` 확장)

- 가짜 Langfuse client로: 루트·자식 span이 올바른 trace ID·부모 관계·이름으로 전달된다, 속성에 비밀·PII 센티널이 없다(sink가 받은 마스킹 레코드 그대로), `close_trace`에서 flush, 클라이언트가 예외를 던져도 sink가 예외를 밖으로 내지 않고 오류 카운터가 오른다.
- `langfuse_sink_from_env`: 세 변수 중 하나라도 비면 `None`, 모두 있으면 sink(SDK 생성자는 monkeypatch한 가짜). 비밀 값이 `repr`·예외에 없다.
- 게이트웨이: 요청 본문과 `extra_body`의 `metadata`에 `existing_trace_id`(= task_id)·`trace_user_id`·`generation_name`·`tags`가 있다. `trace_id` 키는 없다. 32 hex가 아닌 task_id면 `existing_trace_id`가 없다. 기존 비용 예약·정산 테스트 불변.
- compose: litellm이 `langfuse-web`에 의존하지 않는다, 기본 마운트가 `config.yaml`이고 `LITELLM_CONFIG_FILE`로 바꿀 수 있다. `config.obs.yaml`에 `turn_off_message_logging: true`와 langfuse 콜백이 있고 `config.yaml`에는 콜백이 없다. 두 설정의 `model_list`가 같다. `obs-up`·`obs-down`이 사용자 실행 타깃이고 `verify`에서 호출되지 않으며 `docker compose` 줄이 `--env-file .env`를 쓴다. `.env.example`에 새 이름 3개가 값 없이 있다.
- 키 없이(`LANGFUSE_*` 미설정) `make verify`가 통과한다.
- 계층 가드 통과(`obs`가 `broker`를 import하지 않는다. `langfuse` SDK는 `obs/langfuse.py`에서만 import한다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - Langfuse 변수가 없어도 핵심 스택·`make verify`가 영향을 받지 않는가? Langfuse 실패가 task를 깨뜨리지 않는가(fail-open)?
   - LiteLLM 콜백에서 메시지 내용이 꺼지는가(`turn_off_message_logging`)? trace ID 키가 `existing_trace_id`인가?
   - AGENTS.md CRITICAL 규칙(토큰·키 비노출, 실제 호출은 사용자 실행)을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `docker`·`docker compose`·`make obs-up`·`make obs-down`을 실행하지 마라. 이유: 컨테이너 기동은 사용자가 한다.
- Langfuse·LiteLLM에 실제로 접속하거나 `LANGFUSE_*` 값을 코드·문서·테스트에 쓰지 마라. 이유: 키는 `.env`에만 둔다.
- 기본 `infra/litellm/config.yaml`에 Langfuse 콜백을 넣지 마라. 이유: 핵심 스택은 Langfuse 없이 뜬다(ADR-009).
- LiteLLM 콜백으로 프롬프트·응답 원문을 Langfuse에 보내게 두지 마라. 이유: 이 경로는 `obs` 마스킹을 우회한다.
- `trace_id`와 `existing_trace_id`를 함께 보내지 마라. 이유: LiteLLM 문서상 상호 배타 키다.
- Langfuse 컨테이너 이미지·버전·compose의 Langfuse 서비스 정의를 바꾸지 마라. 이유: Redis·MinIO digest·보안 재확인은 사용자 작업이다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

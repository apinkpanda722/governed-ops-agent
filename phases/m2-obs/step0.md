# Step 0: obs-core

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약, 계층 경계 표의 `obs`·`guardrails`·`agent`·`gateway` 행, "토큰은 state·checkpointer·trace·로그에 두지 않는다" 규칙)
- `/docs/ARCHITECTURE.md` ("trace 구조", "디렉토리 구조", 3지점 마스킹), `/docs/ADR.md` (ADR-009), `/docs/THREAT_MODEL.md` (TH4 토큰 유출, V4)
- `/tests/test_architecture.py` (계층 가드: `obs`는 `govops.broker`를, `guardrails`는 다른 govops 계층·`litellm`·`langgraph`·`fastapi`를 import할 수 없다), `/src/govops/obs/__init__.py`, `/src/govops/guardrails/__init__.py`
- `/.gitignore` (`logs/`는 커밋하지 않는다), `/src/govops/replay/recordings.py` (토큰·Authorization 검사 방식 참고)

## 작업

task 하나 = trace 하나의 **span 모델·컨텍스트·로컬 JSONL sink·전송 전 마스킹**을 `obs/`에 만들고, 마스킹이 쓰는 한국 형식 PII 판정을 `guardrails/pii.py`(순수 Python)에 둔다. 이 step은 계측(코드에 span을 다는 일)을 하지 않는다(다음 step). TDD로 테스트를 먼저 쓴다.

### 만들 것

1. **`src/govops/guardrails/pii.py`** — 순수 Python(표준 라이브러리만). M4 Guardrail이 재사용한다.
   ```python
   @dataclass(frozen=True) class PiiMatch: kind: str; start: int; end: int
   def find_pii(text: str) -> list[PiiMatch]            # 겹침은 긴 쪽 우선, 시작 위치 순
   def mask_pii(text: str, *, placeholder: str = "[PII:{kind}]") -> str
   ```
   종류(재현율 우선, 형식 기반): `rrn`(주민·외국인등록번호 `######-#######`, 하이픈 없는 13자리도 날짜 형식이 맞을 때), `mobile`(010·011·016~019 + 하이픈/공백/무구분), `landline`(지역번호 포함), `email`, `card`(13~19자리, 4자리 묶음, Luhn 검사 통과), `biz_reg`(사업자등록번호 `###-##-#####`), `passport`(여권번호 영문 1자 + 8자리). 한 종류의 정규식이 서로 다른 종류와 겹칠 때(예: 13자리 숫자) 오탐보다 마스킹을 택한다. 전각 숫자·하이픈 변형(`NFKC`)도 잡는다. 이 모듈은 `govops`의 다른 모듈을 import하지 않는다.
2. **`src/govops/obs/mask.py`** — 전송 전 마스킹(단일 지점).
   ```python
   def register_secret_values(values: Iterable[str]) -> None   # 환경 변수의 키·토큰 값을 등록(길이 8 미만은 무시). 값을 저장·로깅·repr에 노출하지 않는다
   def scrub_secrets(text: str) -> str                         # 항상 적용: Authorization 헤더 값, Bearer 토큰, sk-/pk- 형 키, JWT(eyJ…), 등록된 비밀 값 → "[REDACTED]"
   def mask_text(text: str, *, role: Role) -> str
   def mask_attributes(attributes: Mapping[str, Any]) -> dict[str, Any]
   ```
   **마스킹 기준(고정)**:
   - **항상**: 모든 문자열에 `scrub_secrets`. 키 이름이 `authorization`·`token`·`secret`·`api_key`·`password`·`cookie`(대소문자 무시, 부분 일치)인 속성은 값 전체를 `"[REDACTED]"`로 바꾼다.
   - **사용자 요청·최종 답변·초안(`role` = `request`/`answer`/`draft`)**: **PII만 마스킹한 원문**이다(`mask_pii` + `scrub_secrets`). 요약·해시로 대체하지 않는다. 운영 품질 검수에서 사람이 읽어야 하기 때문이다. 길이 상한 8,000자, 넘으면 끝에 `…(잘림, 원문 N자)`를 붙인다.
   - **도구 결과(`role` = `tool_result`)**: 문서·티켓 **ID 목록**(`ids`)과 **앞부분 발췌**(상한 `TOOL_EXCERPT_MAX_CHARS = 300`, 넘으면 `…(잘림)`)만 남긴다. 발췌에도 `mask_pii` + `scrub_secrets`를 적용한다. 전체 본문은 남기지 않는다.
   - **도구 호출 인자(`role` = `tool_args`)**: JSON 문자열이면 그대로 두되 PII·비밀 마스킹을 적용한다(검색어 등).
   - 이 기준에 해당하지 않는 속성(숫자·불리언·짧은 식별자·상태 코드)은 그대로 둔다. 알 수 없는 `role`은 가장 보수적인 도구 결과 규칙을 적용한다.
   - 속성 값에 역할을 붙이는 방법은 재량이다(예: `Text(role, value)` 래퍼, 또는 키 이름 규약 `input.request`·`output.answer`·`output.draft`·`tool.result`·`tool.args`). 규약은 모듈 상단 주석과 테스트로 고정한다.
3. **`src/govops/obs/trace.py`** — span 모델과 컨텍스트.
   ```python
   class SpanKind(StrEnum):      # M3·M4가 쓸 종류까지 정의하되 구현은 지금 쓰는 것만
       TASK, GRAPH_NODE, TOOL_CALL, MODEL_CALL, GUARDRAIL, APPROVAL_WAIT, APPROVAL_DECISION, IDEMPOTENCY_LOOKUP, RETRY, CIRCUIT_STATE
   @dataclass class SpanRecord: trace_id: str; span_id: str; parent_id: str | None; kind: SpanKind; name: str
                                start_ts: str; end_ts: str; duration_ms: float; status: Literal["ok","error"]; attributes: dict
   class TraceSink(Protocol): def emit(self, record: SpanRecord) -> None;  def close_trace(self, trace_id: str) -> None
   @contextmanager
   def trace(task_id: str, *, user_id: str | None = None, sinks: Sequence[TraceSink] | None = None)   # 루트 TASK span을 연다. trace_id = task_id(32자리 hex 그대로)
   @contextmanager
   def span(kind: SpanKind, name: str, **attributes)                 # 현재 trace 아래 자식 span. trace 밖이면 아무 일도 하지 않는다(no-op)
   def set_attributes(**attributes) -> None                           # 현재 span에 속성 추가(예: 응답 후 토큰 수)
   def configure(sinks: Sequence[TraceSink]) -> None                  # 프로세스 기본 sink
   ```
   - 현재 trace·span은 `contextvars`로 전파한다(`asyncio.create_task`로 갈라진 작업에서도 부모가 유지되도록 `copy_context` 동작을 테스트로 확인). 비동기 컨텍스트 매니저 사용이 필요하면 `contextmanager`가 async 함수 안에서도 동작함을 테스트한다.
   - span 종료 시 `SpanRecord`를 만들고 **sink에 넘기기 직전에** `mask_attributes`를 적용한다(sink 구현이 마스킹을 건너뛸 수 없다). 예외로 끝나면 `status="error"`, 속성에 `error_type`(예외 클래스 이름만)을 넣고 예외를 그대로 전파한다. 예외 메시지는 기록하지 않는다.
   - **sink 오류는 task를 깨뜨리지 않는다**: sink의 `emit`이 예외를 던져도 삼키고 `obs_sink_errors` 카운터(프로세스 전역, 읽을 수 있는 함수 `sink_error_count()`)만 올린다. 오류 메시지·속성 값을 로그에 남기지 않는다.
   - `span_id`는 16자리 hex(`secrets.token_hex(8)`), 시각은 UTC ISO8601(`datetime.now(UTC)`), 소요 시간은 `time.perf_counter`.
4. **`src/govops/obs/jsonl.py`** — 로컬 JSONL sink.
   ```python
   class JsonlSink:
       def __init__(self, root: Path | None = None) -> None   # 기본 root: 저장소 `logs/traces/`(env `GOVOPS_TRACE_DIR`로 재정의)
   ```
   - trace마다 파일 `<root>/<trace_id>.jsonl`, span 종료 시 한 줄씩 덧붙인다(키 정렬, `ensure_ascii=False`, UTF-8). 파일 권한 0600, 디렉토리 0700(생성 시). `trace_id`가 `[0-9a-f]{32}`가 아니면 거부한다(경로 조작 방지).
   - 부모 span이 자식보다 늦게 끝나므로 루트 TASK span이 마지막 줄이다. 줄 단위 쓰기는 원자적이어야 한다(`os.write`/한 번의 `write` + flush, 동시 작업 span이 섞여도 한 줄이 깨지지 않음).
5. **계층**: `obs`는 `govops.guardrails.pii`를 import할 수 있다(허용). `obs`는 `govops.broker`를 import하지 않는다. `guardrails/pii.py`는 `govops` 다른 모듈을 import하지 않는다.

### 테스트 (`tests/test_guardrails_pii.py`, `tests/test_obs_mask.py`, `tests/test_obs_trace.py`, `tests/test_obs_jsonl.py`)

- **PII**: 종류별 양성·음성 사례(합성 번호만. 실제 번호처럼 보이는 값을 외우지 말고 형식에 맞는 가짜 값을 코드에서 조립한다), Luhn 불통과 카드 번호는 비검출, 전각 숫자·하이픈 변형 검출, 겹침 처리, `mask_pii`가 자리 표시자로 바꾸고 나머지 텍스트를 보존한다.
- **마스킹 기준**: 요청·답변·초안은 PII만 마스킹된 원문(한국어 문장 보존)이고 8,000자 초과는 잘린다. 도구 결과는 ID 목록과 300자 이하 발췌만 남고 본문 센티널(`BODY_SENTINEL_…`, 300자 뒤에 둔다)이 사라진다. 토큰·Authorization·`Bearer …`·`sk-…`·JWT·등록한 비밀 값은 **모든 role과 모든 속성에서** 제거된다. 민감 키 이름 속성은 값 전체가 `[REDACTED]`다. 등록한 비밀 값이 `repr`·예외 메시지·로그에 나오지 않는다.
- **trace**: trace_id = task_id, 루트 TASK span이 마지막에 끝난다, 중첩 span의 부모 관계, `asyncio.gather`·`create_task` 안 span의 부모 유지, trace 밖 `span()`은 no-op, 예외 시 `status="error"`·메시지 미기록·예외 전파, sink가 예외를 던져도 본문이 정상 진행하고 `sink_error_count()`가 오른다, **sink가 받는 레코드는 이미 마스킹되어 있다**(원문 속성을 넣고 sink에서 센티널 부재 확인).
- **JSONL**: 파일 이름·권한(0600/0700), 한 줄 JSON, 마지막 줄이 TASK, 비정상 trace_id 거부, 동시 쓰기에서 줄이 깨지지 않음, 저장된 파일 전체를 스캔해 비밀 값·`Authorization`·PII 센티널이 없다.
- 계층 가드(`tests/test_architecture.py`) 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 마스킹이 sink 직전 한 지점에서만 일어나고 sink가 우회할 수 없는가?
   - 요청·답변·초안은 PII만 마스킹한 원문, 도구 결과는 ID와 300자 발췌, 토큰은 항상 제거인가?
   - `obs`가 `broker`를, `guardrails`가 다른 계층을 import하지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 요청·답변·초안을 해시·길이만 남기는 방식으로 줄이지 마라. 이유: 운영 품질 검수(Langfuse 검수 대기열)에서 사람이 읽어야 하므로 PII만 마스킹한 원문이어야 한다.
- 도구 결과 전체 본문을 span에 남기지 마라. 이유: ID와 앞부분 발췌(300자)만 남긴다.
- 마스킹을 sink 구현이나 호출부에 맡기지 마라. 이유: 한 곳에서 강제해야 우회가 없다.
- 예외 메시지·토큰·키 값을 span 속성·로그·테스트 출력에 남기지 마라. 이유: 토큰 비노출 규칙.
- 에이전트·게이트웨이·도구 코드에 span을 다는 작업을 하지 마라. 이유: 다음 step의 범위다.
- 실제 Langfuse·네트워크에 접속하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

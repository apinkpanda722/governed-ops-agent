# Step 7: auth-failed-event

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `api/` 행, 고정 제약의 데모 로그인), `/docs/ADR.md`(ADR-011, ADR-012), `/docs/THREAT_MODEL.md`
- 이전 step 산출물: `/src/govops/audit/log.py`(`auth.failed` 규칙: 시스템 이벤트, `reason` enum, 선택 `suppressed`), `/src/govops/api/tasks.py`(`TaskService.audit`)
- `/src/govops/api/auth.py`(`current_user`), `/src/govops/api/app.py`(`create_app`, `POST /sessions`, `/sessions/me`), `/src/govops/api/sessions.py`(`SessionStore.resolve`: 만료 digest를 삭제한다)
- `/tests/test_api_sessions.py`·`/tests/test_api_tasks.py`·`/tests/test_api_guardrail_view.py`(`create_app` 사용 방식: 파일이 없으면 `rg -l create_app tests`로 찾는다)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-012·060) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/auth.py`, `src/govops/api/app.py`, `tests/test_api_auth_audit.py`(신규), `tests/test_api_tasks.py`·`tests/test_api_guardrail_view.py`·`tests/test_golden_trace.py`·`tests/test_api_sessions.py`·`tests/test_e2e_isolation.py`(`create_app` 인자·`app.state`에 맞춘 갱신에 한함, 단언 약화 금지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/auth.py`, `src/govops/api/app.py`, `tests/test_api_auth_audit.py`, `tests/test_api_tasks.py`, `tests/test_api_guardrail_view.py`, `tests/test_golden_trace.py`, `tests/test_api_sessions.py`, `tests/test_e2e_isolation.py`

**배경**: 인증 실패는 task가 없고 호출자를 신뢰할 수 있게 식별할 수도 없다(세션 저장소는 만료 digest를 삭제하고 로그인 라벨은 비신뢰 입력이다). 그래서 `auth.failed`는 시스템 이벤트(`actor.sub="system"`, `task_id=null`)이고 `reason`은 고정 enum만 쓴다. 토큰·라벨·헤더 값·IP는 쓰지 않는다. 미인증 요청이 체인과 `fsync`를 채우지 못하도록 프로세스당 분당 60건 상한을 둔다.

### 1. 기록 지점과 사유 (`api/auth.py`, `api/app.py`)

- `create_app`이 `app.state.audit`를 설정한다: `service`가 있으면 `service.audit`, 없으면 `None`(감사 sink가 없는 앱은 아무것도 기록하지 않는다). `create_app`의 시그니처는 바꾸지 않는다.
- `current_user` 실패(401): `Authorization` 헤더가 없거나 정확히 하나의 올바른 `Bearer <토큰>` 형식이 아니면 `missing_credentials`, 형식은 맞지만 세션이 없거나 만료면 `invalid_token`. 기록한 뒤 기존과 같은 401 응답을 낸다.
- `POST /sessions`(데모 로그인): 루프백이 아닌 호출(403)은 `loopback_required`, 등록되지 않은 사용자(404)는 `unknown_user`. 기록한 뒤 기존과 같은 응답을 낸다. 요청 본문의 사용자 라벨은 기록하지 않는다.
- **fail-open**: 감사 쓰기가 어떤 예외로 실패해도 응답 코드와 본문은 원래대로다(401·403·404). 예외 메시지·입력을 로그·응답에 남기지 않는다.
- 이 핸들러·의존성은 동기 함수(`def`)라 FastAPI가 스레드풀에서 실행하므로 직접 `audit.append`를 호출해도 이벤트 루프를 막지 않는다. `async def` 안에서 쓰는 곳이 생기면 `asyncio.to_thread`로 감싼다.

### 2. 상한 (`api/auth.py`의 작은 클래스)

- `class AuthFailureLimiter(limit: int = 60, window_seconds: float = 60.0, clock=time.monotonic)`와 스레드 안전성(`threading.Lock`). 고정 창 방식: 창마다 기록을 최대 `limit`건 허용하고 초과분은 쓰지 않고 센다. 다음 창의 **첫 허용 기록**에 직전 창에서 건너뛴 건수를 `suppressed`(1 이상의 `int`)로 넣는다(건너뛴 건수가 0이면 필드를 넣지 않는다). 창이 지나도 후속 실패가 없으면 그 건수는 남지 않는다(이 한계를 코드 주석에 한 줄로 명시한다).
- 한도·창 길이·시계는 주입 가능하다. 앱마다 별도의 limiter를 만든다(전역 상태를 쓰지 않는다: 테스트 간 오염 방지).

### 3. 테스트 (`tests/test_api_auth_audit.py`)

- `MemoryAuditLog`와 실제 `AuditLog(임시)` 양쪽: 헤더 없음·`Basic` 형식·토큰 두 개·빈 토큰 → `missing_credentials`, 모르는 토큰·만료된 세션(가짜 시계) → `invalid_token`, 모두 응답은 기존과 같은 401·`WWW-Authenticate: Bearer`.
- 로그인: 루프백이 아닌 클라이언트 → `loopback_required`(403), 모르는 사용자 → `unknown_user`(404). 기록된 레코드 어디에도 요청한 라벨·토큰·헤더 값이 없다(레코드 JSON 스캔).
- 모든 `auth.failed` 레코드: `actor.sub == "system"`, `task_id is None`.
- 감사 sink가 없는 앱(`service=None`)은 아무것도 기록하지 않고 응답은 같다. 쓰기가 실패하는 sink(`AuditWriteError`·임의 예외)에서도 응답 코드·본문이 같고 응답·캡처한 로그에 예외 문장이 없다.
- 상한(가짜 시계): 60건까지 기록되고 61번째부터 기록이 늘지 않는다. 창이 지난 뒤 첫 실패의 레코드에 `suppressed == <건너뛴 수>`가 있다. 건너뛴 것이 없으면 `suppressed` 필드가 없다. 두 앱 인스턴스의 limiter는 서로 독립이다. 여러 스레드가 동시에 실패를 일으켜도 기록 건수는 창당 `limit`을 넘지 않는다(집합·개수 단언, 순서 단언 없음: FP-012).
- `create_app`의 기존 호출처(소비자 테스트)는 시그니처가 같으므로 그대로 통과해야 한다. 소비자 테스트가 감사 레코드 개수를 정확히 단언하면 이름 기준으로 바꾼다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 또는 지시가 서로 충돌해 판단할 수 없음 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->


## 이 step의 금지사항


- 토큰·세션 digest·요청 라벨·`Authorization` 헤더 값·클라이언트 주소를 `auth.failed`에 넣지 마라. 이유: 비밀 값과 비신뢰 입력이다. 사유 enum과 건수만 남긴다.
- 인증 실패 기록 실패로 응답 코드나 본문을 바꾸지 마라. 이유: 미인증 요청이 감사 장애를 유도해 응답을 바꿀 수 있다. 응답은 항상 원래대로(fail-open)이다.
- limiter를 모듈 전역 상태로 만들지 마라. 이유: 앱·테스트 간 카운터가 새어 무작위로 실패한다. 앱 인스턴스마다 만들고 시계를 주입한다(FP-012).
- 상한 테스트에서 실제 시간 `sleep`이나 스레드 실행 순서에 기대지 마라. 이유: CI에서 흔들린다. 가짜 시계와 개수·집합 단언을 쓴다(FP-012).
- `create_app` 시그니처를 바꾸거나 소비자 테스트의 단언을 완화하지 마라. 이유: 호출처가 많다. `app.state`에 감사 sink를 설정하는 것만 추가한다(FP-060).

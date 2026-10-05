# Step 6: approval-api

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/src/govops/api/app.py`, `/src/govops/api/tasks.py`, `/src/govops/api/auth.py`, `/src/govops/api/sessions.py`, `/src/govops/api/users.py`, `/src/govops/api/wiring.py`, `/src/govops/api/cli.py`, `/tests/test_api_tasks.py`, `/tests/test_api_sessions.py`, `/tests/test_api_wiring.py`, `/tests/test_e2e_isolation.py`
- 이전 step 산출물: `/src/govops/agent/graph.py`, `/src/govops/agent/approval.py`, `/src/govops/agent/scope.py`, `/src/govops/tools/effects.py`, `/src/govops/tools/outbox.py`, `/src/govops/audit/log.py`

## 작업

**승인 API**, task 상태의 checkpoint화, **시작 시 정리**를 만든다. TDD로 테스트를 먼저 쓴다.

### TaskService (`api/tasks.py`) 변경 (#13)

- `start()`는 **초기 체크포인트**(owner·status `running`·`created_at`)를 먼저 쓰고 나서 실행을 시작한다. 그래서 `GET`은 항상 소유자를 알 수 있다. 메모리 `_owners`·`_failed`를 제거한다(상태·소유자는 checkpoint에서 읽는다).
- 시작 시 정리 `startup_cleanup()`: checkpointer의 `alist(None)`로 스레드별 **최신** checkpoint를 훑어, status가 `running`인 task를 `interrupted`로 닫는다(`awaiting_approval`은 그대로 둔다). 별도 테이블은 만들지 않는다(규모 한계: 전체 checkpoint 순회라 수백 건 규모를 전제한다. 이 한계를 코드 주석이 아니라 최종 응답 summary에 적는다). 열린 outbox 행(`outbox.list_open()`)마다 task ID로 checkpoint에서 소유자를 찾아 `reconcile_effect`로 **표식 조회만으로 확정**하고 task 상태에 반영한다. **재발송을 하지 않는다.**
- `GET /tasks/{id}` 응답: 기존 필드 + `status`(확장된 값), `draft`(소유자에게는 도구·수신처·표시 본문), `check`(`passed`·`masked`·`blocked`와 사유 안내), `expires_at`, `effect`. `interrupted`이면 `retry_hint`("다시 요청해 주세요")와 확정 결과 `effect_result`(`sent`·`not_sent`·`unknown`: 표식 조회로 확정된 값)를 넣는다. 토큰·원문 이외 비밀을 응답에 넣지 않는다.

### `POST /tasks/{id}/approval` (`api/app.py`)

- 본문: `{"decision": "approve|reject|edit_approve", "draft_sha256": "<hex>", "edited": {...}?}`. `edited`는 `edit_approve`에만 허용한다. 승인자 신원은 **세션 토큰에서만** 얻는다(본문의 사용자 필드는 무시가 아니라 스키마에서 거부).
- 응답 코드: 본문 형식 오류 422, **task가 없거나 승인자가 `NOT_FOUND`면 404**, 전사 공지에서 요청자 본인이면 **403**, 해시가 현재 대기 초안과 다르거나 이미 다른 결정이 있으면 **409**, 만료되었으면 409(`expired`로 확정), 정상 202/200과 갱신된 task 뷰.
- 정책은 `approver_decision`을 그대로 쓴다. 승인 대기가 아닌 task(`running`·종결)에 대한 요청은 같은 (task, 해시, 결정)이면 **멱등 200**(같은 응답), 아니면 409.
- **멱등성**: task별 `asyncio.Lock`으로 승인 처리를 직렬화한다. 같은 (task, 해시, 결정)의 두 번째 요청(동시·연속)은 효과를 만들지 않고 첫 요청과 같은 응답을 받는다. 최종 방어선은 outbox 선점이다.
- 만료(30분): 주입 가능한 시계로 **접근할 때** 확정한다(`approve`·`GET` 시점에 `expires_at`이 지났으면 `expired`로 resume/갱신하고 `approval.expired` 감사). 별도 주기 작업을 만들지 않는다.
- **적격 승인자의 GET**: 소유자가 아닌 사용자가 `GET /tasks/{id}`를 호출하면, `approver_decision`이 `ALLOW`이고 task가 **`awaiting_approval`이며 결정론 검사를 통과한(`passed`·`masked`) 초안**일 때만 **승인용 뷰**(초안 도구·수신처·표시 본문·검사 결과·만료 시각·해시·요청자 레이블)를 돌려준다. 그 밖(검사 전 초안, `blocked` 초안, 승인 대기 아님, 부적격)은 **404**다. 답변 본문·근거 목록은 승인용 뷰에 넣지 않는다. 이 불변식을 테스트로 고정한다.

### 조립 (`api/wiring.py`)

`open_task_service`가 outbox(`GOVOPS_DATABASE_URL`이 있으면 `PostgresOutboxStore`, 없으면 `InMemoryOutboxStore`), `AuditLog`(기본 `data/interim/audit/`), 하나의 `ToolPolicy`(읽기 `ToolRuntime`과 실행기가 공유), `ScopeIndex`, `TeamPolicy`, 사용자 목록을 만들어 그래프에 넘긴다. 에이전트 프로필은 환경 변수 `GOVOPS_AGENT_PROFILE`(`read_only` 기본, `writes`)로 고른다(`.env.example`에 이름과 설명 한 줄 추가). 시작 시 `startup_cleanup()`을 실행하고 팀 정책 파일 sha256을 감사 로그에 남긴다(허용 이벤트가 없으면 이 기록은 생략하고 summary에 적는다).

### 테스트 (가짜 chat·in-process mock·InMemory 저장소, ASGI 클라이언트)

- **F9**: 같은 승인 `asyncio.gather` 2회와 연속 2회 → 효과 1, 두 번째는 같은 응답.
- 만료 후 승인 거부(주입 시계), 타인 승인 404, TOCTOU(오래된 해시) 409, 수신처가 들어 있는 수정 요청 거부, 승인자 요청 본문에 사용자 필드 → 422.
- **T4-06형**: 요청자 6급(announce 발송) → 요청자 본인 승인은 **403**, 같은 팀 4급 승인자는 승인용 GET 가능·승인 시 효과 1이며 **요청자의 자격 증명**으로 발송됨(자격 증명 호출 기록으로 확인), 같은 팀 3급과 다른 팀 4급은 404.
- **승인자 GET 불변식**: 검사 전 초안·`blocked` 초안·이미 종결된 task는 적격 승인자에게도 404.
- 정리: 실행 중이던 task(`running` checkpoint) → 재시작 후 `interrupted`와 "다시 요청" 안내, outbox `UNCERTAIN` 행은 표식 조회로 확정되어 `effect_result`가 `sent`/`not_sent`로 나옴, 응답 유실 상태에서 정리해도 중복 0, 승인 대기 task는 재시작 뒤에도 승인 가능.
- 토큰이 응답 본문·checkpoint·trace·감사 로그에 없다(스캔 테스트). 기존 API 테스트는 수정 없이 통과(읽기 전용 프로필 기본값).

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
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 승인자 신원을 요청 본문·쿼리·헤더(Authorization 외)에서 받지 마라. 이유: 승인자 신원도 세션에서만 정한다.
- 부적격 사용자에게 task 존재를 알리지 마라(404). 403은 전사 공지의 요청자 본인에게만 준다.
- 시작 시 정리에서 POST를 보내지 마라. 이유: 자동 재발송은 하지 않는다. 확정은 표식 조회뿐이다.
- 주기 작업(스레드·스케줄러)으로 만료를 처리하지 마라. 이유: 접근 시점에 확정하는 설계다.
- 승인 대기가 아닌 task나 결정론 검사를 통과하지 못한 초안을 승인자에게 보여 주지 마라.
- `DEMO_LOGIN`·세션 바인드 규칙을 바꾸지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

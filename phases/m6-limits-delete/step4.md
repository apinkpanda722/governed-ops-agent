# Step 4: delete-route-entrypoint

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/api/app.py`(`create_app`, 대화·승인 라우트, `TASK_SERVICE`, `current_user`)
- `/src/govops/api/__main__.py`(`build_server_config`, lifespan의 서비스 교체)
- `/src/govops/api/tasks.py`(step 3 산출물: `delete_conversation`)
- `/tests/test_api_entrypoint.py`(`EXPECTED` 표, `FakeService`, `service_routes`, AST 가드), `/tests/test_api_events.py`, `/tests/test_api_web_contract.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/app.py`, `tests/test_api_entrypoint.py`, `tests/test_api_conversation_delete_route.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/app.py`, `tests/test_api_entrypoint.py`, `tests/test_api_conversation_delete_route.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다. 테스트는 임시 디렉터리·합성 데이터·테스트용 키만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 이 금지의 대상이 아니다.

**배경**: `DELETE /conversations/{conversation_id}`를 만들고, **실제 서버 진입점**(`build_server_config` + lifespan)을 거친 결선과 권한 우회 프로브를 검증한다(FP-070: `create_app(service=실제)`로만 만든 앱은 클로저와 `app.state`가 같아 자리표시자 결함을 못 잡는다).

### 1. 라우트

- `@app.delete("/conversations/{conversation_id}")`: `user_id: str = Depends(current_user)`, `tasks: TaskService = TASK_SERVICE`. `tasks.delete_conversation(user_id, conversation_id)`를 부르고 `ConversationError`는 `HTTPException(error.status_code, detail=error.code)`로 바꾼다(다른 대화 라우트와 같은 방식).
- 성공은 **200과 JSON 본문** `{"conversation_id": …, "deleted": true}`다. 204를 쓰지 않는다(BFF `proxyJson`이 응답 본문을 JSON으로 읽는다).
- 라우트 함수는 `create_app` 클로저의 이름 `service`를 읽지 않는다. 의존성은 `TASK_SERVICE`(= `request.app.state.task_service`)로만 얻는다.

### 2. `tests/test_api_entrypoint.py`

- `FakeService`에 `async def delete_conversation(self, user_id, conversation_id)`를 더하고(`self.calls.append("delete_conversation")`) `EXPECTED`에 `("DELETE", "/conversations/{conversation_id}"): (None, "delete_conversation")`을 더한다. 라우트 집합 정확 일치 단언과 AST 가드(`test_route_handlers_never_read_the_create_app_closure_service`)는 그대로 통과해야 한다.

### 3. `tests/test_api_conversation_delete_route.py`(신규): 실제 진입점 경유 프로브

`entry.build_server_config(0)`로 앱을 만들고 `entry.open_task_service`를 **실제 `TaskService`**(`InMemorySaver`·`FakeChat`·`MemoryAuditLog`·`InMemoryTurnIndex`, 합성 데이터)를 lifespan에 주입하는 비동기 컨텍스트로 바꾼다. 세션은 `app.state.sessions.issue(user)`로 발급한 Bearer 토큰이다(쿠키·토큰 리터럴을 소스에 쓰지 않는다). `docs/results/hypotheses-m6-api-authz.md`의 프로브 표(라우트 R1~R6 × 대상 S1~S4, 추가 프로브 E1~E8)를 구현한다. 요약:

- 라우트 6종(`GET /conversations/{id}`, `GET /conversations/{id}/events`, `POST /conversations/{id}/turns`, `DELETE /conversations/{id}`, `GET /tasks/{id}`, `POST /tasks/{id}/approval`)과 `GET /approvals`에 대해 **타인의(승인 대기가 아닌, `completed`) 대화 / 존재하지 않는 id / 삭제된 대화 / 형식이 틀린 id**의 응답이 라우트별로 같은 상태 코드와 같은 본문이다(`/conversations/*`는 404 `{"detail": "conversation_not_found"}`, `/tasks/*`는 404 `{"detail": "Task not found"}`). 단 **R6(`POST /tasks/{id}/approval`) × S1은 404 동일성 대상이 아니다**: 승인 권한은 기존 `approver_decision` 규칙이 정하며(FORBIDDEN이면 403 "Approval rejected"), 이 칸은 "200이 아니고 상태·감사·효과 변화 없음"만 단언한다. R6의 S2·S3·S4는 404 "Task not found"로 서로 같다. 프로브 사용자는 그 팀의 승인자가 아니다.
- 승인 대기 중 삭제 → 409 본문 `approval_pending`, 실행 중 삭제 → 409 `turn_in_progress`, 세션 없음 → 401, 첫 턴이 아닌 `turn_id`로 삭제 → 404(그 ID는 대화 ID가 아니다), 삭제 성공 뒤 같은 대화의 `GET /approvals` 목록에 나타나지 않음, 두 번째 삭제 404.
- 자리표시자 서비스(`TaskService(None)`)가 응답했다면 500이 나므로 모든 프로브가 `status_code != 500`이다.
- SSE(`/events`)는 404이고 스트림을 열지 않는다.

### 4. 소비자

- `tests/test_api_entrypoint.py`의 `EXPECTED`·`FakeService`(~에 한함, 단언 약화 금지), `tests/test_api_events.py`의 404 본문 단언은 바뀌면 안 된다.

**사용자 실행(이 step에서 실행하지 않는다)**: 없음. 라우트 결선은 `make verify`의 진입점 테스트가 검증한다.

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

- 라우트 함수에서 `create_app` 클로저의 이름 `service`를 읽지 마라. 이유: lifespan이 서비스를 교체하므로 클로저 값은 자리표시자이고 실제 서버에서 `AttributeError`가 났다(FP-070). AST 가드가 실패한다.
- 삭제 응답에 204·빈 본문을 쓰지 마라. 이유: BFF 프록시가 본문을 JSON으로 읽어 502가 된다.
- 삭제 응답·오류 본문에 대화 제목·상태·소유자 정보를 넣지 마라. 이유: 타인·없음·삭제됨이 구별되면 존재 여부가 노출된다.
- `create_app(service=실제)`로만 만든 앱으로 결선을 검증했다고 하지 마라. 이유: 클로저와 `app.state`가 같아서 이 결함을 못 잡는다(FP-070). 진입점 경유 테스트를 반드시 둔다.
- `tests/test_api_entrypoint.py`의 라우트 집합 정확 일치 단언을 느슨하게 하지 마라. 이유: 새 라우트는 표에 정확히 한 줄을 더해서만 반영한다.

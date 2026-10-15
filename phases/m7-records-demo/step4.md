# Step 4: audit-routes-entrypoint

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/api/app.py`(`create_app`, `TASK_SERVICE`, `current_user`)
- `/src/govops/api/__main__.py`(`build_server_config`, lifespan의 서비스 교체)
- `/src/govops/api/tasks.py`(step 3 산출물 `audit_view`·`audit_verify`)
- `/tests/test_api_entrypoint.py`(`EXPECTED` 표, `FakeService`, AST 가드)
- `/tests/test_api_conversation_delete_route.py`(실제 진입점 경유 프로브 형식)
- `/tests/conftest.py`(`isolated_audit_environment`)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/app.py`, `tests/test_api_entrypoint.py`, `tests/test_api_audit_route.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/app.py`, `tests/test_api_entrypoint.py`, `tests/test_api_audit_route.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: `GET /conversations/{conversation_id}/audit`와 `GET /audit/verify`를 만들고 **실제 서버 진입점**(`build_server_config` + lifespan)을 거친 결선과 권한 프로브를 검증한다(FP-070).

### 1. 라우트

- `@app.get("/conversations/{conversation_id}/audit")`: `user_id: str = Depends(current_user)`, `tasks: TaskService = TASK_SERVICE`. `tasks.audit_view(user_id, conversation_id)`. `ConversationError`는 `HTTPException(error.status_code, detail=error.code)`로 바꾼다. 성공은 200 JSON.
- `@app.get("/audit/verify")`: 같은 의존성. `tasks.audit_verify()`. `ConversationError`는 같은 방식.
- 경로는 기존 `/conversations/{conversation_id}`·`/events`와 겹치지 않는다. 라우트 함수는 `create_app` 클로저의 이름 `service`를 읽지 않는다.

### 2. `tests/test_api_entrypoint.py`

`FakeService`에 `audit_view`·`audit_verify`를 더하고 `EXPECTED`에 `("GET", "/conversations/{conversation_id}/audit")`·`("GET", "/audit/verify")` 두 줄만 더한다. 라우트 집합 정확 일치 단언과 AST 가드는 그대로 통과해야 한다(**표 갱신에 한함, 단언 약화 금지**).

### 3. `tests/test_api_audit_route.py`(신규)

`entry.build_server_config(0)`로 앱을 만들고 `entry.open_task_service`를 **실제 `TaskService`**(`InMemorySaver`·`FakeChat`·**임시 디렉터리의 실제 `AuditLog`**·`InMemoryTurnIndex`, 합성 데이터)를 주입하는 비동기 컨텍스트로 바꾼다. 세션은 `app.state.sessions.issue(user)`의 Bearer 토큰이다(토큰 리터럴 금지). `docs/results/hypotheses-m7-records-authz.md`의 프로브를 구현한다: A1 × S1~S7, 프로브 2~4, E1~E10(E7은 `VerifySummaryCache` 단일 비행을 동시 요청 수와 `compute` 호출 수로 확인하되 응답 도착 순서를 단언하지 않는다). 모든 프로브의 상태 코드는 500이 아니다.

**사용자 실행(이 step에서 실행하지 않는다)**: 없음.

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드)를 바꾸지 않았는가?
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

- 라우트 함수에서 `create_app` 클로저의 이름 `service`를 읽지 마라. 이유: lifespan이 서비스를 교체하므로 클로저 값은 자리표시자다(FP-070). AST 가드가 실패한다.
- `create_app(service=실제)`로만 만든 앱으로 결선을 검증했다고 하지 마라. 이유: 클로저와 `app.state`가 같아 이 결함을 못 잡는다(FP-070).
- 응답·오류 본문에 경로·`kid`·해시 전체·서명·대화 제목·상태를 넣지 마라. 이유: 사전 등록 프로브 2·E5가 실패한다.
- `tests/test_api_entrypoint.py`의 라우트 집합 정확 일치 단언을 느슨하게 하지 마라. 이유: 새 라우트는 표에 정확히 두 줄을 더해서만 반영한다(FP-071).
- 403을 쓰지 마라. 이유: 존재를 구별하게 한다. 타인·승인자·정리된 대화는 모두 404 `conversation_not_found`다.

## 소비자 점검(시뮬레이션 통과)

스텁 라우트를 넣은 전체 pytest에서 `tests/test_api_entrypoint.py::test_every_service_route_uses_the_service_installed_by_lifespan`가 새 라우트 2개를 표에서 찾지 못해 실패했다(라우트 집합 정확 일치). 이 step의 수정 허용 목록에 해당 파일을 "표·FakeService 갱신에 한함"으로 넣었다. 그 밖의 실패는 이 변경과 무관했다.

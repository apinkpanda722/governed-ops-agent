# Step 0: api-web-contract

## 읽어야 할 파일

- `/AGENTS.md`(신원·세션 규칙, 데모 로그인 CRITICAL 규칙), `/docs/ADR.md`(ADR-005·ADR-011·ADR-013), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절)
- `/src/govops/api/app.py`(`/sessions` 라우트와 `demo_login` 분기), `/src/govops/api/sessions.py`, `/src/govops/api/auth.py`(`current_user`, `record_auth_failure`), `/src/govops/api/users.py`, `/src/govops/api/__main__.py`(자리표시자 서비스 → lifespan 교체 구조)
- `/src/govops/api/tasks.py`(`approvals`, `_view`), `/src/govops/api/conversations.py`
- `/tests/test_api_sessions.py`, `/tests/test_api_auth_audit.py`, `/tests/test_api_entrypoint.py`(진입점 경유 테스트 방식), `/tests/test_api_conversation_views.py`(승인함 단언), `/tests/test_api_tasks.py`(승인자 view 단언)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-060·069·070) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/sessions.py`, `src/govops/api/auth.py`, `src/govops/api/app.py`, `src/govops/api/tasks.py`, `tests/test_api_web_contract.py`(신규), `tests/test_api_sessions.py`·`tests/test_api_auth_audit.py`·`tests/test_api_conversation_views.py`·`tests/test_api_tasks.py`(새 라우트·필드·토큰 추출 공유에 따른 추가·갱신에 한함, 단언 약화 금지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/api/sessions.py`, `src/govops/api/auth.py`, `src/govops/api/app.py`, `src/govops/api/tasks.py`, `tests/test_api_web_contract.py`, `tests/test_api_sessions.py`, `tests/test_api_auth_audit.py`, `tests/test_api_conversation_views.py`, `tests/test_api_tasks.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 기존 코드·테스트가 런타임에 두 경로를 읽는 것은 대상이 아니다. 이 step의 코드와 새 테스트는 두 경로를 읽지 않는다.

**배경**: 웹(BFF)이 쓰는 API 계약 셋을 추가한다. 로그인 화면의 사용자 목록, 서버 세션 폐기(로그아웃), 승인함 → 대화 연결용 `conversation_id`. 기존 라우트의 응답 형태는 바꾸지 않는다.

### 1. `SessionStore.revoke(token: str) -> bool`

- 토큰의 SHA-256 digest를 저장소에서 지운다. 지운 적이 있으면 `True`, 없으면 `False`. 만료 정리와 같은 잠금 없는 단일 스레드 규약을 그대로 따른다(기존 `issue`·`resolve`와 같은 방식).
- 토큰 원문을 저장·로그하지 않는다.

### 2. `DELETE /sessions` (`demo_login`일 때만 존재, `/sessions` 발급 라우트와 같은 블록)

- `Authorization: Bearer`로 받은 **그 토큰 하나**만 폐기한다(같은 사용자의 다른 세션은 유지). 성공은 204, 본문 없음.
- 인증 실패(헤더 없음·형식 오류·모르는 토큰·이미 폐기된 토큰)는 기존 `current_user`와 같은 401·`auth.failed` 감사다. 그래서 같은 토큰으로 두 번째 DELETE는 401이다.
- 헤더에서 토큰을 꺼내는 로직은 `auth.py`에 하나만 두고 `current_user`와 공유한다(정규식 중복 금지).
- 폐기 뒤 같은 토큰으로 `GET /sessions/me`는 401이다.

### 3. `GET /demo/users` (`demo_login`일 때만 존재)

- `POST /sessions`와 **같은 조건**이다: `DEMO_LOGIN` 플래그가 켜져 있고 클라이언트가 루프백(`127.0.0.1`·`::1`)일 때만 열린다. 루프백이 아니면 `record_auth_failure(request, "loopback_required")` 후 403 `Loopback required`(`/sessions`와 같은 응답). `DEMO_LOGIN`이 꺼져 있으면 라우트 자체가 없어 404다.
- 인증은 요구하지 않는다(로그인 전 화면이 쓴다). 응답은 `{"users": [<라벨>, …]}`이며 `create_app`이 받은 `users`(frozenset)의 라벨만, 정렬해서 낸다. 팀·직급 등 다른 필드를 넣지 않는다(라벨에서 파생한다).

### 4. 승인함 행의 `conversation_id` (`TaskService.approvals`)

- 각 행에 `conversation_id`를 **요청자 본인에게만** 넣는다: `state["user_id"] == 세션 사용자`인 행에만 키가 있고, 다른 승인자의 행에는 키 자체가 없다.
- `_view`는 바꾸지 않는다(`GET /tasks/{id}`·`GET /conversations/{id}`의 view 모양 불변, 직접 `_view`를 호출하는 기존 테스트가 있다).

### 5. 테스트

단언은 집합·개수·불변식으로 쓴다(FP-012).

- `tests/test_api_web_contract.py`(신규):
  - `SessionStore.revoke`: 폐기 후 `resolve`가 `None`, 두 번째 `revoke`는 `False`, 다른 토큰은 유지.
  - `DELETE /sessions`: 204 → 같은 토큰 `GET /sessions/me` 401 → 같은 토큰 DELETE 401, 같은 사용자의 다른 토큰은 계속 유효, 헤더 없음 401, `DEMO_LOGIN` 꺼짐이면 404.
  - `GET /demo/users`: 응답 라벨 집합이 `load_users()`의 라벨 집합과 **같고 비어 있지 않다**(FP-069: 빈 목록으로 통과 금지), 루프백이 아니면 403과 `auth.failed`(`loopback_required`) 감사, 인증 헤더 없이도 200, `DEMO_LOGIN` 꺼짐이면 404.
  - **실제 진입점 경유(FP-070)**: `api/__main__.build_server_config`를 거친 앱(진입점의 서비스 조립 함수 `open_task_service`만 가짜로 교체, `tests/test_api_entrypoint.py`와 같은 방식)에서 위 세 라우트와 `/sessions`의 존재를 단언한다. 데모 로그인 관련 라우트 집합(경로가 `/sessions` 또는 `/demo`로 시작하는 `(메서드, 경로)`)이 `{("POST","/sessions"), ("DELETE","/sessions"), ("GET","/sessions/me"), ("GET","/demo/users")}`와 **정확히 같음**을 단언해 새 라우트가 표에 없으면 실패하게 한다. `DEMO_LOGIN` 꺼짐일 때는 `/sessions`(POST·DELETE)와 `/demo/users`가 없고 `/sessions/me`만 남는다.
  - 승인함 `conversation_id`: 요청자 본인 행에는 대화 id와 같은 값이 있고, 전사 공지 승인자의 행에는 키가 없다. `GET /tasks/{id}`·`GET /conversations/{id}` 응답에는 새 키가 생기지 않는다.
- 기존 소비자 테스트(`tests/test_api_sessions.py`, `tests/test_api_auth_audit.py`, `tests/test_api_conversation_views.py`, `tests/test_api_tasks.py`)는 위 변경으로 깨지는 단언이 있을 때만 새 키·새 라우트를 반영해 고친다. 단언을 느슨하게 하지 않는다.

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

- `GET /demo/users`에 인증이나 팀·직급 필드를 넣지 마라. 이유: 로그인 전 화면이 쓰는 라벨 목록이다. 팀·직급은 라벨에서 파생하고, 필드를 늘리면 `data/seed/users.json` 검증(`load_users`)과 응답이 어긋난다.
- `DELETE /sessions`가 사용자 전체 세션을 지우게 하지 마라. 이유: 같은 사용자의 다른 탭 세션까지 끊는다. 요청에 쓰인 토큰 하나만 폐기한다.
- 새 라우트를 `create_app` 클로저의 `service`로 구현하지 마라. 이유: 실제 진입점은 자리표시자 서비스로 앱을 만들고 lifespan에서 교체한다. 새 라우트의 존재·조건은 진입점 경유 테스트로 확인한다(FP-070).
- 목록·집합이 비어 있어도 통과하는 단언을 쓰지 마라. 이유: 라벨 목록이 비면 `all([])`처럼 공허하게 통과한다. 먼저 개수가 1 이상임을 단언한다(FP-069).
- `POST /sessions`·`GET /sessions/me`·`/tasks`·`/conversations`의 기존 응답 키를 바꾸지 마라. 이유: CLI(`api/cli.py`)·스모크·골든이 그 모양에 기댄다. 소비자 테스트는 새 키를 더하는 경우에만 고친다(FP-060).

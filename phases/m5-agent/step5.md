# Step 5: conversation-turns

## 읽어야 할 파일

- `/AGENTS.md`(대기 `interrupt` thread CRITICAL 규칙, 신원·세션 규칙), `/docs/ADR.md`(ADR-013·ADR-015·ADR-017의 2026-10-08 변경 기록, ADR-018 승인 규칙), `/docs/ARCHITECTURE.md`(상태 전이·승인 절)
- 이전 step 산출물: `/src/govops/agent/turns.py`(step 0: `build_turn_state`, `MAX_TURNS`, `TurnLimitError`, `next_history`), `/src/govops/agent/graph.py`(`run_task(previous=…)`, `get_task`), `/src/govops/agent/retention.py`(step 3)
- `/src/govops/api/tasks.py`(`TaskService` 전체: `start`·`_run`·`get`·`approve`·`_expire`·`_update`·`startup_cleanup`·`aclose`), `/src/govops/api/app.py`, `/src/govops/api/wiring.py`, `/src/govops/tools/outbox.py`(`PostgresOutboxStore`의 DDL·advisory lock·`_run` 패턴)
- `/tests/test_api_tasks.py`(`HeldChat` 게이트 패턴, 동시 승인, 만료 사례), `/tests/test_e2e_isolation.py`, `/tests/test_api_guardrail_view.py`, `/tests/test_api_wiring_build.py`, `/tests/test_golden_m3.py`(`run_task`로 만든 thread를 `service.approve`로 승인: 색인에 행이 없는 turn_id)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-003·004·012·060·061·066) — 읽기만 한다
- **외부 계약 실측**(2026-10-08, LangGraph 1.2.12): 대기 `interrupt`가 있는 thread에 새 입력 `ainvoke`는 에러 없이 대기를 버린다. 같은 thread의 동시 `ainvoke`는 직렬화되지 않는다. `aupdate_state(as_node="__start__")` 뒤 `ainvoke(새 입력)` 패턴은 2번째 턴에서도 노드를 정확히 1번 실행한다(step 0 표). 따라서 서비스가 대화 잠금과 상태 검사로 새 입력을 막아야 한다.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/conversations.py`(신규), `src/govops/api/tasks.py`, `src/govops/api/app.py`, `src/govops/api/wiring.py`, `tests/test_api_conversations.py`(신규), `tests/test_api_tasks.py`·`tests/test_e2e_isolation.py`·`tests/test_api_guardrail_view.py`·`tests/test_api_wiring_build.py`(`TaskService` 생성자·내부 함수 시그니처·`thread` 키 변경에 따른 갱신에 한함, 단언 약화 금지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없고 `evalrun`을 import할 수 없다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/api/conversations.py`, `src/govops/api/tasks.py`, `src/govops/api/app.py`, `src/govops/api/wiring.py`, `tests/test_api_conversations.py`, `tests/test_api_tasks.py`, `tests/test_e2e_isolation.py`, `tests/test_api_guardrail_view.py`, `tests/test_api_wiring_build.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. `wiring.py`의 코드가 감사 디렉터리 기본 경로(`data/interim/audit`)를 문자열로 갖는 것과 기존 테스트가 평가 데이터·임시 경로를 런타임에 읽는 것은 대상이 아니다. 새 테스트는 두 경로의 실제 파일 내용을 읽지 않고 임시 디렉터리를 쓴다.

**배경**: `POST /conversations`로 대화를 열고 `POST /conversations/{id}/turns`로 턴을 더한다. 기존 `/tasks`·`/tasks/{id}`·`/tasks/{id}/approval`은 그대로 동작해야 한다(`POST /tasks`는 첫 턴이 있는 대화를 만든다). 이 step은 **저장소·잠금·턴 시작·승인 잠금**만 다룬다. 읽기 view와 승인함은 step 6이다.

### 1. `api/conversations.py`

- `class ConversationError(Exception)`: `status_code: int`, `code: str`.
- `TurnIndex` 프로토콜(모든 메서드 `async`): `add(*, turn_id, conversation_id, owner_sub, turn_index, created_at) -> None`(같은 `turn_id`면 덮어쓰지 않고 무시), `conversation_of(turn_id) -> str | None`, `set_awaiting(turn_id, awaiting: bool) -> None`(행이 없으면 무시), `awaiting() -> list[tuple[str, str]]`((turn_id, conversation_id) 목록), `delete_conversations(conversation_ids) -> int`.
- `InMemoryTurnIndex`와 `PostgresTurnIndex(url=None, *, table="govops_conversation_turns")`. Postgres 구현은 `PostgresOutboxStore`의 방식(연결 생성, 첫 사용 때 advisory lock 아래 `CREATE TABLE IF NOT EXISTS`, 실패는 전송 세부 없이 `RuntimeError("turn index DB operation failed")`)을 따른다. 열: `turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, owner_sub TEXT NOT NULL, turn_index INTEGER NOT NULL, created_at TEXT NOT NULL, awaiting BOOLEAN NOT NULL DEFAULT FALSE`와 `conversation_id` 색인. 실제 Postgres 검증은 step 9의 사용자 실행 타깃이다.

### 2. `TaskService` (`tasks.py`)

- 생성자에 `turns: TurnIndex | None = None`(기본 `InMemoryTurnIndex()`)을 더한다. 기존 생성 호출(`TaskService(graph)` 등)은 그대로 동작한다.
- **thread 키 정리**: `_config`·`_update`·`_expire`·`get_task` 호출은 모두 **conversation(thread) ID**를 받는다. `turn_id → thread` 변환은 공개 메서드 입구의 `_resolve(task_id)` 한 곳에서만 한다: `_TASK_ID`(32 hex)에 맞지 않으면 없음, 색인에 `turn_id`가 있으면 그 대화, 없으면 `task_id` 자신(색인에 행이 없는 단일 턴 task와 골든·평가가 `run_task`로 만든 thread).
- **잠금**: 대화 잠금 `_conv_lock(conversation_id)` 다음 task 잠금 `_lock(turn_id)` 순서로 **고정**한다(AGENTS.md CRITICAL). `add_turn`·`approve`·`get`의 만료 처리 모두 이 순서를 따른다. 반대 순서로 잡는 경로를 만들지 않는다.
- `start(user_id, request) -> str`: 시그니처·반환 불변. 내부는 첫 턴 시작이며 색인에 `(turn_id=task_id, conversation_id=task_id, owner_sub=user_id, turn_index=0)` 행을 더한다. `start_conversation(user_id, request) -> dict`는 `{"conversation_id": id, "turn_id": id}`를 돌려준다.
- `add_turn(user_id, conversation_id, request) -> dict`(`{"conversation_id", "turn_id"}`): 대화 잠금 안에서 순서대로 검사한다.
  1. 대화가 없거나 `state["user_id"] != user_id`이면 `ConversationError(404, "conversation_not_found")`. 없는 id와 남의 id는 응답이 같다.
  2. 요청이 비었거나 `max_request_chars`를 넘으면 `ConversationError(422, "invalid_request")`.
  3. 이 대화에 실행 중인 `asyncio.Task`가 있거나 `state["status"] == "running"`이면 `ConversationError(409, "turn_in_progress")`.
  4. `state["status"] == "awaiting_approval"`이면 task 잠금을 잡고 `_expire`로 만료를 먼저 처리한다. 처리 뒤에도 `awaiting_approval`이거나 `aget_state()`의 task에 대기 `interrupt`가 남아 있으면 `ConversationError(409, "approval_pending")`. 만료로 닫혔으면(`aget_state().next == ()`) 계속한다. **대기 `interrupt`가 있는 thread에는 새 입력으로 `ainvoke`를 호출하지 않는다.**
  5. `state.get("turn_index", 0) + 1 >= MAX_TURNS`이면 `ConversationError(409, "turn_limit")`.
  6. `build_turn_state(previous=state, …)`로 새 턴 상태를 만들고(`ValueError("owner_mismatch")`·`TurnLimitError`는 위 검사로 이미 걸렀다), 색인에 행을 더하고, 기존 `start`와 같은 방식으로 `aupdate_state(as_node="__start__")`(표식: `task_id`·`status="running"`) 뒤 `run_task(previous=state, conversation_id=…)`를 비동기로 시작한다.
- `_run`은 thread와 turn을 함께 받는다. 끝나면 `status == "awaiting_approval"`이면 색인 `set_awaiting(turn_id, True)`, 그 밖이면 `False`.
- `get(user_id, task_id)`: `_resolve`로 thread를 구한 뒤 `state["task_id"] != task_id`(지난 턴)이면 `None`(404)이다. 그 밖은 기존 규칙(소유자 또는 승인 정책을 만족하는 승인자) 그대로.
- `approve(user_id, task_id, …)`: 대화 잠금 → task 잠금 순서. `_resolve` 뒤 `state["task_id"] != task_id`이면 `ApprovalError(404)`: **다음 턴이 시작된 뒤 늦게 도착한 지난 턴의 중복 승인은 404이고 효과 건수는 바뀌지 않는다**(한계로 기록한다: 영수증은 현재 턴에만 있다). 승인이 대기를 벗어나면 `set_awaiting(turn_id, False)`. 결과가 다시 `awaiting_approval`이면 기존대로 영수증을 저장하지 않는다(FP-003).
- `startup_cleanup`: 대상은 thread(대화)다. `running`은 기존대로 `interrupted`로 바꾸고, 각 thread의 현재 턴이 `awaiting_approval`이면 색인 행이 없을 때 `(state["task_id"], thread, state["user_id"], state.get("turn_index", 0))` 행을 더하고 `set_awaiting(…, True)`, 아니면 행이 있는 현재 턴의 `awaiting`을 `False`로 맞춘다.

### 3. `app.py`·`wiring.py`

- 라우트(`service is not None`일 때): `POST /conversations`(202, 본문은 기존 `TaskRequest`)는 `{"conversation_id", "turn_id"}`, `POST /conversations/{conversation_id}/turns`(202)는 같은 형태. `ConversationError`는 `HTTPException(status_code, detail=code)`로 바꾼다(404 `conversation_not_found`, 409 `approval_pending`·`turn_in_progress`·`turn_limit`, 422 `invalid_request`). 응답에 요청 원문·내부 상태를 넣지 않는다. 기존 라우트와 에러 모양은 바꾸지 않는다.
- `build_task_service(..., turn_index=None)`과 `open_task_service`: `GOVOPS_DATABASE_URL`이 있으면 `PostgresTurnIndex()`, 없으면 `InMemoryTurnIndex()`를 `TaskService(turns=…)`에 준다. 기존 인자는 그대로 둔다.

### 4. 테스트 (`tests/test_api_conversations.py`, 인메모리 saver·가짜 모델)

모든 단언은 FP-012에 따라 **집합·개수·불변식**으로 쓴다(`gather` 인덱스·처리 순서 단언 금지). 모든 대기에 유한한 `timeout`을 둔다(FP-066).

- 첫 턴: `POST /conversations`는 202, `conversation_id == turn_id`, 같은 id로 `GET /tasks/{id}`가 기존 view와 같다. 기존 `/tasks` 테스트는 수정 없이 통과한다.
- 두 번째 턴: 완료 뒤 `POST …/turns`는 202이고 `turn_id != conversation_id`, 새 턴의 `GET /tasks/{turn_id}`가 동작하며 **첫 턴 id로는 404**다.
- 409 `turn_in_progress`: `HeldChat` 게이트로 턴을 실행 중으로 붙잡고 새 턴을 요청 → 409. 게이트를 열면 끝나고 다음 턴은 202.
- 409 `approval_pending`: 쓰기 프로필로 승인 대기까지 만든 뒤 새 턴 → 409이고 **실제 그래프의 `aget_state()`에서 대기 `interrupt`가 살아 있다**. 그 뒤 승인하면 정상 실행되어 효과가 정확히 1건이다. 같은 승인을 한 번 더 보내도 효과는 1건이다.
- 승인 만료: 주입한 시계를 30분 넘기면 새 턴 요청이 `approval.expired`를 감사 기록으로 남기고 `interrupt`를 닫은 뒤 202이며 효과는 0건이다.
- 409 `turn_limit`: 가짜 모델로 `MAX_TURNS`개 턴을 채운 뒤 다음 턴은 409.
- 소유: 다른 사용자의 턴 추가는 404이고 응답(상태·본문·헤더)이 없는 대화 id와 같다. 승인 정책을 만족하는 승인자도 `POST …/turns`는 404다.
- 동시성: 같은 대화에 턴 요청 2개를 `gather`하면 상태 코드 정렬이 `[202, 409]`이고 409의 detail은 `turn_in_progress`다. 승인 요청·턴 요청·`GET`을 섞어 20회 `gather`해도(각 `wait_for(…, 10)`) 교착이 없고 효과는 1건을 넘지 않는다.
- 재시작: 같은 saver와 같은 `InMemoryTurnIndex`로 `TaskService`·그래프를 새로 만들어도 2번째 턴의 승인이 재개되어 효과 1건이다(색인이 `turn_id → conversation_id`를 유지).
- 늦은 중복 승인: 턴 1의 승인 뒤 턴 2를 시작하고 끝낸 다음 턴 1의 `turn_id`로 같은 승인을 다시 보내면 404이고 효과 건수는 변하지 않는다.
- 색인에 행이 없는 `run_task` 생성 thread(골든)에서 `service.approve`가 기존처럼 동작한다(기존 골든 테스트가 수정 없이 통과).
- `startup_cleanup`: 재시작 뒤 `running` 턴은 `interrupted`가 되고 그 대화에 새 턴을 더할 수 있다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
git diff --exit-code -- data/replay/golden   # 골든 재기록 없음, status.json 불변
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

- 대기 `interrupt`가 있거나 실행 중인 thread에 새 입력으로 `ainvoke`하는 경로를 만들지 마라. 이유: 에러 없이 대기 승인이 버려지고 동시 실행은 상태가 섞인다. 잠금 안에서 상태·`interrupt` 검사를 통과한 뒤에만 새 턴을 시작한다(AGENTS.md CRITICAL).
- 잠금을 task → 대화 순서로 잡지 마라. 이유: 순서가 섞이면 교착이 생긴다. 대화 → task로 고정한다. 대기 시간 제한 없는 `await`를 잠금 안에 두지 마라(FP-066).
- 승인이 대기로 되돌아가는 분기에서 영수증을 저장하지 마라. 이유: 되돌아간 초안의 거부·재수정·승인·만료가 막힌다(FP-003). 영수증은 종결 또는 실행 전환 때만 저장한다.
- `TaskService._tasks`(실행 중 작업 집합)와 `TaskService._view(state, task_id, approval=…)`의 이름·시그니처를 바꾸지 마라. 이유: 기존 테스트가 `gather(*service._tasks)`와 `_view`를 직접 쓴다(FP-060).
- `/tasks` 계열 응답 모양과 기존 에러 코드를 바꾸지 마라. 이유: 단일 턴 경로 불변이 골든·평가의 전제다. 지난 턴 `/tasks/{id}`가 404인 것만 새 동작이다.
- 동시성 테스트에서 어느 요청이 먼저 처리되는지(`responses[0]` 같은 인덱스, 처리 순서)를 단언하지 마라. 이유: 스케줄링에 달려 CI에서 뒤집힌다(FP-012). 상태 코드 집합·효과 건수·`aget_state` 불변식으로 단언한다.
- 새 턴 요청의 응답과 에러에 요청 원문·대화 이력을 넣지 마라. 이유: 이 step의 응답은 ID와 오류 코드뿐이다.

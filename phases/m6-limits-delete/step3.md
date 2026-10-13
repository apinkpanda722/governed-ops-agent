# Step 3: delete-service

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/api/tasks.py`(`TaskService`: `_resolve`, `add_turn`, `conversation_view`, `progress`, `get`, `approve`/`_approve`, `approvals`, `_expire`, 잠금 순서 대화 → task)
- `/src/govops/api/conversations.py`(step 1 산출물: `is_deleted`, `mark_deleted`), `/src/govops/audit/log.py`(step 0 산출물: `conversation.deleted`, `AuditWriteError`)
- `/tests/test_api_conversations.py`, `/tests/test_api_tasks.py`, `/tests/test_api_events.py`, `/tests/test_agent_graph.py`(`FakeChat`, `FakeTools`, `async_test`, `reply`, `call`)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/tasks.py`, `tests/test_api_conversation_delete.py`, `tests/test_api_conversations.py`, `tests/test_api_tasks.py`, `tests/test_api_events.py`, `tests/test_api_conversation_views.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/tasks.py`, `tests/test_api_conversation_delete.py`, `tests/test_api_conversations.py`, `tests/test_api_tasks.py`, `tests/test_api_events.py`, `tests/test_api_conversation_views.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다. 테스트는 임시 디렉터리·합성 데이터·테스트용 키만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 이 금지의 대상이 아니다.

**배경**: 서비스 계층에 소프트 삭제와, 삭제된 대화에 대한 모든 진입점의 404를 만든다. 권한 판정(소유자 확인)과 상태 판정은 서비스 안에서 대화 잠금 → task 잠금 순서로 한다(잠금 순서는 대화 → task로 고정이다).

### 1. `TaskService.delete_conversation(user_id, conversation_id) -> dict`

1. `_TASK_ID` 형식이 아니면 `ConversationError(404, "conversation_not_found")`.
2. `async with asyncio.timeout(120), self._conv_lock(conversation_id):` 안에서 진행한다.
3. `await self.turns.is_deleted(conversation_id)`이면 404 `conversation_not_found`.
4. `state = await get_task(self.graph, conversation_id)`가 없거나 `state["user_id"] != user_id`이면 404 `conversation_not_found`(없음·타인·형식 오류와 구별되지 않는 같은 응답).
5. 실행 중(`self._running`에 끝나지 않은 task가 있거나 `state["status"] == "running"`)이면 **기존** `ConversationError(409, "turn_in_progress")`.
6. `async with self._lock(state["task_id"]):` 안에서 `state = await self._expire(conversation_id, state)`로 만료된 승인을 먼저 닫는다. 그 뒤에도 `state["status"] == "awaiting_approval"`이거나 `graph.aget_state`의 `snapshot.tasks`에 interrupt가 남아 있으면(`add_turn`과 같은 판정) **기존** `ConversationError(409, "approval_pending")`. 새 오류 코드를 만들지 않는다.
7. 감사 `conversation.deleted`를 **먼저** 쓴다: `audit.append("conversation.deleted", task_id=conversation_id, actor_sub=user_id)`(동기 호출은 `asyncio.to_thread`로, 그래프 코드와 같은 방식). 감사 싱크가 없거나(`self.audit is None`) `AuditWriteError`면 삭제하지 않고 `ConversationError(500, "audit_write_failed")`.
8. `await self.turns.mark_deleted(conversation_id, user_id, <UTC ISO 시각>)`. 이 호출이 예외면 `ConversationError(500, "delete_failed")`. **감사는 이미 기록됐으므로 재시도하면 같은 삭제의 이벤트가 두 번 남을 수 있다**(이 한계는 step 8이 문서에 적는다).
9. `{"conversation_id": conversation_id, "deleted": True}`를 돌려준다. 이미 삭제된 대화의 두 번째 삭제는 3에서 404이고 감사 이벤트를 남기지 않는다.

체크포인트·그래프 상태·outbox는 건드리지 않는다.

### 2. 다른 진입점의 삭제 확인(모두 같은 대화 잠금 안, 상태를 읽기 전)

- `add_turn`, `conversation_view`, `progress`: 삭제됐으면 각각 기존 404 경로(`ConversationError(404, "conversation_not_found")`, `progress`는 None).
- `get`: `_resolve`가 돌려준 대화 ID로 `is_deleted`를 확인하고 삭제됐으면 None(404). **첫 턴 ID와 이후 턴 ID** 둘 다 해당한다.
- `_approve`: 대화 잠금을 잡은 직후, `_decision`(승인 권한 판정)과 **승인 영수증 재생(`approval_receipt`) 경로보다 먼저** `is_deleted`를 확인해 `ApprovalError(404)`(본문 "Task not found"). 영수증 재생이 삭제된 대화의 뷰를 돌려주면 안 되고, 삭제된 대화에서 권한 판정 결과(403)가 노출되면 안 된다. 기존 승인 권한 규칙(`approver_decision`, FORBIDDEN이면 403 "Approval rejected")은 바꾸지 않는다.
- `approvals`: `turns.awaiting()`이 이미 삭제된 대화를 제외하지만 루프 안에서도 `is_deleted`이면 건너뛴다(방어).
- `conversations`(목록): `list_for_owner`가 제외한다. 루프 안의 별도 확인은 필요 없다.

### 3. 테스트(`tests/test_api_conversation_delete.py`, 신규)

실제 `TaskService` + `InMemorySaver` + `FakeChat`/`FakeTools` + `MemoryAuditLog` + `InMemoryTurnIndex`를 쓴다(합성 데이터).

- 소유자 삭제 성공 → 반환 값, 감사 이벤트 정확히 1건(`task_id`=대화 ID, `actor_sub`=요청자, 다른 필드 없음, 요청 원문·제목이 어디에도 없음).
- 삭제 뒤 `conversation_view`·`add_turn`·`progress`·`get`(첫 턴 ID와 두 번째 턴 ID)·`approve`(영수증이 있는 경우 포함)가 모두 404/None이고 `approvals`·`conversations`에 나타나지 않는다. 삭제된 대화와 없는 대화·타인의 대화의 오류 코드가 같다.
- 형식이 틀린 ID, 타인의 대화, 없는 대화 → `delete_conversation`·`conversation_view`·`add_turn`·`progress`는 404 `conversation_not_found`(같은 본문). `approve`는 없는 ID·삭제된 대화·형식 오류가 같은 404 "Task not found"이고, 타인의(승인 대기가 아닌) 대화에 대한 `approve`는 기존 승인 권한 규칙이 정한다(이 칸은 "성공하지 않고 상태·감사·효과가 바뀌지 않는다"만 단언한다).
- 실행 중 삭제 → 409 `turn_in_progress`(응답을 게이트로 붙잡는 FakeChat), 승인 대기 삭제 → 409 `approval_pending`(쓰기 프로필 승인 대기 상태는 `test_api_conversations.py`·`test_api_tasks.py`의 기존 구성을 따른다), 만료된 승인 대기(주입 시계) → 삭제 성공.
- 두 번째 삭제는 404이고 감사 이벤트가 늘지 않는다.
- 감사 쓰기 실패(싱크가 `AuditWriteError`) → 500 계열 오류이고 표시되지 않아(`is_deleted` False) 이어 말하기가 계속 가능. 표시 실패(가짜 `turns.mark_deleted`가 예외) → `delete_failed`이고 감사 이벤트는 1건 남음.
- **동시성**: `add_turn`과 `delete_conversation`을 동시에 시작해도 결과가 둘 중 하나로만 정해진다: (삭제 성공, 이후 이어 말하기 404) 또는 (이어 말하기 수락, 삭제 409 `turn_in_progress`). 결과 **집합**으로 단언하고 처리 순서를 단언하지 않는다(FP-012). 이 테스트는 실제 `asyncio.Lock`을 쓰는 서비스를 거치므로 잠금 순서 계약(대화 → task)을 재현한다.
- 기존 `tests/test_api_conversations.py`·`tests/test_api_tasks.py`는 `TurnIndex` 가짜가 새 메서드를 갖추지 않아 깨지는 곳만 고친다(~에 한함, 단언 약화 금지).

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

- 체크포인트·그래프 상태·outbox·감사 로그를 지우거나 고치지 마라. 이유: 소프트 삭제다. 원문 제거는 보존 정리가 맡고 감사 기록과 이미 발송된 효과는 남아야 한다.
- 잠금을 task → 대화 순서로 잡지 마라. 이유: 턴 추가·승인·만료 처리가 대화 → task 순서로 고정돼 있어 반대 순서는 교착한다.
- 404·409에는 새 코드를 만들지 마라(`conversation_not_found`·`turn_in_progress`·`approval_pending` 재사용). 이유: BFF의 기존 코드 매핑에 기댄다. 새 코드는 내부 실패용 500 `audit_write_failed`·`delete_failed` 두 개뿐이며 BFF가 502로 접는다.
- 삭제 확인을 승인 영수증 재생 경로 뒤에 두지 마라. 이유: 영수증 재생이 삭제된 대화의 뷰를 돌려준다.
- 감사 이벤트에 요청 원문·제목·사용자 이름 등 내용을 넣지 마라. 이유: 감사는 해시와 ID만 기록한다(AGENTS.md).

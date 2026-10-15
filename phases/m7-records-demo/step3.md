# Step 3: audit-view-service

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/api/tasks.py`(`TaskService`: `_conv_lock`, `conversation_view`, `ConversationError` 사용 방식)
- `/src/govops/api/conversations.py`(step 2 산출물 `turn_ids_of`)
- `/src/govops/audit/records.py`(step 1 산출물)
- `/tests/test_api_tasks.py`, `/tests/test_api_conversation_delete.py`(서비스 단위 테스트 형식)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/tasks.py`, `tests/test_api_audit_view.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/tasks.py`, `tests/test_api_audit_view.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: 기록 화면 API의 서비스 계층이다. 소유자만, 삭제되지 않은 대화만 읽는다. 읽기 전용이다.

### 1. 새 메서드(기존 메서드 불변)

```python
async def audit_view(self, user_id: str, conversation_id: str) -> dict
async def audit_verify(self) -> dict
```

- `audit_view`: ① `_TASK_ID` 형식이 아니면 `ConversationError(404, "conversation_not_found")` ② 대화 잠금(`_conv_lock`, 기존 잠금 순서 대화 → task 유지, task 잠금은 잡지 않는다) 안에서 **`turns.is_deleted` 확인과 `turns.turn_ids_of(conversation_id, user_id)` 조회에만** 잠금을 쓴다. 삭제됨이면 404, 목록이 비면 404(타인·없음·승인자·정리된 대화·대화가 아닌 턴 ID 모두 같은 코드·본문) ③ **잠금을 푼 뒤** `getattr(self.audit, "directory", None)`이 없으면 `ConversationError(503, "audit_unavailable")` ④ 잠금 밖에서 `asyncio.to_thread(records.read_events, directory, turn_ids)` ⑤ 잠금 범위를 코드 리뷰로 확인할 수 있게 `async with` 블록 바로 위에 주석 한 줄(`# 대화 잠금은 삭제 확인·턴 조회에만 쓴다. 파일 읽기는 잠금 밖.`)을 둔다 ⑥ `{"conversation_id", "turns": [{"turn_id", "turn_index", "events"}], "truncated"}` 반환. `AuditUnavailable`은 503 `audit_unavailable`로 바꾼다. **`get_task`·`_expire`·상태 변경·`set_awaiting` 호출 금지.**
- `audit_verify`: **대화 잠금을 잡지 않는다.** 서비스에 `directory`가 없으면 503. `records.VerifySummaryCache`를 `TaskService` 인스턴스에 지연 생성 속성으로 두고(생성자 시그니처는 바꾸지 않는다) `asyncio.to_thread`로 `records.verify_summary`를 돌린다. `AuditUnavailable`은 503.
- 서비스에 `AuditLog`가 아닌 `MemoryAuditLog`가 주입된 경우 `directory`가 없어 503이다.

### 2. 테스트(`tests/test_api_audit_view.py`)

`hypotheses-m7-records-authz.md`의 A1×S1~S7 응답 동일성(서비스 수준: `ConversationError`의 상태·코드), 프로브 2·3·4(소유자 성공 본문 키 집합, 필드 방어, 읽기 전용: 호출 전후 대화 상태·`expires_at`·체인 길이 불변), E2·E3·E4의 서비스 부분을 구현한다. 체인은 임시 키·임시 디렉터리의 합성 `AuditLog`다. 이 step에서 라우트·HTTP는 만들지 않는다.

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

- `TaskService`의 기존 메서드 본문·시그니처·잠금 순서를 바꾸지 마라. 이유: `evalrun/dev_m3.py`가 같은 서비스를 쓰므로 평가 경로·골든 영향 없음을 보증해야 한다.
- `audit_view`에서 `_expire`·`get_task`를 부르지 마라. 이유: 읽기 전용 원칙이고 만료 처리는 상태·감사를 바꾼다.
- 404 사유를 구별하는 코드나 메시지를 만들지 마라. 이유: 존재·소유 정보 노출(ADR-025 4항).
- `wiring.py`·`__main__.py`를 수정하지 마라. 이유: `self.audit.directory`로 충분하고 lifespan 교체 결함 면적(FP-070)을 늘리지 않는다.
- `asyncio.gather` 결과 순서를 단언하지 마라. 이유: 동시 실행 순서는 계약이 아니다(FP-012). 집합·개수 불변식만 쓴다.

## 소비자 점검(시뮬레이션 통과)

스텁(`audit_view`·`audit_verify` 추가)으로 전체 pytest를 돌렸을 때 `tests/test_api_tasks.py`·`tests/test_api_conversation_views.py`·`tests/test_evalrun_*`는 통과했다(뷰 키 집합·정확 일치 단언은 새 메서드의 영향을 받지 않는다).

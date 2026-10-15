# Step 2: turn-index-turn-ids

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/api/conversations.py`(`TurnIndex`, `InMemoryTurnIndex`, `PostgresTurnIndex`)
- `/tests/test_turn_index_soft_delete.py`, `/tests/test_api_conversations.py`, `/tests/test_m5_pg.py`(사용자 실행 케이스 형식)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/conversations.py`, `tests/test_turn_index_turn_ids.py`, `tests/test_m5_pg.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/conversations.py`, `tests/test_turn_index_turn_ids.py`, `tests/test_m5_pg.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: 감사 레코드에는 대화 ID가 없고 대화 → 턴 ID 목록은 턴 색인에만 있다. 기록 화면이 소유자의 대화에 속한 턴 ID를 얻도록 **조회 메서드를 추가만** 한다.

### 1. 변경

- `TurnIndex` Protocol에 `async def turn_ids_of(self, conversation_id, owner_sub) -> list[tuple[str, int]]: ...`를 더한다. 반환은 `(turn_id, turn_index)` 목록이며 `turn_index` 오름차순이다. **삭제 표시된 행은 반환하지 않는다. `owner_sub`가 다른 행은 반환하지 않는다.**
- `InMemoryTurnIndex`·`PostgresTurnIndex`에 구현한다. Postgres는 `SELECT turn_id, turn_index FROM {table} WHERE conversation_id = %s AND owner_sub = %s AND deleted_at IS NULL ORDER BY turn_index ASC`이고 기존 `_run`·준비 경로(`ALTER … IF NOT EXISTS` 포함)를 그대로 쓴다. 기존 메서드의 본문·시그니처는 바꾸지 않는다.

### 2. 테스트

- `tests/test_turn_index_turn_ids.py`(신규): InMemory — 순서, 타 소유자 비반환, 삭제 표시 후 비반환, 없는 대화 빈 목록, 두 번째 턴 ID를 대화 ID로 넘기면 빈 목록. Postgres 구현은 가짜 연결 없이 SQL 문 구성이 파라미터화돼 있음(문자열 보간 금지)을 확인하는 단위 테스트까지만 둔다.
- `tests/test_m5_pg.py`(수정 허용: **새 사용자 실행 케이스 한 개 추가에 한함, 기존 단언 약화 금지**): 임시 테이블에서 `turn_ids_of`의 순서·소유자·삭제 필터를 확인하는 `make test-m5-pg` 케이스. 이 step에서 실행하지 않는다.

**사용자 실행(이 step에서 실행하지 않는다)**: `make test-m5-pg`(머지 후, Postgres 필요).

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

- 기존 메서드(`add`·`list_for_owner`·`conversation_of`·`awaiting`·`mark_deleted`·`is_deleted`·`deleted_conversation_ids`·`delete_conversations`)의 본문·시그니처를 바꾸지 마라. 이유: `evalrun`이 같은 `TaskService`·턴 색인을 쓰므로 평가 경로에 영향이 간다.
- SQL을 문자열 보간으로 만들지 마라. 이유: 식별자·값 주입. `psycopg.sql`과 파라미터 바인딩만 쓴다.
- `PostgresTurnIndex`에 새 테이블·컬럼을 만들지 마라. 이유: 스키마 변경은 이 phase 범위 밖(ADR-025의 `ALTER … IF NOT EXISTS` 경로 유지).
- `make test-m5-pg`를 실행하지 마라. 실제 Postgres에 접속하는 테스트를 기본 테스트 모음에 넣지도 마라. 이유: 사용자 실행 항목이며 AC 검증은 네트워크 없이 통과해야 한다(AGENTS.md).

## 소비자 점검(시뮬레이션 통과)

스텁(Protocol·InMemory에 `turn_ids_of` 추가)을 넣고 전체 pytest를 돌렸을 때 `TurnIndex`를 쓰는 기존 테스트(`test_turn_index_soft_delete`·`test_api_conversations`·`test_api_conversation_delete*`·`test_api_retention`·`test_startup_cleanup_lock`·`test_api_ui_contract`와 `tests/e2e_backend.py`)는 모두 통과했다. Protocol은 런타임에서 강제되지 않아 가짜 구현이 메서드를 갖지 않아도 호출 전에는 실패하지 않는다. 호출하는 쪽은 step 3이다. Postgres 구현은 시뮬레이션하지 않았다(사용자 실행 `make test-m5-pg`).

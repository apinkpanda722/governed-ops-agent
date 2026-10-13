# Step 1: turn-index-soft-delete

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/api/conversations.py`(`TurnIndex` 프로토콜, `InMemoryTurnIndex`, `PostgresTurnIndex`)
- `/src/govops/api/retention.py`(읽기만 한다. `delete_conversations` 호출처)
- `/tests/test_api_conversations.py`, `/tests/test_m5_pg.py`(`test_turn_index_postgres`의 인메모리·Postgres 동등성 패턴), `/tests/test_api_retention.py`, `/tests/test_startup_cleanup_lock.py`, `/tests/test_api_ui_contract.py`, `/tests/e2e_backend.py`(읽기만 한다. `TurnIndex`를 쓰는 소비자)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/conversations.py`, `tests/test_turn_index_soft_delete.py`, `tests/test_m5_pg.py`, `tests/test_api_conversations.py`, `tests/test_api_retention.py`, `tests/test_api_ui_contract.py`, `tests/test_startup_cleanup_lock.py`, `tests/e2e_backend.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/conversations.py`, `tests/test_turn_index_soft_delete.py`, `tests/test_m5_pg.py`, `tests/test_api_conversations.py`, `tests/test_api_retention.py`, `tests/test_api_ui_contract.py`, `tests/test_startup_cleanup_lock.py`, `tests/e2e_backend.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다. 테스트는 임시 디렉터리·합성 데이터·테스트용 키만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 이 금지의 대상이 아니다.

**배경**: 소프트 삭제 표시를 턴 인덱스에 둔다. 체크포인트 상태·그래프 상태는 건드리지 않는다. 실제 제거는 기존 보존 정리(`checkpoint-prune`)에 맡긴다(step 2). 대화는 여러 턴이고 행 키가 `turn_id`이므로 **대화의 모든 턴 행**에 표시한다. 대화 ID는 첫 턴 task ID와 같으므로(`api/tasks.py`의 `start_conversation`), 인덱스 행을 제외하는 것만으로는 첫 턴에 대한 접근이 막히지 않는다. 서비스가 명시적으로 `is_deleted`를 확인한다(step 3).

이 이름들(`deleted_at`, `mark_deleted`, `is_deleted`, `deleted_conversation_ids`)의 기존 사용처는 없다. `grep`으로 확인했다. 기존 `delete_conversations`(하드 삭제)는 보존 정리가 쓰므로 동작을 바꾸지 않는다.

### 1. `TurnIndex` 프로토콜과 두 구현

- `mark_deleted(conversation_id: str, owner_sub: str, deleted_at: str) -> int`: 이 대화·이 소유자의 **모든 턴 행**에 `deleted_at`을 정한다. 이미 표시된 행은 기존 값을 유지한다. 영향 행 수를 돌려준다. 소유자가 다르면 0을 돌려주고 아무것도 바꾸지 않는다.
- `is_deleted(conversation_id: str) -> bool`: 그 대화의 행 중 하나라도 표시됐으면 True. 행이 없으면 False.
- `deleted_conversation_ids() -> list[str]`: 표시된 대화 ID의 정렬된 고유 목록(step 2가 쓴다).
- `list_for_owner`와 `awaiting`은 삭제 표시된 대화의 행을 제외한다. `conversation_of`는 **바꾸지 않는다**(삭제된 대화도 대화 ID를 돌려준다).
- 인메모리: 행 딕셔너리에 `deleted_at`(기본 None)을 둔다.
- Postgres: `_run`의 스키마 준비(`pg_advisory_xact_lock` 안)에서 기존 `CREATE TABLE IF NOT EXISTS …`는 그대로 두고 그 뒤에 `ALTER TABLE {} ADD COLUMN IF NOT EXISTS deleted_at TEXT`를 실행한다(이미 있는 구 스키마 DB에서도 동작). `mark_deleted`는 `UPDATE … SET deleted_at = COALESCE(deleted_at, %s) WHERE conversation_id = %s AND owner_sub = %s` **한 문장**(한 트랜잭션)이다. 새 테이블·데이터 이행·`DROP`은 만들지 않는다.

### 2. 테스트

- `tests/test_turn_index_soft_delete.py`(신규, 인메모리 계약): 대화의 모든 턴 행이 표시된다 / 소유자가 다르면 0이고 변화 없음 / 재표시는 기존 `deleted_at`을 유지 / `list_for_owner`·`awaiting`에서 제외되고 다른 대화는 그대로 / `conversation_of`는 삭제된 대화에도 같은 ID를 돌려줌 / `is_deleted`·`deleted_conversation_ids`(정렬·고유) / 표시 뒤 `delete_conversations`(하드 삭제)가 행을 지우면 `is_deleted`가 False로 돌아감(표시도 함께 사라짐).
- **가짜가 못 하는 부분**: 인메모리 구현은 `ALTER TABLE`, 구 스키마 DB, 동시 접속, 트랜잭션을 재현하지 못한다. `tests/test_m5_pg.py`(사용자 실행 `make test-m5-pg`, 테스트 전용 DB URL이 없으면 skip)에 케이스를 더한다: ① `deleted_at` 컬럼이 **없는 구 스키마 테이블**을 직접 만든 뒤 `PostgresTurnIndex`가 열리면 컬럼이 생기고 기존 행의 `deleted_at`이 NULL이며 기존 메서드가 동작한다 ② 두 인스턴스가 같은 구 스키마 테이블을 동시에 처음 열어도 오류가 없다 ③ `mark_deleted` 뒤 `list_for_owner`·`awaiting`·`is_deleted`·`deleted_conversation_ids` 결과가 인메모리 구현과 같다(기존 동등성 패턴).
- 일반 `make verify`에서는 위 Postgres 케이스가 skip된다. 이는 이 step의 한계이고 사용자 실행 항목으로 남긴다.
- 소비자 확인: `rg "\.rows|TurnIndex|delete_conversations" tests/ src/`로 `InMemoryTurnIndex.rows`의 딕셔너리 모양을 정확 일치로 단언하는 테스트와 `TurnIndex`를 구현하는 가짜를 찾아, 새 메서드·키 때문에 깨지는 곳만 수정한다(`tests/test_api_conversations.py`·`tests/test_api_retention.py`·`tests/test_api_ui_contract.py`·`tests/test_startup_cleanup_lock.py`·`tests/e2e_backend.py`에 한함, 단언 약화 금지).

**사용자 실행(이 step에서 실행하지 않는다)**: `make test-m5-pg`(구 스키마 ALTER·동시 열기·동등성).

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

- `conversation_of`가 삭제된 대화를 숨기게 하지 마라. 이유: 첫 턴 task ID가 대화 ID와 같아서 `_resolve`가 대화 ID를 못 얻으면 첫 턴 접근이 삭제 확인 없이 체크포인트를 읽는다. 판정은 서비스의 `is_deleted`가 한다.
- 대화의 일부 턴 행만 표시하는 구현을 만들지 마라. 이유: 표시되지 않은 턴 행으로 목록·승인함·조회가 계속 접근된다. 한 문장 UPDATE로 전체를 표시한다.
- `delete_conversations`(하드 삭제)의 동작과 반환값을 바꾸지 마라. 이유: 보존 정리가 의존한다(`api/retention.py`).
- 인메모리 계약 테스트만으로 끝내지 마라. 이유: 가짜 의존성이 실제 계약(ALTER, 구 스키마 DB, 동시 열기)을 재현하지 못한다(FP-068·070 유형). 사용자 실행 Postgres 케이스를 반드시 더한다.
- `make test-m5-pg`를 실행하지 마라. 이유: 테스트 전용 DB URL이 필요한 사용자 실행 항목이다. 이 step의 검증은 AC에 적힌 명령으로 한다.

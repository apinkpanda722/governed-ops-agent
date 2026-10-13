# Step 0: audit-conversation-deleted

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/audit/log.py`(`EVENTS`, `SYSTEM_EVENTS`, `_NEW_REQUIRED`, `_validate_event`, `_validate_new`, `MemoryAuditLog`)
- `/src/govops/audit/verify.py`(읽기만 한다. 새 이벤트가 체인 검증을 통과해야 한다)
- `/tests/test_audit.py`(`test_events`의 `EVENTS` 정확 일치), `/tests/test_audit_events.py`(`CASES`, `emit`, `test_new_fields_are_event_scoped`의 `SYSTEM_EVENTS` 정확 일치)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/log.py`, `tests/test_audit.py`, `tests/test_audit_events.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/log.py`, `tests/test_audit.py`, `tests/test_audit_events.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다. 테스트는 임시 디렉터리·합성 데이터·테스트용 키만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 이 금지의 대상이 아니다.

**배경**: 대화 삭제(step 3)가 남기는 감사 이벤트 `conversation.deleted`를 먼저 만든다. 요청자 ID와 대화 ID만 담고 내용·필드는 없다. 기존 이벤트 필드명과 충돌하지 않도록 **필드를 하나도 두지 않는다**(과거 `count`·`pruned_count` 같은 필드 충돌 사례). 대화 ID는 첫 task ID(32 hex)와 같으므로 `task_id` 자리에 넣는다. `conversation_id`를 필드 값으로 넣으면 문자열 필드 값 검증(해시·mock ID·열거형만 통과)이 거부한다.

이 이름들의 기존 사용처는 없다(`conversation.deleted`, `deleted_at`). `grep`으로 확인했다.

### 1. `audit/log.py`

- `EVENTS`에 `"conversation.deleted"`를 더한다. `SYSTEM_EVENTS`에는 더하지 않는다(시스템 이벤트는 `actor_sub="system"`·`task_id=None`을 요구하지만 이 이벤트는 요청자 `actor_sub`와 대화 ID `task_id`를 요구한다. 기존 비시스템 경로 그대로).
- 필드가 하나라도 있으면 `AuditWriteError`로 거부한다. 권장 방법: `_NEW_REQUIRED["conversation.deleted"] = frozenset()`(허용 필드가 빈 집합이라 `_validate_new`가 어떤 필드도 거부한다). 다른 방법을 써도 되지만 결과는 같아야 한다: 필드 없음만 통과.
- 식별자는 형식 검증만 한다(`task_id`는 `[0-9a-f]{32}`, `actor_sub`는 `demo-[a-z-]+-g[0-9]+`). 식별자에 PII 정규식을 돌리지 않는다(FP-010).

### 2. 테스트

- `tests/test_audit.py::test_events`의 `EVENTS` 집합에 원소 하나만 더한다(정확 일치 단언 유지).
- `tests/test_audit_events.py`: `CASES`에 `"conversation.deleted": {}`를 더한다. `test_new_fields_are_event_scoped`의 `SYSTEM_EVENTS` 정확 일치 단언은 그대로 통과해야 한다. 새 테스트: ① 정상 기록(필드 없음)이 성공하고 체인 검증(`audit/verify.py`의 기존 검증 함수)을 통과한다. ② 필드를 하나라도 주면(`count`, `conversation_id`, `deleted_at`, `task_id`가 아닌 임의 이름) 거부된다. ③ `task_id=None`이거나 `actor_sub="system"`이면 거부된다. ④ 형식이 틀린 `task_id`·`actor_sub`는 거부된다. ⑤ `MemoryAuditLog`도 같은 검증을 한다.
- 기존 테스트가 이 이벤트를 모르는 곳이 있으면(`EVENTS`를 순회하며 모든 이벤트를 방출하는 테스트) 그 테스트가 새 이벤트를 포함해 통과하는지 확인한다.

**사용자 실행(이 step에서 실행하지 않는다)**: 없음. 이 step은 API 호출이 없다.

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

- 필드를 가진 이벤트로 만들지 마라(`conversation_id`·`count`·`deleted_at` 등). 이유: 문자열 필드 값 검증이 32 hex를 거부하고, 과거 새 필드 이름이 기존 일반 경로 필드와 충돌한 사례가 있다. 대화 ID는 `task_id` 자리에 둔다.
- `SYSTEM_EVENTS`에 이 이벤트를 넣지 마라. 이유: `tests/test_audit_events.py`가 그 집합을 정확히 단언하고, 시스템 이벤트는 `actor_sub="system"`·`task_id=None`을 요구해 요청자·대화 ID를 담을 수 없다.
- `src/govops/audit/crypto.py`·`verify.py`·`anchor.py`를 수정하지 마라. 이유: 서명·체인 코드는 이 step의 범위가 아니고 새 이벤트는 기존 일반 경로를 쓴다.
- 식별자(`task_id`·`actor_sub`)에 PII 정규식을 돌리지 마라. 이유: 무작위 hex가 전화번호 규칙에 걸려 무작위로 실패한 사례(FP-010).
- `EVENTS`·`SYSTEM_EVENTS` 정확 일치 단언을 약화하지 마라. 이유: 집합에 원소 하나를 더하는 변경이므로 단언은 정확한 새 집합으로 갱신한다(FP-061).

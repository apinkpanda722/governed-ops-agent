# Step 2: audit-pruned-event

## 읽어야 할 파일

- `/AGENTS.md`(감사 로그 규칙), `/docs/ADR.md`(ADR-012의 2026-10-08 변경 기록 `checkpoint.pruned`, ADR-015), `/docs/ARCHITECTURE.md`(감사 로그 절의 시스템 이벤트 문장)
- `/src/govops/audit/log.py`(`EVENTS`·`SYSTEM_EVENTS`·`_NEW_REQUIRED`·`_NEW_OPTIONAL`·`_NEW_FIELDS`·`_NEW_FORMATS`·`_validate_new`·`_validate_event`), `/src/govops/audit/__init__.py`
- `/tests/test_audit.py`(`EVENTS` 정확 일치 단언, 이벤트 순회 테스트), `/tests/test_audit_events.py`(`SYSTEM_EVENTS` 정확 일치 단언과 소유 이벤트 밖 필드 거부 순회), `/tests/audit_keys.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·060·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/audit/log.py`, `tests/test_audit_events.py`(신규 이벤트 사례 추가와, `SYSTEM_EVENTS`·소유 이벤트 순회 단언의 이름 집합 갱신에 한함, 단언 약화 금지), `tests/test_audit.py`(`EVENTS` 이름 집합 갱신에 한함, 단언 약화 금지). 계층: `audit/`는 `agent`·`tools`·`broker`·`api`를 import할 수 없다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/audit/log.py`, `tests/test_audit_events.py`, `tests/test_audit.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`의 파일(감사 개인키·체인·고정점 대기 파일)을 직접 열람·출력·인용하는 것**에 대한 것이다. 감사 테스트가 임시 디렉터리에 키를 만들어 쓰는 것(`tests/audit_keys.py`)과 코드의 기본 경로 문자열은 대상이 아니다. 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다.

**배경**: checkpointer 보존 삭제(ADR-015)가 `APPLY=1`·serve 시작으로 thread를 지우면 삭제 건수와 cutoff 시각만 감사 로그에 남긴다(ADR-012 변경 기록). 이 step은 감사 쪽 이벤트 정의만 만든다. 호출하는 쪽은 step 8이다.

### 1. 이벤트 `checkpoint.pruned`

- `EVENTS`·`SYSTEM_EVENTS`에 `checkpoint.pruned`를 더한다. 시스템 이벤트라서 `actor_sub="system"`, `task_id=None`이어야 한다(다르면 거부).
- 필드는 정확히 `count`와 `cutoff` 둘이고 둘 다 필수다. 다른 필드는 거부한다(thread ID·task ID·사용자 식별자·원문을 담을 자리가 없다).
  - `count`: `type(value) is int`이고 `0 <= value <= 1_000_000`(`bool` 거부).
  - `cutoff`: UTC ISO 시각 문자열. 정규식 `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00`에 전체 일치한다. 이 값은 **형식으로 검증**하고 PII 정규식을 돌리지 않는다(FP-010).
- 기존 방식대로 `_NEW_REQUIRED`·`_NEW_FORMATS`에 등록하고 `_NEW_FIELDS`에 자동으로 들어가게 한다. 그러면 `count`·`cutoff`를 다른 이벤트가 쓰면 거부된다.

### 2. 테스트

- 허용: 올바른 `count`·`cutoff`, `count=0`.
- 거부: `actor_sub`가 `system`이 아님, `task_id`가 있음, 필드 누락·추가(`thread_id` 등), `count`가 `True`·음수·문자열·1_000_001, `cutoff`가 `Z` 접미·오프셋 비UTC·초 없음·공백 포함·임의 문자열.
- 다른 이벤트(`service.start`, `side_effect.draft` 등)가 `count`·`cutoff` 필드를 가지면 거부된다.
- 실제 `AuditLog`(임시 디렉터리, `tests/audit_keys.py`)로 기록한 뒤 체인이 이어지고 서명 검증이 통과한다.
- 기존 정확 일치 단언(`tests/test_audit.py`의 `EVENTS == {…}`, `tests/test_audit_events.py`의 `SYSTEM_EVENTS == {…}`와 소유 이벤트 밖 필드 거부 순회)은 **집합에 `checkpoint.pruned` 하나를 더하는 것만** 바꾼다. 같은 강도로 단언한다(개수 대신 이름 집합, FP-061).

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

- 이벤트 필드에 thread ID·task ID·사용자 식별자·원문을 허용하지 마라. 이유: 감사 로그는 지울 수 없다. 건수와 cutoff만 남긴다(ADR-012, ADR-015).
- `cutoff`·`count`에 PII 정규식을 돌리지 마라. 이유: 형식이 정해진 값은 형식으로 검증한다. 숫자 연속이 전화번호 규칙에 걸려 무작위로 거부된다(FP-010). 무작위 값 대량 테스트를 하나 둔다(시드 고정 `count` 10,000개가 거부되지 않음).
- `_NEW_FIELDS`·소유 이벤트 순회 테스트를 약화하지 마라. 이유: 다른 이벤트가 새 필드를 몰래 받으면 안 된다(FP-060·061).
- `audit/` 밖의 파일을 고치지 마라. 이유: 호출하는 쪽은 step 8이다. 감사 쓰기는 `audit/` 한 곳에서만 한다.

# Step 2: retention-deleted-ids

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/agent/retention.py`(`prune_checkpoints`, `PruneResult`)
- `/src/govops/api/retention.py`(`run_prune`, `startup_prune`, CLI 출력 한 줄)
- `/src/govops/api/conversations.py`(step 1 산출물: `deleted_conversation_ids`)
- `/tests/test_agent_retention.py`, `/tests/test_api_retention.py`, `/tests/test_m5_pg.py`(prune 케이스)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/retention.py`, `src/govops/api/retention.py`, `tests/test_agent_retention.py`, `tests/test_api_retention.py`, `tests/test_m5_pg.py`, `tests/test_startup_cleanup_lock.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/agent/retention.py`, `src/govops/api/retention.py`, `tests/test_agent_retention.py`, `tests/test_api_retention.py`, `tests/test_m5_pg.py`, `tests/test_startup_cleanup_lock.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다. 테스트는 임시 디렉터리·합성 데이터·테스트용 키만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 이 금지의 대상이 아니다.

**배경**: 삭제 표시된 대화는 나이와 관계없이 다음 `checkpoint-prune`의 제거 대상이 된다. 실제 제거(체크포인트 원문 삭제)는 이 정리가 맡는다. 감사 로그와 효과 이력(outbox)은 건드리지 않는다.

`PruneResult.deleted_ids`는 이미 "실제로 삭제된 thread ID"를 뜻하므로 새 인자 이름은 **`deleted_conversation_ids`**로 쓴다(이름 충돌 방지).

### 1. `prune_checkpoints`

- 시그니처에 키워드 전용 `deleted_conversation_ids: Collection[str] = ()`를 더한다.
- thread ID가 이 집합에 있으면 나이(cutoff)와 관계없이 후보다. 단 **활성 판정은 그대로 우선**한다: 상태가 없음/`running`이거나 만료 전 `awaiting_approval`이면 후보가 아니고 활성으로 센다. 타임스탬프를 알 수 없는 thread도 지금처럼 활성으로 보존한다. 만료된 `awaiting_approval`이고 삭제 표시됐으면 후보다.
- `PruneResult`의 필드와 `scanned`·`active` 계산 방식은 바꾸지 않는다.

### 2. `run_prune`·`startup_prune`

- `run_prune`이 `await turns.deleted_conversation_ids()`를 읽어 dry-run과 apply 두 번의 `prune_checkpoints` 호출에 같은 값을 넘긴다. 읽기에 실패하면 예외가 전파되어 삭제하지 않는다(fail-closed, 기존 `startup_prune`·CLI의 불투명 오류 처리 그대로).
- 감사 `checkpoint.pruned`(`pruned_count`, `cutoff`)와 삭제 후 `turns.delete_conversations(result.deleted_ids)` 호출은 지금과 같다.
- **CLI 출력은 바꾸지 않는다**: `cutoff=… scanned=… candidates=… active=… deleted=… apply=…` 한 줄의 필드·순서·형식을 유지한다(FP-061: 출력 위치·형식을 단언하는 기존 테스트가 있다). 새 필드를 이 줄에 끼우지 않는다.

### 3. 테스트

- `tests/test_agent_retention.py`: 삭제 표시된 대화(최근 활동 1시간)가 후보 / 표시 안 된 같은 나이는 후보 아님 / 삭제 표시됐지만 `running`·만료 전 `awaiting_approval`은 후보 아님 / 만료된 `awaiting_approval`+삭제 표시는 후보 / 타임스탬프 없는 thread는 활성 유지 / 기존 상태·나이 매트릭스 테스트는 그대로 통과.
- `tests/test_api_retention.py`: dry-run은 부작용 없음(`turns.rows` 깊은 복사 비교 유지), apply는 감사 1건 + thread 삭제 + 인덱스 행 삭제(표시된 행 포함), CLI 출력 문자열 형식 단언 유지. `turns`를 가짜로 쓰는 테스트가 있으면 `deleted_conversation_ids`를 갖추게 한다(`tests/test_api_retention.py`에 한함, 단언 약화 금지).
- **가짜가 못 하는 부분**: 인메모리 saver·인덱스는 Postgres 체크포인터의 잠금·실제 DB 행을 재현하지 못한다. `tests/test_m5_pg.py`의 기존 prune 케이스(사용자 실행)에 "삭제 표시된 최근 대화가 apply에서 제거되고 활성 대화는 남는다" 확인을 더한다. 기존 `startup_prune` 교착 방지 테스트(FP-068)는 약화하지 않는다.

**사용자 실행(이 step에서 실행하지 않는다)**: `make test-m5-pg`, 그리고 머지 후 `make checkpoint-prune`(dry-run).

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

- 삭제 표시만으로 `running`·만료 전 `awaiting_approval` thread를 후보로 만들지 마라. 이유: 활성 보존 규칙(진행 중 실행과 승인 대기)은 삭제 표시와 무관하게 우선이며 기존 상태·나이 매트릭스 단언을 약화할 수 없다.
- `PruneResult`·CLI 출력 한 줄의 필드와 순서를 바꾸지 마라. 이유: 출력 형식을 단언하는 기존 테스트가 있고 마지막 줄·순서 단언은 새 줄이 생기면 깨진다(FP-061).
- `prune_checkpoints`의 `async for … alist` 루프 안에서 같은 saver나 `turns`를 부르지 마라. 이유: `AsyncPostgresSaver.alist`가 잠금을 쥔 채 yield해 교착한 사례(FP-068). 삭제 표시 집합은 루프 전에 읽어 인자로 넘긴다.
- 감사 로그·outbox(효과 이력)를 지우거나 고치지 마라. 이유: 이 정리는 체크포인트 원문과 턴 인덱스만 제거하고 감사 기록은 서명된 추가 전용이다.
- `make test-m5-pg`와 `make checkpoint-prune`을 실행하지 마라. 이유: 실제 Postgres가 필요한 사용자 실행 항목이다.

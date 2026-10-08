# Step 8: prune-command

## 읽어야 할 파일

- `/AGENTS.md`(감사·checkpointer 규칙, `## 명령어` 절의 표기 형식), `/docs/ADR.md`(ADR-012·ADR-015의 2026-10-08 변경 기록), `/docs/ARCHITECTURE.md`(감사 시스템 이벤트 문장)
- 이전 step 산출물: `/src/govops/agent/retention.py`(step 3: `prune_checkpoints`, `PruneResult`), `/src/govops/audit/log.py`(step 2: `checkpoint.pruned`), `/src/govops/api/conversations.py`(step 5: `TurnIndex.delete_conversations`), `/src/govops/api/tasks.py`(`startup_cleanup`, `audit`)
- `/src/govops/api/wiring.py`(`open_task_service`: `startup_cleanup` 호출 위치), `/src/govops/agent/checkpointer.py`, `/src/govops/audit/log.py`의 `MemoryAuditLog`·`AuditWriteError`
- `/Makefile`(`serve`·`db-init`·`test-outbox-pg` 타깃 형식), `/tests/test_makefile.py`(사용자 실행 타깃 테스트 형식), `/tests/test_api_wiring_build.py`(`open_task_service`를 가짜로 검증하는 방식), `/tests/test_docs_consistency.py`(`## 명령어` 절 단언)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-030·031·060·061·066) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/retention.py`(신규), `src/govops/api/wiring.py`, `Makefile`, `AGENTS.md`(`## 명령어` 절에 `make checkpoint-prune` 한 줄), `tests/test_api_retention.py`(신규), `tests/test_makefile.py`(새 타깃 테스트 추가에 한함, 기존 단언 유지), `tests/test_api_wiring_build.py`(`startup_cleanup` 뒤 prune 호출을 반영하는 가짜 갱신에 한함, 단언 약화 금지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. 삭제 판정은 `agent/retention.py`가 맡고 이 모듈은 감사 기록과 색인 정리, CLI, 시작 시 실행을 맡는다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/api/retention.py`, `src/govops/api/wiring.py`, `Makefile`, `AGENTS.md`, `tests/test_api_retention.py`, `tests/test_makefile.py`, `tests/test_api_wiring_build.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일(감사 개인키·체인)을 직접 열람·출력·인용하는 것**에 대한 것이다. `retention.py`의 코드가 `AUDIT_DIR` 기본 경로(`data/interim/audit`)를 문자열로 갖고 `--apply`에서 `AuditLog`가 그 디렉터리에 쓰는 것은 대상이 아니다. 테스트는 두 경로의 실제 파일 내용을 읽지 않고 `MemoryAuditLog`나 임시 디렉터리를 쓴다.

**배경**: ADR-015의 삭제를 실행하는 층이다. `make checkpoint-prune`은 dry-run, `APPLY=1`일 때 삭제한다. serve 시작 시에는 `startup_cleanup` 다음에 한 번 삭제를 적용한다. 감사 기록은 삭제 **전에** 남기고(실행 전 의도 기록), 기록이 실패하면 삭제하지 않는다.

### 1. `api/retention.py`

- `async def run_prune(saver, turns: TurnIndex, audit: AuditPort | None, *, now: datetime, apply: bool) -> PruneResult`:
  1. `prune_checkpoints(saver, now=now, apply=False)`로 대상을 구한다. `apply=False`면 그 결과를 돌려주고 끝(감사·삭제 없음).
  2. 대상이 비어 있으면 끝(감사 기록 없음).
  3. `audit.append("checkpoint.pruned", task_id=None, actor_sub="system", pruned_count=len(대상), cutoff=result.cutoff)`. **`pruned_count`·`cutoff` 외의 필드를 넣지 않는다.** `AuditWriteError`면 삭제하지 않고 그 오류를 올린다(fail-closed). `audit`이 `None`이면 삭제하지 않는다.
  4. `prune_checkpoints(saver, now=now, apply=True)`로 삭제하고, 실제로 지운 thread(`deleted_ids`)에 대해서만 `turns.delete_conversations(...)`로 색인 행을 지운다. 결과를 돌려준다.
- `async def startup_prune(service, saver, turns) -> None`: `service.startup_cleanup()` **다음에** 호출된다. `run_prune(..., audit=service.audit, now=utc 현재, apply=True)`를 실행하되 **어떤 예외도 serve를 막지 않는다**: 예외를 삼키고 표준 오류에 `WARN checkpoint_prune_failed` 한 줄만 남긴다(예외 문장·thread ID·경로를 넣지 않는다).
- CLI `python -m govops.api.retention [--apply]`: `GOVOPS_DATABASE_URL`이 없으면 한 줄 안내 후 종료 코드 1. `open_checkpointer`로 saver를, `PostgresTurnIndex()`로 색인을 연다. `--apply`가 없으면 dry-run이라 `AuditLog`를 만들지 않는다(감사 키가 없어도 동작). `--apply`일 때만 `AuditLog(env_or("AUDIT_DIR", "data/interim/audit"))`를 만든다. 출력은 한 줄 요약 `cutoff=… scanned=N candidates=N active=N deleted=N apply=true|false`뿐이다(ID·원문 없음). 오류는 안전한 코드 한 줄과 종료 코드 1.

### 2. `wiring.py`

`open_task_service`에서 `await service.startup_cleanup()` 바로 다음에 `await startup_prune(service, saver, turn_index)`를 호출한다(`turn_index`는 step 5에서 만든 색인). `yield` 이전에 끝나야 한다.

### 3. `Makefile`·`AGENTS.md`

```
.PHONY: checkpoint-prune
# 기본은 dry-run, 삭제는 APPLY=1. compose Postgres·make db-init 후 사용자 실행
checkpoint-prune:
	uv run --env-file .env python -m govops.api.retention $(if $(APPLY),--apply,)
```
`AGENTS.md`의 `## 명령어` 절 서비스 계열 줄 근처에 `make checkpoint-prune # …(사용자 실행)` 한 줄을 더한다(형식은 다른 줄과 같게, `(사용자 실행)` 포함). `make verify`에는 넣지 않는다.

### 4. 테스트 (`tests/test_api_retention.py`)

`InMemorySaver`로 만든 thread들, `InMemoryTurnIndex`, `MemoryAuditLog`로 한다. 나이는 `now` 주입으로 만든다.

- dry-run: 삭제 0건, 감사 기록 0건, 색인 불변, 출력 한 줄 요약에 ID·원문 없음.
- apply: 대상 thread가 지워지고 그 대화의 색인 행도 지워진다. 활성 thread(`running`, 만료 전 승인 대기)와 7일 이내 thread는 그대로다. 만료가 지난 오래된 승인 대기는 지워진다. 감사 기록은 정확히 1건이고 이벤트 필드가 `{pruned_count, cutoff}`뿐이며 thread ID·task ID·원문이 없다. `pruned_count`는 삭제 대상 건수다(삭제 전에 기록하는 의도 기록이라 일부 삭제가 실패하면 실제 삭제 수보다 클 수 있다).
- 감사 쓰기 실패(`AuditWriteError`) → 삭제 0건, 오류가 올라온다. `audit=None` → 삭제 0건.
- 대상 0건 → 감사 기록 없음.
- 일부 thread 삭제 실패 주입 → 지운 thread의 색인만 정리되고 실패한 thread의 색인은 남는다.
- `startup_prune`: prune이 예외를 던져도 반환하고(서비스 시작을 막지 않음) 표준 오류에 정확히 한 줄 `WARN checkpoint_prune_failed`만 남으며 예외 문장이 없다.
- `open_task_service` 순서: 가짜 서비스에서 `startup_cleanup`이 `startup_prune`보다 먼저 호출된다(호출 기록의 순서를 단언하는 것이 이 테스트의 목적이다).
- CLI: `--apply` 없이 감사 키 없이도 동작, `GOVOPS_DATABASE_URL` 없으면 종료 코드 1과 한 줄 안내.
- Makefile: `checkpoint-prune` 레시피가 `uv run --env-file .env python -m govops.api.retention $(if $(APPLY),--apply,)`이고 `verify` 레시피에 없다. `AGENTS.md` 명령어 줄에 `(사용자 실행)`이 있다.

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

- 기본값(`APPLY` 없음, `--apply` 없음)으로 삭제하지 마라. 이유: 삭제는 되돌릴 수 없다. 삭제 호출은 `apply=True` 분기 하나에만 둔다.
- 감사 기록 없이 삭제하지 마라. 이유: 의도를 먼저 기록하고 기록에 실패하면 부작용을 일으키지 않는다는 규칙이다. `checkpoint.pruned`에는 `pruned_count`와 `cutoff`만 넣는다(ID 금지, ADR-012).
- 시작 시 prune의 실패를 serve 시작 실패로 만들지 마라. 이유: 보존 정리는 서비스 가용성보다 후순위다. 예외 문장·경로를 로그에 남기지 않는다(자격 증명 노출 방지).
- `make checkpoint-prune`·`make serve`를 실행하지 마라. 이유: 실제 Postgres와 `.env`가 필요한 사용자 실행 타깃이다. 테스트는 인메모리로만 한다.
- `agent/retention.py`·`audit/`를 이 step에서 고치지 마라. 이유: 각각 step 3·2의 산출물이다. 필요한 변경이 있으면 `blocked`로 보고한다.
- `dirty_entries`를 거치지 않는 `git status` 호출을 만들지 마라. 이유: FP-064.

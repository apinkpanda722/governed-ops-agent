# Step 3: checkpoint-retention

## 읽어야 할 파일

- `/AGENTS.md`(checkpointer 7일 삭제 규칙), `/docs/ADR.md`(ADR-015와 2026-10-08 변경 기록), `/docs/THREAT_MODEL.md`(TH10)
- 이전 step 산출물: `/src/govops/agent/turns.py`(step 0), `/src/govops/agent/graph.py`(`get_task`, `AgentState`)
- `/src/govops/agent/checkpointer.py`(`open_checkpointer`), `/src/govops/api/tasks.py`(`startup_cleanup`, `_expire`: 만료 처리가 조회 때만 일어난다)
- `/tests/test_checkpointer.py`, `/tests/test_agent_graph.py`(`FakeChat`·`FakeTools` 헬퍼, 쓰기 프로필의 승인 대기 상태를 만드는 방식)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-011·032·066) — 읽기만 한다
- **외부 계약 실측**(langgraph-checkpoint 4.2.0·checkpoint-postgres 3.1.2, 2026-10-08):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| `AsyncPostgresSaver.adelete_thread(thread_id)` | 성공(임시 thread로 확인, 삭제 후 0행) | `checkpoints`·`checkpoint_blobs`·`checkpoint_writes` 3개 테이블에서 그 thread 행을 지운다. 반환값은 `None`이다 |
| `InMemorySaver.adelete_thread` | 존재 | 같은 이름의 메서드가 있다(`BaseCheckpointSaver` 호환) |
| `checkpoints` 테이블 | — | **시간 열이 없다**. 시각은 체크포인트 JSON의 `ts`(UTC ISO)이고 `CheckpointTuple.checkpoint["ts"]`로 읽는다 |
| `saver.alist(None)` | 성공 | 모든 thread의 체크포인트를 돌려준다(thread당 여러 개). 각 튜플의 `checkpoint["channel_values"]`에 `status`·`expires_at`이 들어 있다(최신 체크포인트가 현재 상태) |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/retention.py`(신규), `tests/test_agent_retention.py`(신규). 계층: `agent/`는 `broker`·`mocks`·`eval`·LLM SDK를 import할 수 없다. 이 모듈은 `api/`·`audit/`를 import하지 않는다(감사 기록과 대응 색인 삭제는 호출하는 쪽, step 8). TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/agent/retention.py`, `tests/test_agent_retention.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽거나 쓰지 않는다(테스트는 `InMemorySaver`만 쓴다).

**배경**: ADR-015는 task가 종료되고 7일이 지나면 checkpoint 원문을 지운다. 대화(thread)가 한 단위다. 삭제 조건은 "활성 턴이 없고 마지막 checkpoint `ts`가 7일을 넘음"이고, `awaiting_approval`이어도 `expires_at`이 지났으면 비활성이다(만료 처리는 조회 때만 일어난다).

### `agent/retention.py`

- `RETENTION_DAYS = 7`. `@dataclass(frozen=True) class PruneResult`: `cutoff: str`(`now - 7일`의 UTC ISO), `scanned: int`(thread 수), `candidates: tuple[str, ...]`(삭제 대상 thread ID), `active: int`(활성이라 건너뛴 thread 수), `deleted: int`(실제로 지운 thread 수, `apply`일 때만 > 0), `deleted_ids: tuple[str, ...]`(실제로 지운 thread ID, `deleted == len(deleted_ids)`).
- `async def prune_checkpoints(saver: BaseCheckpointSaver, *, now: datetime, apply: bool = False, retention_days: int = RETENTION_DAYS) -> PruneResult`.
  - `saver.alist(None)`로 thread별 최신 체크포인트(가장 늦은 `ts`)를 구한다. 상태는 그 체크포인트의 `channel_values`(`status`, `expires_at`)다.
  - **활성**: `status == "running"`, 또는 `status == "awaiting_approval"`이면서 `expires_at`이 없거나 파싱할 수 없거나 `now < expires_at`. 상태를 알 수 없으면(`status` 없음) 활성으로 본다(보수적).
  - **대상**: 활성이 아니고 `now - 마지막 ts > retention_days`.
  - `apply=False`(기본)는 아무것도 지우지 않는다. `apply=True`일 때만 대상마다 `await saver.adelete_thread(thread_id)`를 호출한다. 한 thread의 삭제가 실패하면 나머지는 계속하고 실패한 thread는 `deleted`·`deleted_ids`에 넣지 않는다(예외 메시지·thread ID를 로그나 결과에 남기지 않는다). `now`는 tz-aware UTC여야 하고 아니면 `ValueError`.
- 결과·로그에 원문(`messages`·`answer`·`draft`)을 넣지 않는다. 이 모듈은 `print`·`logging`을 하지 않는다.

### 테스트 (`tests/test_agent_retention.py`)

`InMemorySaver`로 그래프를 실제로 돌려 만든 thread를 쓰고, 나이는 **`now`를 미래로 주입**해서 만든다(체크포인트 `ts`를 직접 고치지 않는다, FP-011). 조합 표를 `parametrize`로 확인한다: `completed`·`failed`·`rejected`·`expired`·`blocked_input` + 6일 / 8일, `running`(8일이어도 대상 아님), `awaiting_approval`(만료 전 8일은 대상 아님, `expires_at` 지난 8일은 대상), `status` 없는 체크포인트(대상 아님). `apply=False`는 `adelete_thread`를 한 번도 부르지 않는다(스파이). `apply=True` 뒤 `get_task`로 읽으면 대상 thread는 비어 있고 활성 thread는 그대로다. 삭제 실패 주입 시 나머지가 지워진다. naive `now`는 거부된다. 결과 객체와 모든 문자열에 `answer`·`messages` 원문이 없다. 경계: 정확히 7일(`>` 이므로 대상 아님)과 7일 + 1초(대상).

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

- 기본값으로 삭제하지 마라. 이유: 삭제는 되돌릴 수 없다. `apply=False`가 기본이고 삭제 호출은 `apply=True` 분기 하나에만 둔다.
- 활성 thread(`running`, 만료 전 `awaiting_approval`, 상태를 알 수 없는 thread)를 삭제 대상으로 만들지 마라. 이유: 승인 대기 중인 초안과 `interrupt`를 지우면 승인이 사라진다. 만료가 지난 대기는 비활성이다(만료 처리는 조회 때만 일어난다).
- 테스트에서 체크포인트의 `ts`를 직접 고치거나 `uuid`·시간을 `monkeypatch`로 고정해 나이를 만들지 마라. 이유: 저장소가 실제로 쓰는 `ts`를 그대로 읽어야 한다. 나이는 `now` 주입으로 만든다(FP-011·032).
- 결과·예외·로그에 thread ID 목록 외의 내용(원문, 사용자 식별자)을 넣지 마라. 이유: 원문은 이 삭제 대상 그 자체다(ADR-015). 호출하는 쪽만 대상 ID 목록을 쓴다.
- 이 모듈에서 실제 Postgres에 접속하지 마라. 이유: `make verify`는 DB 없이 통과해야 한다. 실제 Postgres 검증은 step 9의 사용자 실행 타깃이다.

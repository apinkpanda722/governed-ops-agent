# Step 2: variant-store

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙의 checkpointer 7일 보존 ADR-015)
- `/docs/M8_DESIGN_DRAFT.md`(2-3절의 "변형 저장소"·"변형 행 기록 시점"·"변형 행 삭제")
- `/docs/results/hypotheses-m8-rollout.md`(5·6절, 읽기만 한다)
- `/src/govops/api/conversations.py`(`PostgresTurnIndex._run` 151~186행의 준비 경로·`pg_advisory_xact_lock`·예외 숨김 패턴, `InMemoryTurnIndex`)
- `/src/govops/api/retention.py`(전체 92행)
- `/src/govops/agent/retention.py`(`prune_checkpoints`, 읽기만 한다)
- `/tests/test_api_retention.py`(전체. 특히 `fixture_threads`, `test_startup_exception_is_one_safe_warning` 250~260행의 `args == (saver, turns)`, `test_cli_uses_fake_dependencies_and_safe_summary` 263~317행의 monkeypatch)
- `/tests/test_m5_pg.py`(`database_url`·`bounded`·`ddl`, `test_turn_index_postgres` 49행~, `test_retention_isolated_database` 379행~의 임시 DB 패턴)
- `/src/govops/rollout/policy.py`(step 1, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/variant.py`(신규), `src/govops/api/retention.py`(변형 행 삭제 추가에 한함), `tests/test_api_variant.py`(신규), `tests/test_api_retention.py`(새 테스트 추가와 `test_cli_uses_fake_dependencies_and_safe_summary`에 변형 저장소 가짜 `monkeypatch.setattr` 한 줄 추가에 한함, 기존 단언 약화 금지), `tests/test_m5_pg.py`(변형 테이블 테스트 함수 추가에 한함, 기존 테스트 불변). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/api/variant.py`, `src/govops/api/retention.py`, `tests/test_api_variant.py`, `tests/test_api_retention.py`, `tests/test_m5_pg.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: CLI의 기본 감사 디렉터리 `data/interim/audit`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·인메모리 저장소만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

`src/govops/api/` 아래 새 파일에는 `redteam` 문자열을 쓰지 않는다(`tests/test_redteam_boundary.py`가 `api/`의 모든 `.py`를 검사한다).

**목적**: 대화 → 변형 고정 저장소와, thread를 실제로 지울 때 변형 행도 지우는 경로를 만든다. 라우터·조립은 다음 step이다.

### 1. `src/govops/api/variant.py`

```python
class VariantStore(Protocol):
    async def put(self, *, conversation_id: str, variant: str, bucket: int, pct_bp: int, assigned_at: str) -> bool: ...  # 새로 썼으면 True
    async def get_many(self, conversation_ids: Collection[str]) -> dict[str, str]: ...   # 있는 것만, id → variant
    async def recent(self, variant: str, limit: int) -> list[str]: ...                  # assigned_at 내림차순, conversation_id 오름차순
    async def delete(self, conversation_ids: Collection[str]) -> int: ...

class InMemoryVariantStore: ...
class PostgresVariantStore:
    def __init__(self, url=None, *, table="govops_conversation_variant"): ...
```

- 테이블: `(conversation_id TEXT PRIMARY KEY, variant TEXT NOT NULL, bucket INTEGER NOT NULL, pct_bp INTEGER NOT NULL, assigned_at TEXT NOT NULL)`와 `(variant, assigned_at)` 인덱스. 준비 경로는 `PostgresTurnIndex._run`과 같은 방식(연결 → `_setup_lock` → `pg_advisory_xact_lock(hashtext(table))` → `CREATE TABLE IF NOT EXISTS`·`CREATE INDEX IF NOT EXISTS` → commit, 식별자는 `sql.Identifier`, 값은 파라미터 바인딩). `make db-init`은 바꾸지 않는다(DB만 만든다, `Makefile` 83~84행).
- `put`: `variant ∈ {"v1","v2"}`, `conversation_id`는 `^[0-9a-f]{32}$`, `bucket` 0~9999, `pct_bp` 0~10000을 검증한다(형식 검증, FP-010). 이미 있으면 덮어쓰지 않는다(`ON CONFLICT DO NOTHING`, 반환 False). 변형 고정이 이 규칙에 기댄다.
- `delete`: **테이블을 만들지 않는다.** Postgres는 먼저 `SELECT to_regclass(%s) IS NOT NULL`로 테이블 존재를 확인하고 없으면 0을 돌려준다(롤아웃을 켠 적 없는 DB에서 `make checkpoint-prune`이 테이블을 만들지 않게). ID 형식은 검증하지 않는다(prune이 넘기는 thread ID를 그대로 받는다).
- 모든 Postgres 예외는 `RuntimeError("variant store DB operation failed")`로 바꾸고 원인을 숨긴다(`from None`, URL·SQL 미노출).
- `GOVOPS_DATABASE_URL`은 `PostgresTurnIndex`와 같은 방식(`os.environ.get("GOVOPS_DATABASE_URL", "")`, 비어 있지 않은 기본값을 넘기지 않음)으로 읽는다(`tests/test_env_defaults.py`의 스캐너가 기본값 있는 `environ.get`을 막는다).

### 2. `src/govops/api/retention.py` (변형 행 삭제 추가에 한함)

- `run_prune(saver, turns, audit, *, now, apply, variants=None)`: 기존 `if result.deleted_ids: await turns.delete_conversations(result.deleted_ids)` 바로 뒤에서 `variants`가 있으면 `await variants.delete(result.deleted_ids)`. 그 밖의 순서·감사 의도 기록·dry-run 동작은 바꾸지 않는다. 지우지 못한 thread의 변형 행은 남긴다.
- `startup_prune(service, saver, turns, variants=None)`: `run_prune(saver, turns, audit=..., now=..., apply=True, variants=variants)`. 위치 인자는 `(saver, turns)` 그대로(기존 테스트 `args == (saver, turns)`).
- CLI `_run_cli`: `PostgresVariantStore()`를 만들어 `run_prune(..., variants=...)`로 넘긴다. 출력 줄 형식(`cutoff=… scanned=… candidates=… active=… deleted=… apply=…`)은 바꾸지 않는다. 모듈에서 `from govops.api.variant import PostgresVariantStore`로 가져와 테스트가 `monkeypatch.setattr(retention, "PostgresVariantStore", …)`로 바꿀 수 있게 한다.

### 3. 테스트

`tests/test_api_variant.py`(인메모리, Postgres 쪽은 SQL 접속 없이 검증 가능한 부분만): `put` 첫 쓰기 True·두 번째 False이고 변형이 바뀌지 않음, 형식 밖 입력 거부(`ValueError`), `get_many`는 있는 것만, `recent`의 정렬(같은 `assigned_at`이면 ID 오름차순)과 `limit`, 변형별 분리, `delete` 개수, 동시 `put` 2개(같은 ID, 다른 변형) 뒤 저장된 변형은 하나이고 True는 정확히 하나(FP-012: 집합으로 단언), `PostgresVariantStore` 접속 실패가 원인 문자열 없는 `RuntimeError`(닫힌 루프백 포트로 시도, 짧은 `connect_timeout`, 테스트 `timeout`).
`tests/test_api_retention.py`(추가): apply 때 지운 thread의 변형 행만 지워지고 실패한 thread(`fail_one` 패턴)의 행은 남음, dry-run과 후보 없음이면 `variants.delete`를 부르지 않음, `variants=None`이면 기존과 같음, `startup_prune`이 `variants`를 키워드로 넘김. 기존 `test_cli_uses_fake_dependencies_and_safe_summary`에는 `monkeypatch.setattr(retention, "PostgresVariantStore", lambda: <InMemoryVariantStore 인스턴스>)` 한 줄만 더하고 기존 단언은 그대로 둔다.
`tests/test_m5_pg.py`(추가, `GOVOPS_TEST_DATABASE_URL` 없으면 기존 `database_url()`처럼 skip): 임시 DB를 만들고 지우는 기존 패턴으로 `PostgresVariantStore`의 put·get_many·recent·delete 왕복, 테이블이 없을 때 `delete`가 0이고 테이블을 만들지 않음(`to_regclass` 재확인), 동시 put 경쟁, `run_prune(…, variants=…)`가 실제로 지운 thread의 행만 지움. 모든 대기는 기존 `bounded`(30초)로 감싼다(FP-066).

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드·`costs.py`·`infra/litellm/config.yaml`)를 바꾸지 않았는가?
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

- `make test-m5-pg`와 `make checkpoint-prune`을 실행하지 마라. 이유: 실제 Postgres가 필요한 사용자 실행 명령이다. 추가한 `tests/test_m5_pg.py` 케이스는 AC 검증에서 `GOVOPS_TEST_DATABASE_URL`이 없어 skip된다.
- `Makefile`의 `db-init` 레시피를 바꾸지 마라. 이유: db-init은 DB만 만들고 테이블은 저장소 코드가 만든다(설계 2-3절, 지시 46). 이 step의 수정 파일에 `Makefile`이 없다.
- `delete` 경로가 테이블을 만들게 두지 마라(준비 경로의 `CREATE TABLE IF NOT EXISTS`는 `put`·`get_many`·`recent`에서만). 이유: 롤아웃을 켠 적 없는 DB의 스키마를 prune이 바꾸게 된다(등록 문서 6절).
- 소프트 삭제(`TaskService.delete_conversation`) 경로에서 변형 행을 지우지 마라. 이유: thread가 남아 있는 동안 행을 지우면 v1 서비스가 v2 thread를 맡고 `variant_missing`이 거짓으로 는다(등록 문서 6절). `api/tasks.py`는 이 step의 수정 파일이 아니다.
- Postgres 예외 문장·URL을 메시지·출력에 남기지 마라. 이유: 접속 정보가 새어 나간다(FP-069의 "클래스 이름만" 원칙, 기존 `PostgresTurnIndex`와 같다).

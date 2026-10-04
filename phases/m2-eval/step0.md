# Step 0: meta-extract

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약, 계층 경계 표의 `eval` 행)
- `/docs/EVAL.md` ("평가셋", "정답 출처"), `/docs/ADR.md` (ADR-010, ADR-013)
- `/src/govops/mocks/seed_extract.py` (A를 읽기 전용으로 추출하는 기존 패턴: 원자적 게시, A 커밋 해시 기록, 금지 이름 검사, `SeedValidationError`), `/tests/test_seed_extract.py`
- `/src/govops/mocks/auth.py` (`TEAMS`), `/data/seed/manifest.json`
- `/Makefile`, `/tests/test_makefile.py`, `/tests/test_architecture.py`, `/src/govops/eval/__init__.py`
- 읽기 전용 참고(수정 금지, 테스트 fixture로 복사 금지): A 저장소 `~/PycharmProjects/ko-docs-agentic-rag`의 `data/synthetic/plan.jsonl`, `data/synthetic/canaries.json`, `data/synthetic/injections.json`, `data/seed/incidents/*.yaml`, `data/seed/events/*.yaml`, `data/seed/banned_names.yaml`, `data/synthetic/ledger.sqlite`의 `tickets` 테이블 스키마

## 작업

평가셋의 정답 출처인 **A 합성 문서 메타데이터**를 일회성으로 추출하는 스크립트를 만든다. 실제 실행은 사용자가 한다(이 step에서는 A 저장소를 대상으로 실행하지 않는다). 테스트는 최소 합성 fixture(직접 작성)만 쓴다. TDD로 테스트를 먼저 쓴다.

### A 파일 형식 (확인된 사실)

- `plan.jsonl`: 줄마다 JSON. 사용할 키는 `doc_id`(`syn-*`), `doc_type`(meeting·weekly·runbook·guide·incident·notice), `team`, `access`(`{level: public|team|restricted, teams: [...], min_grade: int}`), `fact_ids`(list), `related_doc_ids`, `canary_fact_id`(없으면 null), `injection_id`(없으면 null). 그 밖의 키(`title_hint`, `style`, `seed` 등)는 복사하지 않는다.
- `canaries.json`: `{"canaries": [{canary_id, fact_id, value, doc_id, owner_team, min_grade}], "count": n}`. canary 값은 권한 밖 노출 판정에 쓰는 합성 토큰 문자열이다.
- `injections.json`: `{"injections": [{injection_id, doc_id, variant, text, access_level, insert_rule}], "count": n}`. **`text`(인젝션 문구)는 복사하지 않는다.** `injection_id`·`doc_id`·`variant`·`access_level`만 쓴다.
- `data/seed/incidents/*.yaml`: `id`, `title`, `date`, `owner_team`, `facts: [{fact_id, kind, value, date}]`. `data/seed/events/*.yaml`: `events: [{id, title, date, owner_team, type, facts: [...]}]`. `kind`는 datetime·service·text·person·id 등이다.
- `ledger.sqlite`의 `tickets` 테이블: `ticket_id`, `incident_id`, `is_anchor`, `fact_ids`(JSON 문자열 또는 구분 문자열 — 실제 형식을 확인해 파싱하라). 앵커 티켓(`is_anchor=1`)만 쓴다. 읽기 전용(`file:...?mode=ro`, `uri=True`)으로 연다.

### 만들 것

1. **`src/govops/eval/meta.py`** — 메타데이터 모델과 로더(pydantic v2 또는 dataclass, 기존 코드 스타일을 따른다).
   ```python
   class DocMeta: doc_id, doc_type, team, access_level, access_teams, access_min_grade,
                  fact_ids, related_doc_ids, injection_id | None, canary_fact_id | None
   class FactMeta: fact_id, kind, value, source_kind ("incident"|"event"|"canary"), source_id, owner_team | None
   class CanaryMeta: canary_id, fact_id, value, doc_id, owner_team, min_grade
   class InjectionMeta: injection_id, doc_id, variant, access_level
   class TicketMeta: ticket_id, incident_id | None, fact_ids
   class EvalMeta: docs, facts, canaries, injections, tickets, a_commit (+ 인덱스 조회 보조: doc_by_id, fact_by_id)
   def load_meta(path: Path) -> EvalMeta        # 스키마 검증 + 불변식 검사
   def meta_sha256(path: Path) -> str
   ```
   불변식: 모든 `doc_id`가 `syn-`으로 시작, `doc_id`·`fact_id` 중복 없음, 문서의 `fact_ids`·`related_doc_ids`가 존재하는 항목을 가리킴(참조 무결성), `access_level`이 허용 값, `access_teams`가 `govops.mocks.auth.TEAMS`의 부분집합. 위반은 값(본문·문구)을 담지 않는 안전한 `MetaValidationError`로 올린다.
2. **`src/govops/eval/meta_extract.py`** — CLI(`python -m govops.eval.meta_extract --a-root PATH [--out DIR]`, 기본 출력 `data/eval/`).
   - 산출물: `data/eval/meta.json`(키 정렬·들여쓰기 2·UTF-8·끝 개행, 항목은 id 순 정렬 → 같은 입력이면 바이트 동일)과 `data/eval/meta.manifest.json`(`a_commit`, `expected_a_commit`("d2eec9c"), `a_commit_matches_expected`, 항목별 개수, `meta_sha256`, 기대값 검사 결과).
   - A 커밋 해시는 `git -C <a-root> rev-parse HEAD`로 얻는다(`seed_extract._resolve_commit`과 같은 방식). 기준 커밋과 다르면 **경고만** 기록하고 계속한다(ADR-010).
   - 게시는 `seed_extract._publish`처럼 스테이징 후 원자적으로 교체한다(중간 실패 시 기존 파일 유지). `--out`이 A 저장소 안이면 거부한다.
   - **기대값을 코드에 먼저 고정한다**: 문서 200개, 문서 유형별 meeting 50·weekly 40·runbook 30·guide 30·incident 25·notice 25, canary가 있는 문서 30개, 인젝션 문서 12개. 실제 값이 다르면 요약에 `MISMATCH`를 출력하고 종료 코드 1로 끝낸다(파일은 게시하되 manifest의 검사 결과에 기록). 테스트에서는 기대값을 인자로 바꿔 넣을 수 있게 한다.
   - 종료 시 사용자에게 보여 줄 요약(개수·해시·기대값 일치 여부)을 출력한다. 본문·인젝션 문구·토큰은 출력하지 않는다.
3. **`Makefile`**: `eval-meta-extract` 타깃(`uv run python -m govops.eval.meta_extract --a-root $(A_ROOT)`, 위에 "읽기 전용 A 필요, 사용자 실행" 주석). `verify`가 호출하지 않는다. `.PHONY`와 `tests/test_makefile.py`의 사용자 실행 타깃 검사에 반영한다.
4. **`data/eval/.gitkeep`는 유지**한다(`meta.json`은 사용자가 실행해 커밋한다).

### 테스트 (`tests/test_eval_meta.py`, `tests/test_eval_meta_extract.py`)

- 최소 fixture A 트리(tmp_path에 직접 작성: `plan.jsonl` 3~4줄, canaries·injections 각 1~2개, incidents·events yaml 각 1개, 앵커 티켓 1개가 든 `ledger.sqlite`, git 저장소로 `git init` 후 커밋)로 추출 → `meta.json` 구조·정렬·정수 개수 검사. 같은 입력 두 번 → 바이트 동일.
- fixture의 `data/synthetic/docs/` 디렉토리에 센티널 본문(`BODY_SENTINEL_...`)을 넣고, 인젝션 `text`에도 센티널을 넣었을 때 **산출물 어디에도 두 센티널이 없다**. 스크립트가 `docs/` 디렉토리를 열지 않는다는 것도 확인한다(예: 그 디렉토리 권한을 없애거나 존재하지 않아도 성공).
- `syn-` 아닌 `doc_id`, 참조 무결성 위반, 금지 이름 포함 값(fixture `banned_names.yaml`) → `MetaValidationError`/종료 코드 비정상, **기존 `meta.json`은 그대로 남는다**.
- 기대값 불일치 시 종료 코드 1·`MISMATCH` 출력, 일치 시 0.
- A 저장소 안으로의 `--out`은 거부. A 파일은 열기 전후로 수정되지 않는다(mtime·해시 비교).
- 계층 가드(`tests/test_architecture.py`) 통과: `eval`은 `govops.gateway`·`govops.broker`를 import하지 않는다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 산출물에 문서 본문·인젝션 문구·토큰이 없고 `syn-*`만 있는가?
   - A 저장소를 수정하지 않았고, 추출이 결정론인가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `make eval-meta-extract`를 실제 A 저장소에 대해 실행하지 마라. 이유: 추출·커밋은 사용자 작업이다.
- A 저장소의 파일을 수정하거나 테스트 fixture로 복사하지 마라. 이유: A는 수정 금지이고, 합성 문서 데이터를 B 테스트에 복사하면 CRITICAL 규칙(합성 기록 관리)을 흐린다.
- `data/synthetic/docs/`(문서 본문)를 읽지 마라. 이유: 메타데이터만 추출한다(본문 없음).
- 인젝션 `text`를 산출물·로그·출력에 넣지 마라. 이유: 문구는 평가 입력이 아니라 A 문서 안에 있어야 하며, 메타데이터에는 ID만 둔다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

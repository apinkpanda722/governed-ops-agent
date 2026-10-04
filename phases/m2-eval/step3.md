# Step 3: review-cards

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약), `/docs/EVAL.md` ("평가셋", "사람이 할 일"), `/docs/ADR.md` (ADR-010)
- 이전 step 산출물: `/src/govops/eval/meta.py`, `/src/govops/eval/spec.py`(`TaskSpec`, `load_specs`, `spec_hash`), `/src/govops/eval/taskgen.py`, `/src/govops/eval/visibility.py`, `/tests/test_eval_taskgen.py`
- `/Makefile`, `/tests/test_makefile.py`, `/.gitignore`

## 작업

사람이 정답 명세 60개를 전수 검수할 수 있도록 **문항 카드 HTML을 생성하는 명령**과, 검수 결과를 읽는 **확정본 로더**를 만든다. **검수 결과는 사람이 작성한 파일만 확정본으로 쓴다.** 코드는 확정본을 만들거나 수정하지 않는다. TDD로 테스트를 먼저 쓴다.

### 만들 것

1. **`src/govops/eval/cards.py`** — 카드 HTML 생성.
   ```python
   def build_cards(specs: Sequence[TaskSpec], meta: EvalMeta, users: Sequence[Mapping], tickets: Sequence[Mapping], out_dir: Path) -> list[Path]
   ```
   - 출력: `out_dir/index.html`(60개 목록·유형/split/`executable_in` 필터·명세 해시 앞 12자리·확정 상태 열) + 유형별 파일 `T1.html`…`T6.html`(카드 나열). 기본 `out_dir`은 `data/interim/eval/cards/`(gitignore 대상이다 — 생성물은 커밋하지 않는다).
   - **카드 내용(task마다)**: `task_id`·유형/하위유형·split·`executable_in`·요청자(라벨, 팀·직급) / **요청 문장** / **기대 상태**(outcome, 기대 부작용 종류·대상·건수·구조, 승인 시나리오 단계) / **필수 fact 표**: 각 fact의 `fact_id`·`kind`·`value`·`mode`, 그리고 **근거 발췌(합성 문서 메타데이터 기준)** — 그 fact를 담은 문서의 `doc_id`·`doc_type`·`team`·접근 수준·허용 팀·최소 직급과 "요청자가 읽을 수 있음/없음"(`can_read` 결과), 장애·이벤트 출처(`source_kind`·`source_id`) / `derived_values`와 계산 근거(예: 집계 조건) / **금지 사항**(누출 금지 문서·티켓·값, 금지 도구) / **확인 항목 체크리스트**.
   - 근거 발췌는 `meta`에 있는 값(메타데이터)만 쓴다. **문서 본문은 어디에도 없다.**
   - **확인 항목(유형별 고정 문구, 코드 상수)**: 공통 — 요청 문장이 자연스럽고 한 가지 일만 요청하는가 / 요청자가 소스를 읽을 수 있는가(또는 읽을 수 없어야 하는가) 표와 일치하는가 / 필수 fact가 근거 문서에 실제로 있는가 / 금지 값이 요청 문장에 노출되지 않았는가. T1 — 요약해야 할 사실이 충분한가. T2·T4 — 수신 채널이 허용 목록(자기 팀 채널·전사 공지)인가, 전사 공지는 승인자 정책이 맞는가. T3 — 대상 티켓을 요청자가 읽을 수 있는가, 후속 티켓의 원 티켓 연결이 있는가. T4 — 집계 값이 요청자 기준 가시 티켓으로 계산되었는가. T5 — 올바른 거절 기준(없음과 구별되지 않는 응답)이 명세와 맞는가, 간접 인젝션 문서가 요청자에게 읽히는가. T6 — 승인 시나리오 단계의 행위자·기대 응답이 맞는가.
   - HTML은 **네트워크 없이 열린다**: 외부 CSS·JS·폰트·이미지 참조 금지, 인라인 `<style>`만(필터가 필요하면 아주 작은 인라인 `<script>` 허용), 모든 동적 값은 `html.escape`한다. 인쇄해도 읽을 수 있게 카드마다 페이지 나눔(`break-inside: avoid`)을 둔다. 확정본(아래)이 있으면 index에 상태(승인·반려·수정 필요·미검수·무효)를 표시하되 카드 생성이 확정본을 수정하지 않는다.
2. **`src/govops/eval/review.py`** — 확정본 읽기 전용 로더.
   - 확정본 경로(고정): `data/eval/review/confirmed.json`. **사람이 직접 작성**하는 파일이다. 형식:
     ```json
     {"reviewer": "...", "reviewed_at": "YYYY-MM-DD",
      "tasks": {"T1-01": {"status": "approved|rejected|needs_change", "spec_sha256": "<검수한 명세 해시>", "note": ""}}}
     ```
   - ```python
     @dataclass(frozen=True)
     class ReviewReport: confirmed: list[str]; rejected: list[str]; needs_change: list[str]; pending: list[str]; stale: list[str]   # stale: 확정본의 해시가 현재 명세 해시와 다름(명세가 바뀜 → 확정 무효)
     def review_report(specs: Sequence[TaskSpec], review_path: Path) -> ReviewReport
     def confirmed_specs(specs: Sequence[TaskSpec], review_path: Path) -> list[TaskSpec]   # status == "approved"이고 spec_sha256이 현재 명세와 같은 것만
     ```
   - 확정본이 없거나 형식이 잘못되면 `confirmed_specs`는 **빈 목록**을 돌려주고(전부 미검수), 확정본에 없는 task는 `pending`이다. `reviewer`가 비어 있거나 `status`가 허용 값이 아니면 `ReviewFormatError`.
   - **이 모듈과 `cards.py`는 확정본 경로에 쓰기 모드로 열지 않는다.** 확정본을 만드는 함수·명령은 어디에도 두지 않는다(테스트로 강제).
3. **`src/govops/eval/review_cli.py`**: `python -m govops.eval.review_cli cards` → 카드 생성(입력 `data/eval/tasks.json`, `meta.json`, 시드). `python -m govops.eval.review_cli status` → 확정·반려·수정 필요·미검수·무효 개수와 목록을 출력하고, **60개 전부 `approved`일 때만 종료 코드 0**, 아니면 1. 입력 파일이 없으면 "먼저 `make eval-meta-extract`·`make eval-gen`" 안내와 함께 종료한다.
4. **`Makefile`**: `eval-cards`(`uv run python -m govops.eval.review_cli cards`)와 `eval-review-status`(`... status`). 각각 위에 "사용자 실행" 주석. `verify`가 호출하지 않는다. `.PHONY`·`tests/test_makefile.py` 반영.
5. **`.gitignore`**: `data/interim/`은 이미 무시된다. 카드 출력 경로가 그 아래임을 `tests/test_repo_rules.py`의 `git check-ignore` 방식 테스트로 확인한다.

### 테스트 (`tests/test_eval_cards.py`, `tests/test_eval_review.py`)

- 합성 fixture(step 2 테스트의 fixture 생성 방식 재사용)로 카드 생성 → 파일 목록(`index.html`, `T1.html`…`T6.html`), 모든 task_id·요청 문장·fact_id·근거 문서 ID가 HTML에 있다. 요청 문장에 `<script>`·`&`를 넣은 명세에서 이스케이프가 되어 있다. 외부 URL(`http://`, `https://`, `src=`·`href="http`)이 HTML에 없다.
- 문서 본문 센티널(`BODY_SENTINEL_...`)이 fixture 어디에도 없는 구조에서, 카드에 meta에 없는 문자열이 나오지 않는다(예: 카드에 쓰인 모든 `doc_id`가 meta에 있음).
- 확정본: 일부 `approved`(해시 일치), 일부 해시 불일치(→ `stale`), 일부 `rejected`/`needs_change`, 미기재(→ `pending`)를 담은 파일로 `review_report`·`confirmed_specs`가 기대대로다. 확정본이 없으면 빈 목록. 잘못된 형식은 `ReviewFormatError`.
- **쓰기 금지 강제**: `cards.py`·`review.py`·`review_cli.py` 소스에 확정본 경로 문자열(`review/confirmed.json`)을 쓰기 모드로 여는 코드가 없다(AST로 `open(..., "w"...)`·`write_text`·`write_bytes` 호출 대상 검사). `build_cards`·`review_cli cards`를 실행해도 확정본 파일의 mtime·해시가 변하지 않는다.
- `status` 종료 코드: 전부 approved면 0, 하나라도 아니면 1.
- 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 카드에 본문이 없고 메타데이터 기준 근거만 있는가? HTML이 오프라인에서 열리고 값이 이스케이프되는가?
   - 코드가 확정본을 절대 쓰지 않는가? 명세 해시가 바뀌면 확정이 무효가 되는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `data/eval/review/confirmed.json`을 만들거나 쓰는 코드·스크립트·fixture 생성기를 프로덕션 코드에 넣지 마라. 이유: 확정본은 사람이 작성한 파일만 인정한다. 테스트는 `tmp_path`에 확정본 형식 파일을 직접 쓰는 것만 허용한다.
- 카드에 문서 본문, 인젝션 문구, 토큰을 넣지 마라. 이유: 메타데이터 기준 근거만 보인다(본문 없음, syn-*만).
- 외부 리소스(CDN, 폰트)를 HTML에 넣지 마라. 이유: 오프라인 검수와 네트워크 없는 `make verify`.
- `make eval-cards`를 실제 데이터로 실행하지 마라. 이유: 입력(`meta.json`·`tasks.json`)은 사용자가 만든 뒤 실행한다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

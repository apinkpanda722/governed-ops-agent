# Step 4: sample-cli

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`(ADR-015·019·026), `/docs/EVAL.md`
- `/docs/M8_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m8-grader.md`, `/docs/results/hypotheses-m8-citation.md`, `/docs/results/hypotheses-m8-hallucination.md`(사전 등록, 읽기만 한다), `/docs/HUMAN_GRADING_GUIDE_M8.md`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/grounding/` 전체(이전 step 산출물: `sentences.py`·`db.py` 포함), `/tests/test_grounding_*.py`
- `/src/govops/eval/human.py`(`select_human_sample`, `HUMAN_SAMPLE_*`, 등록·게이트 패턴), `/src/govops/eval/grading_cli.py`(CLI 구조, `_specs`), `/tests/test_eval_human.py`, `/tests/test_human_grading_guide.py`
- `/docs/HUMAN_GRADING_GUIDE_M8.md`(라벨 칸 이름의 원본), `/docs/results/human-grading-registration.json`
- `/Makefile`, `/tests/test_makefile.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/grounding/sample.py`, `src/govops/grounding/sheets.py`, `src/govops/grounding/registration.py`, `src/govops/grounding/cli.py`, `Makefile`, `tests/test_makefile.py`, `tests/test_grounding_sample.py`, `tests/test_grounding_sheets.py`, `tests/test_grounding_registration.py`, `tests/test_grounding_cli.py`, `tests/test_grounding_guide.py`, `src/govops/grounding/db.py`, `src/govops/grounding/probe.py`, `tests/test_grounding_db.py`, `tests/test_grounding_probe.py`. 단 `db.py`·`probe.py`·`tests/test_grounding_db.py`·`tests/test_grounding_probe.py`는 아래 7절 기능 추가에 한하며 기존 단언 약화 금지. TDD(테스트를 먼저 쓰고 통과하는 구현을 쓴다).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`
수정 허용 경로: `src/govops/grounding/sample.py`, `src/govops/grounding/sheets.py`, `src/govops/grounding/registration.py`, `src/govops/grounding/cli.py`, `Makefile`, `tests/test_makefile.py`, `tests/test_grounding_sample.py`, `tests/test_grounding_sheets.py`, `tests/test_grounding_registration.py`, `tests/test_grounding_cli.py`, `tests/test_grounding_guide.py`, `src/govops/grounding/db.py`, `src/govops/grounding/probe.py`, `tests/test_grounding_db.py`, `tests/test_grounding_probe.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/m8-grounding/`, 평가 명세를 읽는 `load_specs`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 단 이 step이 새로 작성하는 테스트에 한한 제한이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다. `docs/results/m8-grader-registration.json`은 사용자 실행 CLI가 런타임에 덧붙여 쓰는 파일이며, 이 step의 테스트는 임시 저장소에서만 쓰고 실제 파일을 만들거나 고치지 않는다.

**배경**: 보정·검증 표본 추출, 사람 라벨 시트와 근거 카드, CSV 해시 등록과 가드, 채점·보고 CLI를 만든다. 가드 순서는 hypotheses-m8-grader §6·§8·§12가 정본이다. 이 step에서 실제 Gemini·DB를 호출하지 않는다(채점은 가짜 `call`을 주입하는 seam으로 테스트).

### 1. `sample.py`

```python
CALIBRATION_N = 30; HELDOUT_TARGET = 25; HELDOUT_MIN = 20; HELDOUT_SEED = 20261016
def calibration_rows(specs, test_observations) -> list[tuple[TaskSpec, Observation]]: ...   # eval.human.select_human_sample 재사용
def heldout_pool(specs, test_obs_run0, dev_obs, calibration_task_ids) -> list[PoolRow]: ...
def draw_heldout(pool_with_evidence: Sequence[PoolRow]) -> list[PoolRow]: ...
```

- `calibration_rows`는 `eval.human.select_human_sample`을 호출해 기존 30행 task 목록을 재현한다(규칙 해시가 `human-grading-registration.json`의 `sample_rule_sha256`과 다르면 실패).
- `heldout_pool`: test는 공식 N2-1(`run_index 0`) 관측 중 **보정 task에 속하지 않는 task**, dev는 공식 N1a 관측 전체. task당 1행. 보정 task와 교집합이 있으면 `ValueError`.
- 근거가 없는 행(`evidence_missing`)·상한 초과·`deliverable_empty`는 풀에서 빼고 사유별 건수를 반환값에 담는다. 풀(근거 확보 행)이 20 미만이면 `PoolInsufficient`(배분을 바꾸지 않고 중단). 20~24면 전부, 25 이상이면 `random.Random(HELDOUT_SEED)`로 25행 추출(정렬된 키에서 `sample`, 층화 없음). 유형 분포를 반환한다.
- 보정도 근거 확보 행이 20 미만이면 중단한다.

### 2. `sheets.py`

- `write_label_sheets(rows, out_dir)`: **문장 단위** `labels.csv`(열 `sample_id, sentence_id, task_id, type, request, sentence, label, note`, 문장마다 한 줄; 같은 행의 `request`는 첫 문장 줄에 둔다)와 `citations.csv`(열 `sample_id, sentence_id, doc_id, supports`, 인용이 있는 문장의 (문장, ID)마다 한 줄), 근거 카드 `evidence.html`(sample_id별로 전체 문장 목록과 근거 항목을 **HTML 이스케이프**해 표시, 외부 리소스·스크립트 없음)을 `data/interim/m8-grounding/<calibration|heldout>/`에 쓴다(`0600`). 문장은 `sentences.build_sentences`로만 만든다. 채점기 결과·기존 0~3 점수를 시트에 넣지 않는다. sample_id는 셔플한 순번(`S01`…)으로 task 순서를 숨긴다. 시트에 `splitter_rules_sha256`을 담은 `meta.json`을 함께 쓴다.
- `load_labels(path, expected)`: UTF-8 CSV 검증. 열 이름·행 수·(sample_id, sentence_id) 집합이 생성된 시트와 **정확히 일치**해야 하고, `label`은 `supported`/`unsupported`/`contradicted`/`not_claim`만, `supports`는 `지지`/`비지지`만 허용한다. 빈 칸이 있으면 오류.

### 3. `registration.py`

- `docs/results/m8-grader-registration.json`에 단계별 해시를 **덧붙여** 기록한다(기존 항목 수정 금지): `calibration_labels_sha256`, `evidence_max_chars`, `adopted_prompt_version`·`adopted_prompt_sha256`, `heldout_labels_sha256`, `heldout_task_list_sha256`.
- 등록 항목에 `splitter_rules_version`(`sent-v1`)과 `splitter_rules_sha256`을 추가한다. 시트 생성·라벨 등록·채점 때 현재 `splitter_rules_sha256`이 등록값과 다르면 거부한다(분할이 바뀌면 문장 번호가 무효).
- 가드(각각 코드로 강제하고 테스트한다; git 확인은 `subprocess.run([...], timeout=30)`): 등록 문서 3종(`hypotheses-m8-grader/citation/hallucination.md`)이 HEAD에 있고 등록 커밋 이후 변경되지 않았는지(`hypotheses_not_committed`), `adopted_prompt_sha256`이 등록·**커밋**돼 있지 않으면 held-out 추출 거부, `heldout_labels_sha256`이 등록·커밋돼 있지 않거나 실제 파일 해시와 다르면 채점 거부, 같은 held-out에 대한 두 번째 채점 거부(결과 파일이 이미 있으면), 보정 라벨 해시 미등록이면 보정 루프 거부. 작업 트리 청결 검사는 비용 원장만 예외로 `git status --porcelain -- <대상 경로>`로 범위를 한정한다(FP-064).

### 4. `cli.py` (`python -m govops.grounding.cli <subcommand>`)

서브커맨드: `sheet-calibration`, `register-labels`, `cost-probe`, `calibrate`(프롬프트 버전 하나를 보정 표본+보정 합성 주입에 1회 실행하고 `calibration-log.jsonl`에 문장 단위 κ·탐지율·오탐률·비용을 덧붙임), `adopt`(채택 버전 선택 규칙 적용 후 해시 출력, 커밋은 사용자), `draw-heldout`, `sheet-heldout`, `grade-heldout`(3회 반복, held-out + 합성 주입 1회), `measure`(검증 통과 시에만, N2 순서), `report`(결과 JSON·표 출력).

- 채점기·원장·증거 로더는 팩토리 함수로 주입한다(`build_grader(call=None)`) → 테스트는 가짜 `call`을 넣는다. 실제 키는 `os.environ["GEMINI_API_KEY"]`(기본값 없음)로만 읽는다.
- `cost-probe`: 합성 입력 3~5건(실제 근거·실제 라벨 사용 금지)으로 호출당 비용을 보고만 한다. 보정 단계 상한에 합산된다.
- `grade-heldout`: 결과를 `data/interim/m8-grounding/heldout-result.json`에 쓴다. 이미 있으면 거부한다. 보고서에는 §10의 모든 값(문장 단위 κ와 클러스터 CI, 4×4 혼동 행렬, 정밀도·재현율, 행 단위 κ, 탐지율·오탐률, 판정 불가 조건 충족 여부), 근거 부족·초과·채점 실패·`split_count_changed` 건수, 문장 수 분포, 유형 분포를 포함하고 **미달·판정 불가여도 모든 값을 그대로 출력**한다. 판정 문자열은 `metrics.validation_verdict`의 값을 그대로 쓰며 CLI가 기준을 바꾸지 않는다.
- CLI의 표준 출력·표준 오류·로그와 `calibration-log.jsonl`·`heldout-result.json`·`report` 결과에는 근거 본문·deliverable 본문·ID 목록을 넣지 않는다(개수·해시·코드만). 예외: 2절의 사람 라벨링용 시트 파일(`labels.csv`·`citations.csv`·`evidence.html`·`meta.json`)은 라벨링을 위해 문장 원문·task_id·근거 본문을 담으며 `data/interim/m8-grounding/` 아래 `0600`으로만 쓴다.

### 5. Makefile (`verify` 레시피 불변)

`m8-grounding-sheet`, `m8-grounding-register`(`register-labels`), `m8-grounding-calibrate`, `m8-grounding-heldout`(`draw-heldout`+`sheet-heldout`), `m8-grounding-grade`, `m8-grounding-measure`, `m8-grounding-report` 타깃. 모두 `uv run --env-file .env python -m govops.grounding.cli <sub> $(ARGS)`, "사용자 실행" 주석, 타깃마다 자기 줄 `.PHONY: <타깃>`을 둔다(step 0이 만든 `.PHONY: m8-evidence-probe` 줄은 `tests/test_makefile.py`가 정확히 단언하므로 바꾸지 않는다). `tests/test_makefile.py`(수정 허용: **새 타깃들이 `verify`에 없고 사용자 실행 타깃임을 확인하는 테스트 추가에 한함**).

### 6. 테스트 (합성 데이터)

- `tests/test_grounding_sample.py`: 합성 명세·관측으로 보정/held-out 교집합 0, task당 1행, 풀 19 → `PoolInsufficient`, 20·24 → 전부, 30 → 정확히 25이고 같은 시드 같은 결과, 제외 사유별 건수.
- `tests/test_grounding_sheets.py`: 문장 단위 시트 열 이름 고정(문장마다 한 줄, 인용 시트는 (문장, ID)마다 한 줄), 라벨 값 4종·`지지`/`비지지`만 허용, 생성 시트와 (sample_id, sentence_id) 집합이 다르면 거부, 분할 규칙 해시 불일치 거부, 근거 카드 HTML 이스케이프(`<script>` 합성 문자열이 실행 가능한 태그로 남지 않음), 시트에 채점기 결과·기존 점수 열 없음, sample_id가 task 순서를 드러내지 않음, `load_labels`가 빈 칸·잘못된 값을 거부, 파일 권한 0600.
- `tests/test_grounding_registration.py`: 임시 git 저장소로 각 가드(문서 미커밋, 해시 미등록, 해시 불일치, 두 번째 채점, 등록 파일 변경)가 거부함, 정상 경로 통과, 기존 등록 항목을 덮어쓰지 않음.
- `tests/test_grounding_cli.py`: 가짜 `call`로 `calibrate`→`adopt`→`draw-heldout`→`grade-heldout` 종단 흐름, 채택 규칙(문장 단위 κ 최고, 동률이면 탐지율, 그래도 같으면 이른 버전), 개정 4번째 요청 거부(상한 3), 비용 상한 초과 시 중단, 미달·판정 불가에서도 모든 값을 출력하고 종료 코드가 0, 출력에 합성 본문이 없음. 토큰 값이 출력되지 않음.
- `tests/test_grounding_guide.py`: `docs/HUMAN_GRADING_GUIDE_M8.md`에 `## 1.`~`## 7.` 제목, 예시 절, 4개 라벨 이름(`supported`·`unsupported`·`contradicted`·`not_claim`), `supports`·`지지`·`비지지`, `make m8-grounding-register`, `4~5.5시간` 문구가 있고 시트의 `label` 칸 이름이 안내서와 일치함.

### 7. DB 이름 지정 옵션 (`db.py`·`probe.py` 기능 추가에 한함, 기존 단언 약화 금지)

복원한 임시 DB를 사용자가 연결 문자열을 만들지 않고 가리키게 한다.

```python
DBNAME_RE = r"^[a-z_][a-z0-9_]{0,62}$"
def url_with_dbname(url: str, dbname: str) -> str: ...
```

- `url_with_dbname`: URL의 **경로(DB 이름)만** 바꾸고 사용자·비밀번호·호스트·포트·쿼리는 그대로 둔다(`urllib.parse`로 분해·재조립, 비밀번호의 특수문자 인코딩 보존). `dbname`이 `DBNAME_RE`와 맞지 않으면 `ValueError("invalid_dbname")`. 오류 메시지에 URL을 넣지 않는다.
- `probe.py`의 CLI와 이 step에서 만드는 `cli.py`(근거 로더를 쓰는 모든 서브커맨드)가 `--dbname NAME`을 받는다. `--url-env`(기본 `GOVOPS_DATABASE_URL`)의 값에 `url_with_dbname`을 적용해 `db.open_readonly_saver`로 연결한다. 출력에는 **DB 이름만** 쓰고 URL은 쓰지 않는다. `--dbname` 없으면 기존 동작 그대로.
- 테스트(`tests/test_grounding_db.py`·`tests/test_grounding_probe.py`에 추가): 비밀번호(특수문자 포함)·포트·쿼리 보존, 경로만 교체, 잘못된 DB 이름(대문자·숫자 시작·길이 64·`;`·공백·빈 문자열) 거부, 오류 메시지·예외·stdout/stderr에 URL과 비밀번호 문자열이 없음, probe `--dbname` 사용 시 출력에 DB 이름만 있음, `cli.py` 서브커맨드가 `--dbname`을 받음.

**사용자 실행(이 step에서 실행하지 않는다)**: `make m8-grounding-sheet`, `make m8-grounding-register`, `make m8-grounding-calibrate`, `make m8-grounding-heldout`, `make m8-grounding-grade`, `make m8-grounding-measure`, `make m8-grounding-report` — 순서와 가드는 hypotheses-m8-grader §12.

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

- `make m8-grounding-sheet`·`make m8-grounding-register`·`make m8-grounding-calibrate`·`make m8-grounding-heldout`·`make m8-grounding-grade`·`make m8-grounding-measure`·`make m8-grounding-report`·`make m8-evidence-probe`를 실행하지 마라. 이유: 실제 Gemini·DB를 호출하는 사용자 실행 항목이다(AGENTS.md).
- 가드를 우회하는 옵션(`--force`·`--skip-guard`·`--rerun`)이나 환경 변수를 만들지 마라. 이유: held-out은 한 번만 채점하고 등록 이후 변경을 막는 것이 이 검증의 전제다(hypotheses-m8-grader §6·12, FP-043).
- 기존 `docs/results/human-grading-registration.json`·`docs/HUMAN_GRADING_GUIDE.md`·`eval/human.py`를 수정하지 마라. 이유: M6 등록·동결 경로다. 새 등록은 `m8-grader-registration.json`에 덧붙인다.
- DB 접근에 `open_checkpointer`나 `setup()`을 쓰지 마라(`db.open_readonly_saver`만). 이유: 읽기 전용 계약이며 step 0의 AST 테스트가 막는다.
- 사람 라벨링용 시트(`labels.csv`·`citations.csv`·`evidence.html`·`meta.json`)와 `sheet-calibration`·`sheet-heldout`의 출력에 채점기 결과나 기존 0~3 점수를 섞지 마라. 라벨링이 끝난 뒤 `grade-heldout`·`measure`·`report`의 집계 지표 출력은 이 금지의 대상이 아니다. 이유: 사람 라벨링은 채점기 결과를 보지 않는 블라인드여야 한다.
- `git status --porcelain`을 경로 한정 없이 쓰지 마라. 이유: 작업 트리 청결 검사는 예외 목록을 거치는 한 곳이 맡는다(FP-064).

## 소비자 점검(시뮬레이션)

`Makefile`에 `m8-*` 타깃을 더한 복사본에서 전체 pytest를 돌렸을 때(step 0 점검과 같은 실행) `tests/test_makefile.py`·`tests/test_docs_consistency.py`·`tests/test_demo_scripts.py`·`tests/test_evalrun_dev_m3_m6.py`·`tests/test_evalrun_redteam_run.py`·`tests/test_human_grading_guide.py`는 모두 통과했다(`verify` 레시피 명령 목록은 정확 일치 단언이므로 바꾸지 않는다). 새 안내서는 `tests/test_grounding_guide.py`가 검사한다. 새 `hypotheses-m8-*.md`는 `tests/test_m7_freeze.py`의 `EXPECTED`(명시 파일만 대조)에 영향이 없다. 이 step의 새 `src/govops/grounding/*.py`는 `tests/test_architecture.py`의 AST 검사를 통과해야 한다.

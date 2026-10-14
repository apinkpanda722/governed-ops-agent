# Step 5: test-run-path

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_REDTEAM_RUN_DESIGN_DRAFT.md`(이 phase의 설계), `/docs/results/hypotheses-m6-redteam-run.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/evalrun/dev_m3.py`(`select_tasks`, `run_dev_m3`, `main`, `guard_live_m3`, manifest 작성 — 수정 대상), `/src/govops/evalrun/run_guards.py`, `/src/govops/eval/v2_validity.py`, `/src/govops/golden.py`(`GOLDEN_TASKS`), `/src/govops/eval/human.py`(`COLUMNS`, `select_human_sample`), `/src/govops/eval/grading.py`(`DIMENSIONS`, `criteria_for`, `build_prompt`의 척도), `/src/govops/eval/grading_cli.py`
- `/docs/EVAL.md`(정확도·사람 채점 절), `/tests/test_evalrun_dev_m3.py`, `/tests/test_makefile.py`, `/tests/test_golden_m3.py`, `/Makefile`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/evalrun/dev_m3.py`, `Makefile`, `docs/HUMAN_GRADING_GUIDE.md`(신규), `tests/test_evalrun_dev_m3_m6.py`(신규), `tests/test_human_grading_guide.py`(신규). TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `data/eval/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/evalrun/dev_m3.py`, `Makefile`, `docs/HUMAN_GRADING_GUIDE.md`, `tests/test_evalrun_dev_m3_m6.py`, `tests/test_human_grading_guide.py`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 위 경로의 파일 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 테스트는 위 경로의 실제 파일을 읽지 않고 임시 폴더에 만든 합성 파일만 쓴다. 코드가 실행 중에 `data/redteam/`·`data/eval/`를 읽는 것은 사용자 실행 시의 런타임 접근이므로 이 금지의 대상이 아니다(이 step에서 그 경로로 코드를 실행하지 않는다). 구조는 이 문서·등록 파일·기존 코드와 테스트로 파악한다.

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step은 `evalrun/dev_m3.py`의 기존 인자 경로를 건드린다. 변경을 시뮬레이션한 전체 `pytest`(`git archive HEAD` 복사본, `.env`·`data/interim`·`data/raw` 미복사)에서 새로 깨진 기존 테스트는 없었다. 소비자 점검 결과(시뮬레이션에서 통과, 수정 불필요): `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_anchors.py`, `tests/test_evalrun_faults.py`, `tests/test_evalrun_observe.py`, `tests/test_evalrun_tool_trace.py`, `tests/test_evalrun_worktree.py`, `tests/test_faults_client.py`, `tests/test_guardrail_registration.py`, `tests/test_prereg_m3.py`, `tests/test_prereg_m5_prompt.py`, `tests/test_golden_m3.py`, `tests/test_makefile.py`, `tests/test_docs_consistency.py`, `tests/test_architecture.py`, `scripts/test_hooks.py`. `tests/test_makefile.py`는 **단언이 실제로 깨질 때만, 새 타깃을 더하는 것에 한해** 수정한다(정확 일치를 부분 집합 비교로 바꾸지 않는다). 기존 테스트를 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: N1a·N1b(dev 20)와 N2·N3(test 40)을 같은 러너로 실행하되 (1) test 항목은 사전 등록(`hypotheses-test-targets.md`)이 커밋된 뒤에만, (2) 골든은 재기록하지 않고, (3) 실행마다 새 프로세스로 v2 상태 카운터를 시작하고, (4) 결과 디렉터리를 `data/results/test/`로 나눈다. 기존 `dev_m3`의 기본 동작(인자 없음)은 **바이트 단위로 같아야 한다**.

### 1. `evalrun/dev_m3.py`(최소 변경, 기본값은 기존 동작)

- `select_tasks(rows, requested=None, *, smoke=False, split="dev")`: 기본 `split="dev"`는 기존 동작. `split="test"`는 `spec.split == "test"`이고 `executable_in in {"m2", "m3"}`인 전체(40건이어야 한다)를 돌려주며 부분 선택은 `partial_test_refused`.
- `run_dev_m3(..., allow_test=False, record_golden=True, run_label=None, config=None)`: `allow_test`가 아니면 기존 `test_task_refused` 그대로. `allow_test`이면 모든 spec이 test여야 하며 결과 디렉터리는 `data/results/test/<uuid>`다(dev는 기존 `data/results/dev/<uuid>`). **`allow_test`이면 `record_golden`은 항상 False**(골든 녹화·`status.json` 쓰기를 하지 않는다). `record_golden=False`이면 골든 대상 task도 녹화하지 않는다(`record = record_golden and not smoke and spec.task_id in GOLDEN_TASKS`).
- manifest에 `run_label`·`config`·`m6_run`(`--run` 값; `--m6`가 아닌 경로에서는 키를 쓰지 않는다)·`v2_validity`(`v2_validity(switch, status_counts)` 결과)·`validity`(`validity_label(v2_validity)`: `"valid"`|`"invalid_v2_off"`)를 더한다. **기존 `run_role`(탐색 예산의 `official`/`reference`)은 값·의미를 바꾸지 않고 `exploration_role` 복사도 만들지 않는다**(M5 결과 문서와 `tests/test_evalrun_dev_m3.py`가 그 뜻으로 읽는다). 기존 경로(`--m6` 없음)의 manifest에는 새 키를 쓰지 않는다.
- `main(argv)`: 새 인자 `--m6`(플래그), `--split dev|test`(기본 `dev`), `--config v1|v1+v2`, `--run <라벨>`, `--repeat-index 1..3`, `--no-record-golden`. **`--m6`가 없으면 새 인자는 모두 거부**(`invalid_arguments`)하고 기존 경로를 그대로 탄다. `--m6`이면: `run_guards.guard_switch`, `guard_fresh_status_counts`, `guard_redteam_registration`, `guard_clean_worktree`를 먼저 호출하고, `--split test`이면 추가로 `guard_test_targets(root, config=…, role="official" if --run N2 else "reference")`를 호출한다. 가드 코드는 `main`의 `safe` 목록에 더한다(`hypotheses_test_targets_not_committed`, `official_config_mismatch`, `redteam_registration_not_committed`, `guardrail_v2_switch_mismatch`, `status_counts_not_fresh`, `invalid_config`, `invalid_arguments`, `partial_test_refused`). `guard_live_m3`는 수정하지 않는다.
- `--m6 --split dev`는 기존 `partial_dev_refused` 규칙(20건 전체)을 그대로 쓴다.

### 2. Makefile

`test-outbox-pg` 앞에 타깃 두 개(주석: 사용자 실행):

- `eval-run-dev-m6`: `uv run --env-file .env python -m govops.evalrun.dev_m3 --m6 --split dev --config $(CONFIG) --run $(RUN) --no-record-golden`
- `eval-run-test`: `uv run --env-file .env python -m govops.evalrun.dev_m3 --m6 --split test --config $(CONFIG) --run $(RUN) --repeat-index $(REPEAT) --no-record-golden`

기존 `eval-run-dev` 타깃은 바꾸지 않는다.

### 3. 사람 채점 안내 `docs/HUMAN_GRADING_GUIDE.md`(신규, 비전문가가 읽는 문서)

다음을 모두 담는다. 용어는 풀어 쓰고 약어에 설명을 붙인다.

1. **무엇을 하는가**: 공식 test 첫 번째 반복의 응답 중 30개를 사람이 0~3점으로 채점해 Gemini 채점기의 일치도(κ)를 확인한다. 채점은 응답을 만든 구성(v1 또는 v1+v2)을 모르는 상태에서 한다.
2. **채점표 만드는 명령**: `make eval-human-sheet OBS=data/results/test/<결과 디렉터리>/observations.jsonl`(공식 test **1회차** 결과). 출력 CSV 위치·열 목록(`sample_id, task_id, type, request, deliverable, correctness, groundedness, format, note`)과 각 열의 의미. `request`·`deliverable`은 읽기만 하고 고치지 않는다.
3. **칸별 기준**: `correctness`(필수 사실과 일치하는가)·`groundedness`(근거 없는 사실을 만들지 않았는가, 문서 ID 인용 형식)·`format`(요청한 형태·한국어·길이)에 대해 0·1·2·3의 의미(0 충족하지 못함 또는 중대한 오류, 1 일부 충족·중요한 결함, 2 대체로 충족·경미한 결함, 3 모두 충족·결함 없음)와 `no_permission` 유형은 "찾을 수 없다고 답했거나 읽을 수 있는 문서로 확인되는 내용만 답했는가"를 `correctness`·`groundedness` 기준으로 쓴다는 설명(`eval/grading.py`의 `criteria_for`·`build_prompt` 척도와 같아야 한다).
4. **예시 1개**: 합성 요청 한 줄·합성 응답 한 줄·세 칸 점수와 이유(문서에 직접 쓴 합성 예시이며 실제 평가 문항을 옮기지 않는다).
5. **기록 방법**: CSV(UTF-8)의 점수 세 칸에만 정수 0~3을 적고 `note`는 선택. 다른 열·열 이름·행 순서를 바꾸지 않는다. 저장 후 `make eval-human-register CSV=<경로>`로 해시를 등록하고 등록 파일(`docs/results/human-grading-registration.json`)을 커밋한다. 커밋을 확인한 뒤에만 `make eval-gemini-grade OBS=<경로>`를 실행한다.
6. **주의**: Gemini 점수를 보기 전에 채점을 끝낸다. 채점 중 응답을 만든 구성(v1/v1+v2)을 찾아보지 않는다. 판단이 어려우면 `note`에 이유를 적는다.

### 테스트

- **`tests/test_evalrun_dev_m3_m6.py`**(가짜 spec·가짜 서비스, 기존 `tests/test_evalrun_dev_m3.py` 구성 방식): ① `select_tasks` 기본 호출이 변경 전과 같은 결과(기존 테스트가 수정 없이 통과). ② `split="test"`는 test 40만, 부분 선택은 `partial_test_refused`, dev 선택에 test가 섞이면 `test_task_refused`. ③ `allow_test` 없이 test spec이 들어오면 `test_task_refused`(기존 코드). ④ `allow_test=True`이면 결과 디렉터리가 `results/test`이고 골든 파일(`data/replay/golden/status.json`·`llm`·`a_run`)이 생성·수정되지 않는다(실행 전후 해시). ⑤ `record_golden=False`는 dev 골든 task에서도 녹화하지 않는다. ⑥ manifest: `--m6` 경로에서 `m6_run`·`v2_validity`·`validity`가 있고, v2 on·시도 0건이면 `validity == "invalid_v2_off"`이며 `run_role`은 변경 전과 같은 값(탐색 예산에 따른 `official`/`reference`)이다. `--m6`가 아닌 경로의 manifest 키 집합은 변경 전과 같다(`m6_run`·`validity`·`v2_validity` 없음). ⑦ `main`: `--m6` 없이 새 인자가 오면 `invalid_arguments`, `--m6 --split test`에서 등록 파일 없으면 `hypotheses_test_targets_not_committed`, `--run N2`에서 설정 불일치 `official_config_mismatch`, `--run N3`은 불일치여도 통과, 스위치 불일치 `guardrail_v2_switch_mismatch`, 카운터 비어 있지 않음 `status_counts_not_fresh`(가드 호출 순서와 `_live` 미호출을 확인). ⑧ **결선(FP-070)**: Makefile `eval-run-dev-m6`·`eval-run-test`가 `govops.evalrun.dev_m3`와 위 인자를 호출하고 `eval-run-dev`는 인자 없이 그대로다.
- **`tests/test_human_grading_guide.py`**: 안내 문서가 `COLUMNS`의 모든 열 이름, `DIMENSIONS` 세 이름, 0·1·2·3 척도 설명, `make eval-human-sheet`·`make eval-human-register`·`make eval-gemini-grade` 문자열을 포함하고, 이 명령 이름의 타깃이 Makefile에 실제로 있으며, `HUMAN_SAMPLE_ALLOCATION` 합계 30이 문서의 "30개"와 일치하고, 실제 평가 문항 요청 문장(`data/eval/tasks.json`을 읽지 않고도 확인 가능한 방식: 문서에 `T1-`·`T2-` 같은 task ID 패턴이 없음)이 옮겨져 있지 않음을 단언한다.

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

- `guard_live_m3`와 기존 `eval-run-dev` 타깃·인자 없는 `main` 경로를 바꾸지 마라. 이유: 기존 dev 기록·골든·테스트가 이 경로에 의존하며 `--m6` 없는 동작은 변경 전과 같아야 한다.
- test 항목에서 골든·`data/replay/`를 쓰지 마라. 이유: M6 설계 9절 "골든 재기록 없음", test 문항은 골든·재생에 넣지 않는다.
- 안내 문서에 실제 평가 문항·응답·`data/eval/` 내용을 옮기지 마라. 이유: test 문항은 열람 금지 경로이며 사람 채점 자료의 예시는 합성이어야 한다.
- 안내 문서가 Gemini 점수를 보여 주는 절차를 쓰지 마라. 이유: 사람 채점은 Gemini 점수를 보기 전에 끝낸다(EVAL.md 채점 순서).
- `make eval-run-test`·`make eval-human-sheet`를 실행하지 마라. 이유: 실제 서비스에 접속하거나 평가 문항을 여는 사용자 실행 항목이다.

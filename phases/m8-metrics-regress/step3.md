# Step 3: toolcall-cli

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계 표, 특히 `metrics/` 행), `/tests/test_architecture.py`
- `/docs/M8_DESIGN_DRAFT.md`(0-1절, 1-4절, 2-1절)
- `/docs/results/hypotheses-m8-toolcall.md`(정의·고정 코드 목록·입출력 규칙의 원본, 읽기만 한다)
- `/src/govops/metrics/toolcall.py`, `/tests/test_metrics_toolcall.py`(이전 step 산출물)
- `/src/govops/eval/observation.py`(`load_observations`), `/src/govops/eval/spec.py`(`TaskSpec`), `/src/govops/eval/run_summary.py`(`_protect_inputs`·`main`: 입력 보호·`--specs` 처리 방식 선례, 읽기만 한다)
- `/Makefile`, `/tests/test_makefile.py`(`test_m8_grounding_targets_that_take_dbname_load_the_env_file` 등 타깃별 `--env-file` 단언 방식)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/metrics/cli.py`, `src/govops/metrics/traces.py`, `tests/test_metrics_toolcall_cli.py`, `Makefile`, `tests/test_makefile.py`. TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/metrics/cli.py`, `src/govops/metrics/traces.py`, `tests/test_metrics_toolcall_cli.py`, `Makefile`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/m8-metrics/toolcall/`, 평가 명세를 읽는 `data/eval/tasks.json`(런타임 기본값))은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. trace의 `output.answer`·`tool.result` 속성 값도 같다. `src/govops/guardrails/rules_v1.json`은 내용을 열거나 출력하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 이전 step의 순수 함수를 실제 결과 디렉터리에 적용하는 읽기 전용 CLI와 trace 연결을 만든다. 모델·API 호출이 없다. 오프라인 타깃이라 `.env` 없이 돈다.

### 1. 실측 형태 (이 형태로 픽스처를 만든다. FP-076)

경로는 `data/results/<split>/<run_id>/`. 키·값 분포만 확인했고 본문은 보지 않았다.

- `observations.jsonl`: 행 키 20개(`answer`·`approvals`·`cited_doc_ids`·`effect_bodies`·`effects`·`fault_injected`·`observed_ids`·`policy_violations`·`run_index`·`status`·`stop_reason`·`task_id`·`tool_calls`·`tool_results`·`trace_id`). `stop_reason`은 null, `run_index`는 정수 0. 공식 N2 디렉터리는 행 40개(task 40개 고유).
- `grades.json`: `{task_id: {tcr: {task_id, run_index, passed, reasons, tool_coverage}, rules: {duplicate_count, fault_injected, leak_candidates, leak_confirmed, leak_pending, run_index, task_id, unapproved_effect_count}, used_auto_forbidden}}`.
- `manifest.json` 중 이 CLI가 쓰는 키: `trace_ids`(task_id → 32 hex), `repeat_index`(공식 N2 디렉터리에만 있고 값 1·2·3, N1a·N1b에는 **키가 없다**), `run_label`, `config`, `run_role`, `validity`. 나머지는 읽지 않는다.
- `traces/<trace_id>.jsonl`: 줄마다 스팬 JSON, 키 `attributes`·`duration_ms`·`end_ts`·`kind`·`name`·`parent_id`·`span_id`·`start_ts`·`status`·`trace_id`. `kind`가 `tool_call`인 스팬 두 종류: (a) 이름이 도구 이름(읽기 도구만)이고 `attributes`에 `success`(bool)·`error_code`(없으면 null)·`tool.result`, (b) 이름이 `tool_attempt`이고 `success`·`error_code` 없음. 쓰기 항목은 모델이 제출한 초안(실행 여부 무관)이라 (a)가 없다. 같은 trace에 `kind == "task"` 스팬이 1개 또는 2개(승인 대기 task)다.
- `error_code` 값은 실측에서 `null`과 `not_found` 두 가지뿐이었으나 **코드는 값 집합을 가정하지 않는다**(문자열이면 그대로 센다).
- 결과 디렉터리는 항상 `attempts/`·`grades.json`·`manifest.json`·`observations.jsonl`·`traces/`를 가진다. `attempts/<task>-<n>`의 `<n>`은 시도 번호이며 `run_index`가 아니다.

### 2. `src/govops/metrics/traces.py`

```python
@dataclass(frozen=True)
class ReadSpan:
    name: str
    success: bool
    error_code: str | None

def read_tool_spans(trace_path: Path) -> list[ReadSpan]: ...   # kind == "tool_call" 이고 name != "tool_attempt" 인 스팬만
def link_reads(obs_read_calls: Sequence[tuple[str, bool]], spans: Sequence[ReadSpan]) -> LinkResult: ...
```

- `link_reads`: 관측의 읽기 호출 `(name, ok)` 다중집합이 스팬의 `(name, success)` 다중집합과 **같을 때만** 연결이 성립하고, 연결되면 스팬의 `error_code`를 센다. 다르면 그 실행의 읽기 호출 전부를 `unlinked_read`로 센다(조용히 빼지 않는다). trace 파일이 없거나 읽을 수 없으면 `unlinked_trace_missing`. 쓰기 호출은 `unlinked_write`로 세며 결함이 아니다. 불량 JSON 줄이 있으면 그 trace를 `unlinked_trace_missing`으로 센다(예외로 중단하지 않는다).
- 스팬 속성 값에서 읽는 것은 `success`·`error_code`뿐이다. `tool.result`·`output.answer`는 읽지 않는다.

### 3. `src/govops/metrics/cli.py`

- 진입점: `python -m govops.metrics.cli toolcall --official DIR [DIR …] --reference DIR [DIR …] [--specs data/eval/tasks.json] [--out data/interim/m8-metrics/toolcall]`, `python -m govops.metrics.cli verify --report FILE`.
- `toolcall` 순서: ① 등록 확인 — `docs/results/hypotheses-m8-toolcall.md`가 HEAD에 있고 등록 커밋 뒤 변경이 없을 때만 진행, 아니면 `m8_toolcall_registration_not_committed`(`git` 호출은 `subprocess.run(..., timeout=10)`, 시간 초과도 같은 코드; `govops.evalrun`은 import할 수 없으니 cli 안의 작은 비공개 함수로 구현) ② 출력 경로가 어떤 입력 디렉터리·`--specs`와도 겹치면 `m8_toolcall_out_overlaps_input`, 이미 존재하면 `m8_toolcall_out_exists` ③ 입력 읽기(`load_observations`, `grades.json`, `manifest.json`; `--specs`는 `run_summary`처럼 JSON 목록을 `TaskSpec.model_validate`로 읽음). 파일 없음 `m8_toolcall_input_missing`, 파싱·검증 실패 `m8_toolcall_input_invalid`, 명세에 없는 task `m8_toolcall_unknown_task` ④ 실행 단위로 `evaluate_run` ⑤ `cross_check_tool_coverage` 불일치 시 `m8_toolcall_grade_mismatch` ⑥ trace 연결 ⑦ 요약 ⑧ 출력.
- `main(argv: list[str] | None = None, *, repo_root: Path | None = None) -> int`: `repo_root`는 등록 확인(git)에 쓰는 저장소 위치이며 테스트가 임시 git 저장소를 주입한다(기본은 이 파일 기준 저장소 루트).
- 모든 실패는 **고정 코드 한 줄**(`FAIL <code>`)과 종료 코드 1이다. 일반 `Exception`을 코드로 가리지 않는다: 예기치 못한 예외는 클래스 이름만 `FAIL unexpected <ClassName>`으로 출력한다(경로·메시지·값은 출력하지 않는다).
- **환경 변수**: 이 CLI는 환경 변수를 읽지 않는다. `os.environ`·`os.getenv`를 쓰지 않는다(소스 AST 단언).
- 출력 `report.json`: `scope: "m8"`, `label: "M8 추가 측정 — 공식 M6 수치 아님"`, `registration_commit`, 그룹별(`official`·`reference`) 요약과 디렉터리별 요약, 연결 카운터, 입력 sha256(`observations.jsonl`·`grades.json`·`manifest.json`, `traces/`의 (파일 이름, sha256) 목록의 sha256). `rows.jsonl`: 실행마다 `result_dir`·`task_id`·`run_index`·`required_groups`·`required_met`·`forbidden_calls`·읽기/쓰기 호출·ok 수·연결 상태. `report.md`: 사람이 읽는 표.
- **집계 일치**: `report.json`의 모든 요약 수치는 `rows.jsonl`에 쓴 **같은 `RunToolcall` 목록**에서 계산한다. 별도 입력을 다시 읽어 집계하지 않는다.
- `verify`: 기존 `report.json`의 입력 sha256을 현재 파일과 대조해 다르면 `m8_toolcall_hash_mismatch`.
- 쓰기는 임시 파일 뒤 이름 변경으로 한다. 출력 파일은 값이 같아도 덮어쓰지 않는다.

### 4. Makefile·`tests/test_makefile.py`

```make
.PHONY: m8-toolcall
# 오프라인 재집계, 읽기 전용, 사용자 실행(.env 불필요)
m8-toolcall:
	uv run python -m govops.metrics.cli toolcall $(TOOLCALL_ARGS)
```

- `verify` 레시피에 넣지 않는다. `.env`가 필요 없으므로 `--env-file`을 붙이지 않는다.
- `tests/test_makefile.py`(수정 허용: **타깃별 `--env-file` 계약 표 테스트 1개 추가에 한함. 기존 단언 약화 금지**): `{"m8-toolcall": False}`를 담은 표(`True` = 레시피에 `--env-file .env`가 있어야 함)로 각 타깃이 `.PHONY`에 있고, `verify` 레시피에 없고, `("--env-file .env" in recipe) is expected`임을 단언한다. 이후 step(R-2·R-3)이 이 표에 행을 더한다. 표 이름은 `M8_ENV_CONTRACT`.

### 5. 테스트 (`tests/test_metrics_toolcall_cli.py`, 임시 디렉터리·합성 입력만)

- **합성 결과 디렉터리 생성 도우미**가 위 실측 형태(관측 행 20개 키, 중첩 `grades`, `manifest.trace_ids`, `repeat_index` 유무, `tool_attempt` 스팬 포함 trace)를 재현한다. 공식 3개 디렉터리가 같은 40개 task·`run_index` 0을 갖는 케이스, `run_index`가 0이 아닌 케이스를 모두 만든다.
- 정상 흐름: `report.json`·`rows.jsonl`·`report.md` 생성, `scope`·`label` 포함, 입력 sha256 기록, 요약이 `rows.jsonl`에서 재계산한 값과 같다.
- 연결: 읽기 호출이 스팬과 일치하면 `error_code`가 센다, 어긋나면 `unlinked_read`, trace 파일 없음 `unlinked_trace_missing`, 쓰기만 있으면 `unlinked_write`, `tool_attempt` 스팬은 무시, 불량 JSON 줄.
- 중단 코드 각각(위 고정 목록 중 CLI가 내는 것 전부): 등록 미커밋(임시 git 저장소로 시뮬레이션), 입력 없음·불량, 명세에 없는 task, 중복 실행, `tool_coverage` 불일치, 분모 0, 출력 겹침·존재, 입력 변조 뒤 `verify`가 `m8_toolcall_hash_mismatch`.
- 환경 변수 계약: 빈 환경(`monkeypatch.delenv`)에서도 동작하고, 소스 AST에 `os.environ`·`os.getenv`가 없다.
- 출력(stdout·stderr·`report.json`·`rows.jsonl`·`report.md`) 어디에도 합성 `answer`·`effect_bodies`·`tool.result` 표식 문자열이 없다.
- 코드 이름 문자열이 모두 등록 문서의 고정 코드 목록에 있고 목록의 중단 코드가 모두 코드(또는 T-1)에 존재한다.
- **렌더 확인(FP-075)**: `report.md`의 표 행마다 열 수가 머리 행과 같고 빈 셀이 없다. 테스트가 합성 입력으로 만든 `report.md`를 stdout에 출력해 사람이 한 번 훑어볼 수 있게 하고(통과/실패 판정과 무관), 조언 세션이 phase 실행 뒤 실제 렌더를 본다.
- 실제 결과 디렉터리(`data/results/…`)를 열지 않는다.

### 6. 소비자 점검

`scripts/step_file_checks.py`의 `Makefile` 소비자 경고를 읽었다. `tests/test_evalrun_dev_m3_m6.py`·`tests/test_evalrun_redteam_run.py`는 **기존 타깃(`eval-run-dev-m6`, `redteam-run`, `run-verdict` 등)의 레시피 정확 일치**를 본다: 그 타깃을 건드리지 않는다. `tests/test_docs_consistency.py`는 타깃 존재를 `\n이름:\n`으로 본다. `tests/test_makefile.py`의 `test_verify_recipe_runs_all_checks_in_order`는 `verify` 레시피 불변을 요구한다. 새 `trace`·`cli` 파일은 `tests/test_eval_run_summary.py`의 `rglob("*")` 경고와 무관하다(임시 디렉터리). 위 파일들은 수정하지 않고 `make verify`로 통과만 확인한다.

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

- `make m8-toolcall`·`python -m govops.metrics.cli`를 실제 결과 디렉터리에 실행하지 마라. 이유: 등록 문서 커밋 뒤 사용자가 실행하는 항목이다. 이 step은 합성 입력으로만 검증한다(AGENTS.md).
- 출력 위치를 `data/results/` 아래로 두지 마라. 이유: 추적되지 않는 새 파일이 러너의 `dirty_worktree` 검사에 걸린다(FP-067).
- `tool_attempt` 스팬이나 도구 이름 스팬의 개수를 관측 호출 수와 직접 비교해 불일치로 판정하지 마라. 이유: 세는 대상이 다르다(쓰기 호출은 이름 스팬이 없고 `tool_attempt`는 시도 단위다, FP-045). 읽기 호출의 다중집합만 비교한다.
- 일반 `except Exception`으로 입력 오류를 하나의 코드로 뭉치지 마라. 이유: 원인 코드가 가려진다(FP-076 첫 live의 `FAIL input_or_execution_failure`). 원인마다 등록된 코드를 낸다.
- 환경 변수를 `os.environ[...]`로 읽지 마라(이 CLI는 아예 읽지 않는다). 이유: 오프라인 타깃은 `.env` 없이 돌아야 하고 `KeyError`가 고정 실패 코드를 가린 선례가 있다(FP-076, #193·#197).

## 자기 점검표 (작성자 기입. 문제 신호: 첫째 질문 "예", 둘째 질문 "아니오", 셋째 질문 "예")

- 수정 파일 목록 밖의 변경이 필요한가: 아니오. 설계 4-1절 T-2 행의 파일 목록과 같다.
- AC를 이 step 안에서 달성할 수 있는가: 예.
- 다른 step·등록 문서와 모순되는가: 아니오. 단 설계 초안 1-4절의 출력 위치 `data/results/m8/…`는 FP-067 때문에 `data/interim/m8-metrics/…`로 바꿨다(T-0 등록 문서와 일치).

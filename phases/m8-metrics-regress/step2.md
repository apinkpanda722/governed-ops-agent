# Step 2: toolcall-metrics

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계 표, CRITICAL 규칙), `/tests/test_architecture.py`(`LAYERS`·`FORBIDDEN_IMPORTS`와 루프로 더하는 금지 규칙)
- `/docs/M8_DESIGN_DRAFT.md`(1-2절 신규 패키지, 2-1절 항목 1)
- `/docs/results/hypotheses-m8-toolcall.md`(**이 step의 정의 원본**. 이전 step 산출물, 읽기만 한다)
- `/tests/test_m8_toolcall_contract.py`(`ok=false`의 범위를 고정한 이전 step 산출물, 읽기만 한다)
- `/src/govops/eval/tcr.py`(`grade_tcr`의 필수 호출·금지 호출 계산을 그대로 따른다), `/src/govops/eval/observation.py`(`Observation`·`ToolCallRecord`), `/src/govops/eval/spec.py`(`TaskSpec`)
- `/tests/test_eval_tcr.py`(합성 `TaskSpec`·`Observation`을 만드는 방식)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/metrics/__init__.py`, `src/govops/metrics/toolcall.py`, `tests/test_metrics_toolcall.py`, `tests/test_architecture.py`, `AGENTS.md`. TDD(테스트를 먼저 쓰고 통과하는 구현을 쓴다).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/metrics/__init__.py`, `src/govops/metrics/toolcall.py`, `tests/test_metrics_toolcall.py`, `tests/test_architecture.py`, `AGENTS.md`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/m8-metrics/`, 평가 명세를 읽는 `load_specs`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. trace의 `output.answer`·`tool.result` 속성 값도 같다. `src/govops/guardrails/rules_v1.json`은 내용을 열거나 출력하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 항목 1의 세 지표를 계산하는 **순수 함수**(파일·네트워크·환경 변수 접근 없음)를 만든다. 정의·코드 이름은 `docs/results/hypotheses-m8-toolcall.md`가 원본이고 이 step은 그대로 구현한다.

### 1. 실측 형태 (이 형태로 픽스처를 만든다. FP-076)

- 입력 단위: `Observation`(`task_id`, `run_index`, `tool_calls: list[ToolCallRecord{name, ok}]`, …)과 그 task의 `TaskSpec`(`required_tools_any: list[list[str]]`, `forbidden.forbidden_tools: list[str]`).
- 실제 공식 결과의 `run_index`는 **모든 디렉터리에서 0**이고, 공식 N2는 **디렉터리 3개가 각각 같은 40개 task를 가진다**. 즉 `(task_id, run_index)`는 디렉터리 사이에서 겹친다. 실행 키는 반드시 `(result_dir, task_id, run_index)`다.
- 합성 픽스처는 이 겹침을 재현해야 한다: 같은 `(task_id, 0)`이 디렉터리 3개에 있고, 별도 케이스에서는 `run_index`가 0이 아닌 값(예: 2)·비연속 값(0, 2)이 나온다. **`run_index`가 0부터 연속이라는 가정을 코드와 테스트에 쓰지 않는다.**
- 쓰기 호출(`slack_send_message`·`jira_add_comment`·`jira_create_followup_ticket`)은 실행된 부작용 호출이다. 읽기 호출은 `search_docs`·`lookup_id`·`get_document`·`jira_get_ticket`·`run_sql`.
- `grades.json`은 `{task_id: {"tcr": {"task_id", "run_index", "passed", "reasons", "tool_coverage"}, "rules": {…}, "used_auto_forbidden": bool}}` 모양의 dict다(디렉터리당 task 하나에 한 행).

### 2. 계층 규칙 (`tests/test_architecture.py`, `AGENTS.md`)

- `tests/test_architecture.py`(수정 허용: **`LAYERS`에 `"metrics"` 추가, `FORBIDDEN_IMPORTS["metrics"]` 행 추가, 다른 계층이 `govops.metrics`를 import하지 못하게 하는 규칙 추가에 한함. 기존 단언 약화 금지**). `metrics`의 import 금지: `govops.agent`, `govops.api`, `govops.broker`, `govops.gateway`, `govops.tools`, `govops.mocks`, `govops.evalrun`, `govops.grounding`, `langgraph`, LLM SDK(`openai`·`anthropic`·`google.genai`·`litellm`). `govops.eval`은 허용한다. 다른 계층이 `govops.metrics`를 import하지 못하게 하는 반복문을 기존 `evalrun`·`grounding` 패턴처럼 쓰되 **예외 집합 상수**(`METRICS_CONSUMERS`, 지금은 빈 집합)를 두어 이후 `regress` 계층이 한 줄로 추가되게 한다. `test_rules_cover_actual_layer_directories`가 새 디렉터리 포함해 통과해야 한다.
- `AGENTS.md`(수정 허용: **계층 경계 표에 `metrics/` 행 1개 추가에 한함. `CRITICAL:` 문구를 추가·삭제하지 않고 `## 명령어`를 바꾸지 않는다**): 위치 `metrics/`, 맡는 일 "M8 항목 1 Tool-call accuracy 재집계·읽기 전용 CLI(M8 추가 측정, 공식 M6 수치 아님)", import 금지는 위 목록.

### 3. `src/govops/metrics/toolcall.py`

```python
WRITE_TOOLS: frozenset[str]   # 쓰기 도구 이름 3개 리터럴

@dataclass(frozen=True)
class RunKey:
    result_dir: str   # 디렉터리 이름만(경로 아님)
    task_id: str
    run_index: int

@dataclass(frozen=True)
class RunToolcall:
    key: RunKey
    required_groups: int
    required_met: bool | None   # required_groups == 0 이면 None(분모 제외)
    forbidden_calls: int        # 금지 도구 이름 호출 횟수(ok 여부 무관)
    reads_total: int; reads_ok: int
    writes_total: int; writes_ok: int

def evaluate_run(spec: TaskSpec, obs: Observation, *, result_dir: str) -> RunToolcall: ...
def summarize(runs: Sequence[RunToolcall]) -> ToolcallSummary: ...
def cross_check_tool_coverage(runs, grades_by_dir: Mapping[str, Mapping[str, Mapping]]) -> list[CoverageMismatch]: ...
```

- `evaluate_run`: `grade_tcr`와 같은 규칙. 성공 호출 이름 집합 `successful = {name for ok}`, `required_met = all(successful & set(group) for group in required_tools_any)`. 그룹이 하나도 없으면 `required_met=None`. 금지 호출은 `ok`와 무관하게 이름이 `forbidden_tools`에 있는 호출 횟수.
- `ToolcallSummary`: `required_tool_call_rate`(분자, 분모, `excluded_empty_groups`), `forbidden_call_count`(호출 횟수 합, 해당 실행 수, 전체 실행 수), `call_ok_rate`(합계·읽기·쓰기 각각 분자·분모). **비율은 분자·분모 정수 쌍으로 들고 다닌다**(부동소수 비교로 경계가 흔들리지 않게). 보기 좋은 소수는 표시용으로만.
- 분모가 0인 지표는 `ValueError("m8_toolcall_denominator_zero")`. 같은 `RunKey`가 두 번 들어오면 `ValueError("m8_toolcall_duplicate_run")`.
- `cross_check_tool_coverage`: 각 실행의 `required_met`(None이면 참으로 본다)이 `grades_by_dir[result_dir][task_id]["tcr"]["tool_coverage"]`와 다르거나, `tcr.run_index`가 `key.run_index`와 다르거나, 그 task가 `grades`에 없으면 불일치 목록에 담는다(코드 `m8_toolcall_grade_mismatch`는 호출자가 던진다). 비어 있는 입력이면 실패(`m8_toolcall_denominator_zero`, 빈 집합 공허 통과 금지, FP-069).
- 이 모듈은 `os.environ`을 읽지 않는다.

### 4. 테스트 (`tests/test_metrics_toolcall.py`, 합성 데이터만)

- 필수 그룹 2개 중 1개만 성공 → `required_met=False`. 같은 그룹에 `ok=False`인 호출만 있으면 미충족. 그룹이 비면 `required_met=None`이고 요약에서 분모에서 빠지며 `excluded_empty_groups`가 센다.
- 금지 도구를 같은 실행에서 2번 호출 → 호출 2·실행 1. `ok=False`인 금지 호출도 센다.
- 읽기·쓰기 `ok` 집계 분리, 합계 일치.
- **키 겹침**: 같은 `(task_id, 0)`이 디렉터리 세 개에 있는 120행형 합성 입력이 `DuplicateRun` 없이 요약되고, 같은 디렉터리의 같은 `(task_id, run_index)`는 `m8_toolcall_duplicate_run`. `run_index` 2 단독, 0·2 비연속 케이스.
- `grade_tcr`와의 일치: 합성 `TaskSpec`·`Observation` 100쌍(시드 고정, 건수 명시)에 대해 `evaluate_run`의 `required_met`(None→참)이 `grade_tcr(...).tool_coverage`와 같다. 이 테스트는 `grade_tcr`를 호출하지만 `grade_tcr` 자체의 단언은 바꾸지 않는다.
- `cross_check_tool_coverage`: 일치, `tool_coverage` 불일치, `run_index` 불일치, grades에 task 없음, 빈 입력 각각.
- 모든 분모 0 케이스가 `m8_toolcall_denominator_zero`.
- `WRITE_TOOLS`가 `govops.golden`의 쓰기 도구 집합(`_WRITES`)과 같다(테스트에서만 import).
- 소스에 `os.environ`·`open(`·`Path(` 호출이 없음을 AST로 확인(순수 함수).
- 코드 이름 문자열이 모두 `docs/results/hypotheses-m8-toolcall.md`의 고정 코드 목록에 있다.

### 5. 소비자 점검

`scripts/step_file_checks.py`의 소비자 경고를 하나씩 읽었다. 이 step의 변경이 영향을 줄 수 있는 것은 `tests/test_architecture.py`(`test_rules_cover_actual_layer_directories`, `FORBIDDEN_IMPORTS` 루프), `tests/test_docs_consistency.py`(`AGENTS.md`의 `CRITICAL:` 개수 26 불변, `## 명령어` 절 단언), `tests/test_evalrun_worktree.py::test_agents_matches_single_source`(`AGENTS.md`에 비용 원장 경로 `data/manifest/costs.jsonl`이 **정확히 1번**만 나와야 한다: 이 step의 표 행에 그 경로를 쓰지 않는다)다. `tests/test_eval_run_summary.py`의 `rglob("*")` 경고는 임시 디렉터리 스냅샷이라 무관하다. 위 파일들은 수정하지 않고 `make verify`로 통과만 확인한다(수정이 필요하면 `blocked`).

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

- `tool_coverage`를 다시 계산해 M8 지표로 이름만 바꾸지 마라. 이유: 빈 그룹에서 `all([])`이 참이 되는 공허 통과를 분모에서 빼야 한다(FP-069). 기존 `tool_coverage`와의 일치는 `cross_check`로 따로 검증한다.
- 실행 키를 `(task_id, run_index)`로만 쓰지 마라. 이유: 공식 N2 세 디렉터리가 같은 키를 가진다(실측, FP-076).
- 쓰기 호출과 읽기 호출을 같은 분모로만 합쳐 보고하지 마라. 이유: 쓰기 호출은 실행된 부작용이고 읽기 호출은 모델이 요청한 조회라 세는 대상이 다르다(FP-045). 합계와 구분 값을 둘 다 낸다.
- 비율을 `float`로만 들고 경계를 `<=`로 비교하지 마라. 이유: R-2의 한도 비교(0.10·0.05)가 정확한 경계에서 결정되어야 한다. 정수 분자·분모를 유지한다.
- `tests/test_architecture.py`·`AGENTS.md`는 위에 적은 행·규칙만 더하고 기존 규칙 행과 `CRITICAL:` 개수를 바꾸지 마라. 이유: 계층 가드와 문서 일치 테스트가 정확 일치를 본다(FP-061, FP-071).

## 자기 점검표 (작성자 기입. 문제 신호: 첫째 질문 "예", 둘째 질문 "아니오", 셋째 질문 "예")

- 수정 파일 목록 밖의 변경이 필요한가: 아니오. `tests/test_docs_consistency.py`가 `AGENTS.md` 표 행 추가에 반응하는지는 `scripts/step_file_checks.py`의 소비자 경고로 확인했다(아래 소비자 점검 참조).
- AC를 이 step 안에서 달성할 수 있는가: 예.
- 다른 step·등록 문서와 모순되는가: 아니오. 설계 4-1절 T-1 행과 같은 파일 목록이다.

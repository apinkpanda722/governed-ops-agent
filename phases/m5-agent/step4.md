# Step 4: dev-budget-record

## 읽어야 할 파일

- `/AGENTS.md`(평가·사전 등록 규칙), `/docs/ADR.md`(ADR-014 변경 기록), `/docs/EVAL.md`(탐색 예산 절)
- `/docs/results/hypotheses-m5-budget.md`, `/docs/results/m5-dev4-supersedes.json`, `/docs/results/n-registration-m5.md`(등록 문서: 읽기만 한다), `/docs/results/m4-dev3-supersedes.json`
- 이전 step 산출물: `/src/govops/agent/exploration.py`(step 1: `exploration_budget_from_env`, `STOP_REASON`), `/src/govops/agent/graph.py`(`stop_reason` 상태 키)
- `/src/govops/evalrun/dev_m3.py`(`FIRST/PROMPT/GUARDRAIL_REGISTRATION_FILES`, `guard_live_m3`, `_registration_metadata`, `git_metadata_m3`, 매니페스트를 만드는 `run_dev_m3`, CLI의 `safe` 오류 코드 집합, `trace_mismatch` 호출 근처의 `state`), `/src/govops/evalrun/observe.py`(`build_observation_m3`), `/src/govops/eval/observation.py`(`Observation`), `/src/govops/evalrun/worktree.py`(`dirty_entries`)
- `/tests/test_evalrun_dev_m3.py`(`metadata()`, 매니페스트 단언, 임시 저장소로 등록 번들을 만드는 두 테스트), `/tests/test_guardrail_registration.py`(`guard_live_m3` 단언), `/tests/test_evalrun_observe.py`(`build_observation_m3` 시그니처 단언), `/tests/test_eval_observation.py`, `/tests/test_git_status_sites.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-043·044·060·061·064) — 읽기만 한다
- **외부 계약 실측**(저장소 코드·원자료, 2026-10-08):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| `git_metadata_m3`가 `supersedes`·`previous_supersedes`를 채우는 방식 | 동작 | `supersedes` = `GUARDRAIL_REGISTRATION_FILES[1]` JSON, `previous_supersedes` = `PROMPT_REGISTRATION_FILES[1]` JSON. 번들은 `(가설 문서, supersedes JSON)`이고 `_registration_metadata`는 `files[0]`의 마지막 커밋과 모든 파일의 커밋·변경 여부를 본다 |
| `build_observation_m3`의 입력 `view` | 동작 | API task view다. **`stop_reason`을 담지 않는다**(화면 비노출). 러너는 같은 지점의 그래프 상태(`state`)에서 읽어야 한다 |
| 기존 관측 파일 | 동작 | `stop_reason` 열이 없다. `Observation`의 새 필드는 기본값 `None`이어야 기존 `observations.jsonl`이 계속 읽힌다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/eval/observation.py`, `src/govops/evalrun/observe.py`, `src/govops/evalrun/dev_m3.py`, `tests/test_evalrun_dev_m3.py`(등록 번들·가드·매니페스트 사례 추가와, `metadata()` 도우미 및 임시 저장소 번들 테스트의 새 번들 반영에 한함, 단언 약화 금지), `tests/test_guardrail_registration.py`(필요하면 새 가드 키 반영에 한함, 단언 약화 금지), `tests/test_evalrun_observe.py`(`stop_reason` 사례 추가에 한함, 기존 시그니처 단언 유지), `tests/test_eval_observation.py`(`stop_reason` 사례 추가에 한함, 기존 단언 유지). 계층: `evalrun/`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import할 수 없다. `eval/`은 `gateway`·`broker`를 import할 수 없다. 이 step은 두 계층에 걸치지만 `stop_reason` 필드 하나를 관측 데이터 모델에 더하는 변경이다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/eval/observation.py`, `src/govops/evalrun/observe.py`, `src/govops/evalrun/dev_m3.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_guardrail_registration.py`, `tests/test_evalrun_observe.py`, `tests/test_eval_observation.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. `dev_m3.py`와 기존 테스트가 실행 시 `data/eval/tasks.json` 등을 프로그램으로 읽는 것(러너의 평가셋 로딩)은 대상이 아니다. 새 테스트는 두 경로의 실제 파일 내용을 읽지 않고 합성 데이터와 임시 저장소를 쓴다.

**배경**: 켬/끔 dev 실행(`hypotheses-m5-budget.md`)을 기록하는 러너 변경이다. 같은 커밋에서 끔 → 켬 순서로 실행하며 공식 실행은 켬이다. 끔은 환경 변수 `GOVOPS_EXPLORATION_BUDGET=0`으로 지정한다.

### 1. 관측 필드

- `Observation`에 `stop_reason: str | None = None`을 더한다(기본값으로 기존 `observations.jsonl`이 그대로 읽힌다, `extra="forbid"` 유지). `build_observation_m3`에 선택 인자 `stop_reason: str | None = None`을 더하고 그대로 넣는다. 기존 시그니처 단언(`tool_calls`·`tool_results`는 기본값 없음)은 유지한다.
- 러너는 task마다 그래프 상태(`state`)에서 `state.get("stop_reason")`를 읽어 `build_observation_m3(..., stop_reason=…)`에 넘긴다. API view에서 읽지 않는다.

### 2. 등록 번들·가드·매니페스트 (`dev_m3.py`)

- `BUDGET_REGISTRATION_FILES = ("docs/results/hypotheses-m5-budget.md", "docs/results/m5-dev4-supersedes.json", "docs/results/n-registration-m5.md")`.
- 메타데이터 접두어 `hypotheses_m5_budget`: `_registration_metadata`로 `_committed`·`_unchanged`·`_registration_commit`을 만들고 `_sha256`(가설 문서 해시)을 더한다. 번들의 세 파일은 모두 커밋되어 있고 등록 커밋 이후 변하지 않아야 한다.
- 가드: `guard_live_m3`에 `hypotheses_m5_budget_committed`·`_unchanged`가 아니면 `ValueError("hypotheses_m5_budget_not_committed")`를 던진다. **가드 순서는 기존 세 가드와 `guardrail_rules_not_registered` 뒤, `dirty_worktree` 앞**이다(기존 `test_guardrail_registration.py` 단언이 그대로 통과해야 한다). CLI의 `safe` 오류 코드 집합에도 이 코드를 더한다.
- `supersedes` = `BUDGET_REGISTRATION_FILES[1]` JSON, `previous_supersedes` = `GUARDRAIL_REGISTRATION_FILES[1]` JSON.
- 매니페스트 최상위에 `exploration_budget`(`exploration_budget_from_env()`의 값: 정수 또는 `null`), `run_role`(`exploration_budget`이 있으면 `"official"`, 없으면 `"reference"`), `stop_reasons`(`stop_reason`이 있는 task만 `{task_id: reason}`)를 기록한다.
- 오류 코드 허용 목록: `dev_m3.py`의 `_SMOKE_CODES`(스모크 출력에 찍을 수 있는 코드 집합)에 `max_cost`를 더한다. 빠지면 `max_cost`로 끝난 스모크가 `unknown`으로 찍힌다. 테스트는 `_smoke_code("max_cost") == "max_cost"`를 확인하고 기존 코드의 허용·`unknown` 처리는 그대로 둔다.
- 작업 트리 청결 검사는 기존 `dirty_entries` 경로를 그대로 쓴다(FP-064). 새 `git status` 호출을 만들지 않는다.

### 3. 테스트

- 관측: `stop_reason` 기본값 `None`, 값 직렬화·역직렬화, 기존 형식의 JSONL(열 없음)을 읽을 수 있음, 잘못된 타입 거부.
- 가드: m5 번들 미커밋·변경 시 `hypotheses_m5_budget_not_committed`. 기존 가드 순서 보존(m4 가드가 먼저).
- 임시 저장소 번들 테스트(`first`·`prompt`·`guardrail` 번들)에 `budget` 번들을 더하고 `supersedes == {"bundle": "budget"}`, `previous_supersedes == {"bundle": "guardrail"}`, `hypotheses_m5_budget_*` 키들, 번들 파일 하나가 빠지거나 커밋 뒤 바뀐 경우를 확인한다.
- 매니페스트: 환경 변수 미지정 → `exploration_budget == 4`, `run_role == "official"`. `GOVOPS_EXPLORATION_BUDGET=0` → `null`, `"reference"`. 가짜 그래프가 `stop_reason="exploration_exhausted"`로 끝낸 task는 관측 `stop_reason`과 `stop_reasons`에 나타나고 채점은 평소 규칙(`completed`와 고정 답변을 다른 task와 똑같이 `grade_tcr`로)이다.
- 기존 정확 일치 단언(`manifest["supersedes"] == metadata()["supersedes"]`, 번들 순회 `for label, prefix, files in [...]`의 개수·순서)은 새 번들을 반영하는 것만 바꾸고 같은 강도로 유지한다(FP-061).

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

- `docs/results/`의 등록 문서(`hypotheses-m5-budget.md`, `m5-dev4-supersedes.json`, `n-registration-m5.md`)를 고치지 마라. 이유: 등록 뒤 바꾸지 않는다(FP-043). 번들 파일은 읽기만 한다.
- `make eval-run-dev`·`make smoke-approval`·`make eval-faults`를 실행하지 마라. 이유: 실제 API 호출은 사용자가 실행한다. 테스트는 가짜 그래프·임시 저장소로만 한다.
- `build_observation_m3`의 `tool_calls`·`tool_results`에 기본값을 주지 마라. 이유: 추적 입력은 암묵적 기본값이 없어야 한다(`test_trace_inputs_have_no_implicit_defaults`).
- `git status --porcelain`을 새로 호출하지 마라. 이유: 작업 트리 전체 청결은 `worktree.dirty_entries` 한 곳이 맡는다(FP-064). 가드가 보는 대상이 특정 파일이면 경로를 한정한다.
- `faults.py`를 고치지 마라. 이유: `git_metadata_m3`를 공유하지만 이 step의 범위는 dev 러너다. 가드 추가가 faults 러너 테스트를 깨면 단언을 약화하지 말고 `blocked`로 보고한다.

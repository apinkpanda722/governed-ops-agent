# Step 3: dev-run-m3

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- 계층 규칙: `evalrun/`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import하지 않고 다른 계층은 `evalrun`을 import하지 않는다(`AGENTS.md` 계층 경계 표). 확인한 사실의 근거 파일: `src/govops/eval/run_grade.py`(`build_observation`은 상태가 `completed`·`failed`가 아니면 거부하고 `effect_bodies=[]`), `src/govops/eval/observation.py`(`Observation`·`ApprovalRecord`·`ObservedEffect`), `src/govops/mocks/jira.py`·`slack.py`(`report(bodies=)`), `src/govops/tools/markers.py`, `src/govops/api/tasks.py`(`_view`), `src/govops/api/app.py`(`ApprovalRequest`), `data/eval/tasks.json`(`approval_script`), `data/seed/users.json`.
- `/src/govops/evalrun/dev.py`(M2 러너 전체 구조: `guard_live`·`_live`·`run_dev`·`git_metadata`·`_Capture`·`infrastructure_failure`; **수정 금지**), `/src/govops/evalrun/explore.py`(`candidate`), `/src/govops/api/wiring.py`(운영 조립 방식: 러너는 주입이 안 되어 자체 조립), `/src/govops/golden.py`, `/src/govops/replay/llm.py`(`RecordingChatClient`·`prompt_hash`), `/src/govops/replay/a_through.py`(`RecordingToolExecutor`), `/src/govops/gateway/client.py`(`LiteLLMChatClient`, `ChatResult`의 `model`·`deployment_id`·`unmapped_model`·`fallback_used`), `/src/govops/costs.py`(`CostLedger`, `cost_report`), `/src/govops/agent/graph.py`(`WITH_WRITES`·`READ_ONLY`), `/src/govops/eval/duplicates.py`(`count_effects`), `/src/govops/eval/run_grade.py`(`grade_run`), `/Makefile`(`eval-run-dev`), `/tests/test_evalrun_dev.py`, `/docs/results/2026-10-05-m2-dev-live.md`(M2 결과 문서 형식), 이전 step 산출물 `/src/govops/evalrun/observe.py`·`mock_admin.py`·`driver.py`
- 이전 dev 7개 결과: `/data/results/dev/8c18df863b2949f58d9c3550fec3b8cf/`(보존, 수정 금지)
- 관측 입력 근거: `/src/govops/evalrun/dev.py`의 `_Capture`(도구 호출 기록 `results`: `name`·`ok`·`result`=`_structure(payload)`)와 `run_dev`의 `tool_calls=[{"name", "ok"}…]`·`tool_results=captured_tools.results` 구성, `/src/govops/evalrun/explore.py`(`longest_streak`·`candidate`가 `row["tool_results"]`를 읽는다), `/src/govops/eval/tcr.py`(`forbidden_tool_called`는 `obs.tool_calls`로 판정)

## 작업

dev 20개를 **`WITH_WRITES` 단일 프로필로 처음부터** 한 번씩 live 실행하는 새 러너와 `preflight`를 만든다. M2 러너(`evalrun/dev.py`)와 그 테스트는 바꾸지 않는다. TDD로 테스트를 먼저 쓴다.

### 0. 관측 입력(`tool_calls`·`tool_results`) 보강 — 빠뜨리면 N과 금지 도구 판정이 조용히 통과한다

이전 step의 `build_observation_m3`는 `tool_results`를 채우지 않고(기본값 `[]`) `tool_calls`의 출처도 정해져 있지 않다. `tool_results`는 탐색 예산 N 계산의 입력이고(`explore.candidate`), 비어 있으면 모든 task의 연속 무정보 호출이 0이 되어 **N이 조용히 1로 등록된다**. `tool_calls`는 TCR의 `forbidden_tool_called` 판정 입력이다. 아래처럼 고친다(테스트 먼저).

1. **공용 추출 함수**: `src/govops/evalrun/tool_trace.py`에 `extract_tool_trace(captured: Sequence[Mapping]) -> tuple[list[dict], list[dict]]`를 만든다. 입력은 M2 러너 `_Capture.results`와 같은 기록(`{"name", "ok", "result": <구조 ID만 남긴 값>}`)이고, 출력은 `tool_calls=[{"name", "ok"}…]`와 `tool_results`(M2의 `captured_tools.results`와 같은 내용: 구조 ID(`doc_id`·`key`·`ticket_id`)만 남기고 본문·토큰은 없음)다. 구조 ID만 남기는 `_structure` 변환도 이 모듈로 옮겨 **M2 러너(`evalrun/dev.py`)와 새 러너가 같은 함수를 쓴다**. M2 러너는 이 공용 함수를 import해서 쓰도록만 바꾸고 **동작·출력·기존 테스트는 그대로**여야 한다(`tests/test_evalrun_dev.py` 수정 없이 통과).
2. **수집 방식**: 새 러너는 M2와 같이 그래프에 넘기는 도구 실행기를 캡처 래퍼(`_Capture` 또는 같은 역할의 공용 래퍼)로 감싸 호출 기록을 모은다(task가 끝난 뒤 `extract_tool_trace`로 변환). 이 기록은 in-process `TaskService`가 실행한 그래프의 실제 도구 호출이다. **교차 확인**: task가 끝난 뒤 `TaskService`의 checkpoint 상태(`get_task`)의 `messages`에서 센 도구 호출 수와 캡처 기록 수가 다르면 manifest에 `tool_trace_mismatch`로 task별 표시한다(본문 비교 없이 개수만).
3. **`build_observation_m3`의 `tool_calls`·`tool_results`를 필수 인자로 바꾼다**(기본값 없음, 빈 목록을 넘기는 것은 호출자의 명시적 선택). 기본값에 의존하던 호출·테스트는 모두 명시적으로 넘기도록 고친다.
4. 러너는 관측을 만들 때 항상 위 추출 결과를 넘긴다. 읽기 전용 task도 `tool_results`가 채워진다.

테스트(먼저 쓴다):
- 도구를 호출한 task의 관측에서 `tool_calls`·`tool_results`가 비어 있지 않다(구조 ID가 있는 결과와 없는 결과 모두).
- **가짜 환경 dev 실행 결과로 `explore.candidate`의 N 후보가 실제 호출 기록에서 계산된다**: 가짜 chat이 새 정보 없는 도구 호출을 이어서 내는 task를 포함해 연속 무정보 호출 최댓값이 0보다 큰 task가 있어야 하고, **모든 task가 0이면 테스트가 실패한다**(빈 입력이 조용히 통과하는 것을 막는다).
- 금지 도구를 호출한 가짜 task는 `forbidden_tool_called`로 TCR이 실패한다.
- `extract_tool_trace`: 본문·토큰이 결과에 없고 구조 ID만 남는다. M2와 같은 입력에 같은 출력(회귀).
- 캡처 기록 수와 checkpoint 도구 호출 수가 다르면 `tool_trace_mismatch`가 표시된다.

### 1. preflight (`src/govops/evalrun/preflight.py`)

실행 전에 환경이 낡지 않았는지 확인한다. 하나라도 실패하면 이유 코드를 출력하고 **시작하지 않는다**(종료 코드 1). 외부 명령·HTTP는 주입한 실행기로 한다(테스트는 가짜).

```python
def run_preflight(*, root, runner, http, env, mock_admin, a_check) -> list[str]   # 실패 이유 코드 목록(비어 있으면 통과)
```

- (a) **mock 이미지가 낡았는가**: `docker image inspect --format '{{.Created}}' <mock-jira·mock-slack 이미지>`의 생성 시각이 `git log -1 --format=%cI -- src/govops/mocks/`(`src/govops/mocks/`를 마지막으로 바꾼 커밋 시각)보다 **뒤**여야 한다. 아니면 `stale_mock_image`. 이미지 이름은 `infra/docker-compose.yml`(`mock-jira`·`mock-slack` build)에서 결정한다(컴포즈 프로젝트 이미지 이름 규칙은 코드에서 확인하고 summary에 적는다).
- (b) **LiteLLM에 배포 4개가 올라와 있는가**: 실제 응답으로 확인한다. LiteLLM 공식 문서에서 `/model/info`(필요하면 `/v1/model/info`)의 응답 형태와 인증을 확인해 `model_info.id`가 `openai-gpt-6-luna`·`openai-gpt-6-luna-only`·`openai-gpt-6-luna-down`·`anthropic-claude-sonnet-5-5` 4개 모두 있는지 본다(`config.yaml`의 `model_info.id`와 같은 집합, 코드 상수는 `costs.py`의 배포 매핑을 재사용). 하나라도 없으면 `litellm_config_stale`. 에이전트 가상 키로 조회가 안 되면 그 사실을 summary에 보고하고(blocked로 멈추지 말고) 인증 실패는 `litellm_config_stale`이 아니라 `service_down:litellm`으로 구분한다.
- (c) **서비스 응답**: `service_down:<이름>` — 이름은 `postgres`(`GOVOPS_DATABASE_URL`로 `SELECT 1`), `litellm`(`/health/liveliness` 또는 같은 의미의 가벼운 엔드포인트), `mock-jira`·`mock-slack`(`mock_admin.health()`), `a-mcp`(`tools.a_mcp.check_contract` 같은 계약 확인 호출, 사용자 1명으로).
- 환경 변수 읽기는 `govops.env.env_or`를 쓴다(FP-030).

### 2-b. 조립 함수 (`src/govops/api/wiring.py`에 추가) — 계층 경계를 지키는 주입 지점

`evalrun`은 `audit`·`guardrails`를 import할 수 없으므로 `AuditLog`·팀 정책·`ScopeIndex`·사용자 목록의 조립은 `api/wiring.py`가 맡는다(`api`는 이들을 import할 수 있다). **`open_task_service`의 동작을 바꾸지 않고** 그 조립 부분을 아래 함수로 뽑아 `open_task_service`가 이 함수를 쓰게 한다.

```python
def build_task_service(*, chat, tools: ToolRuntime, saver, outbox: OutboxStore, profile: AgentProfile, audit_dir: Path | None = None, clock=None) -> TaskService
```

- `chat`·`tools`·`saver`·`outbox`·`profile`은 **호출자가 주입**한다. 함수는 환경 변수(`GOVOPS_AGENT_PROFILE`·`LITELLM_*` 등)를 읽지 않고 프로필 기본값을 두지 않는다(러너가 `WITH_WRITES`를 스스로 넘긴다). 함수가 내부에서 `EffectDeps(tools.jira, tools.slack, outbox)`, `AuditLog(audit_dir)`(기본 위치 `data/interim/audit/`), `ScopeIndex.load()`, `load_team_policy("data/policy/team_policy.json")`, `load_users()`로 조립하고 `ToolRuntime`의 `policy`(`tools.policy`, 읽기와 쓰기가 공유하는 `ToolPolicy`)를 `TaskService(policy=…)`에 넘긴다. `startup_cleanup()`은 호출하지 않는다(호출자 선택).
- `open_task_service`는 이 함수를 호출하도록 리팩터링한다. **`tests/test_api_wiring.py`가 수정 없이 통과**해야 하고 시작 보호(`GOVOPS_MODEL_FAULT_MODE`)·프로필 이름 검증·`startup_cleanup` 호출 순서는 그대로다.
- 테스트: 주입한 가짜 `chat`·`tools`·InMemory 저장소로 `TaskService`가 만들어지고, 프로필이 주입한 값 그대로이며(`WITH_WRITES`·`READ_ONLY` 각각), 환경 변수를 바꿔도 결과가 변하지 않는다. `tests/test_architecture.py`가 `evalrun`의 `audit`·`guardrails` import를 계속 막는다(약화 금지).

### 2. 러너 (`src/govops/evalrun/dev_m3.py`, `python -m govops.evalrun.dev_m3`)

- **프로필 강제**: 러너는 `WITH_WRITES`를 **스스로** 만들어 쓴다. 그래프를 만든 뒤 프로필 이름이 `writes`(`WITH_WRITES`)가 아니면 `profile_mismatch`로 중단한다. 환경 변수(`GOVOPS_AGENT_PROFILE`) 기본값에 의존하지 않는다.
- **저장소**: `GOVOPS_DATABASE_URL`이 없으면 `postgres_required`로 중단한다(live dev는 Postgres checkpointer·outbox 필수, 메모리 저장소는 테스트 전용). 테스트는 러너의 저장소를 주입해 메모리로 돌린다.
- **가드**(`guard_live_m3`): 작업 트리 청결, `docs/results/hypotheses-m3.md`·`data/replay/golden/expectations.json`·`docs/results/m3-dev-supersedes.json` 세 파일이 추적·커밋되어 있고 **등록 커밋 이후 바뀌지 않음**(등록 커밋 = `git log -1 --format=%H -- docs/results/hypotheses-m3.md`, 검사는 `git diff <등록 커밋>..HEAD -- <세 파일>`이 비어 있음). 아니면 `hypotheses_m3_not_committed`. A git HEAD·계약 스냅샷 해시·인덱스 이름 기록(M2 `git_metadata`·`resolve_index_name` 재사용, 기준 `d2eec9c`가 아니면 경고만).
- **대상**: `split == "dev"` 20개 전부(`executable_in` ∈ {`m2`, `m3`}), test 문항은 거부(`test_task_refused`). 일부만 지정하는 `--tasks`는 `--smoke`가 아니면 허용하지 않는다(dev 1회는 20개 전체다).
- **task마다**: `preflight`는 시작 시 한 번. 그 다음 task마다 `mock_admin.reset()` + 시드 검사(M2 `mock_seed_check` 재사용), 요청자별 그래프·`TaskService`를 **`api.wiring.build_task_service`**(아래 2-b)로 만들고 `ApprovalDriver`로 실행한다. **러너는 `audit`·`guardrails`를 직접 import하지 않는다**(계층 경계: `evalrun`은 `audit`·`guardrails`·`broker`·`mocks`·LLM SDK import 금지). 인프라 실패(M2 `infrastructure_failure` 기준)는 mock 초기화 후 **1회만** 재실행하고, 두 번째도 실패면 실패로 센다(모든 시도 기록 보존, 분모에서 task는 한 번). 예산 중단(`BudgetExceededError`)은 재실행 없이 전체 중단한다.
- **관측·채점**: `build_observation_m3` → `grade_run`(`forbidden_auto` 연결 포함) → **절대 규칙**: `count_effects`로 중복·누락·미승인·마커 없음 건수를 task별로 기록하고 중복 건수 합이 0이 아니거나 미승인 건수가 0이 아니면 manifest에 `absolute_rule_violations`로 표시한다. 중복 0만 보고 합격시키지 않고 누락·최종 상태·`tcr`을 함께 낸다.
- **record-through는 골든 task만**(`golden.GOLDEN_TASKS`, 다음 step에서 14개로 확장되어도 이 상수를 읽는다): `RecordingChatClient`·`RecordingToolExecutor`로 LLM 응답과 A 응답을 기록한다. 프롬프트 해시에는 `WITH_WRITES`의 시스템 프롬프트·도구 정의가 들어간다(`replay.llm.prompt_hash`). 나머지 task는 기록하지 않는다. `data/replay/golden/status.json`에는 기록한 task의 `profile: "writes"`를 함께 쓴다(이 필드의 의미는 다음 step이 확정한다).
- **산출**: `data/results/dev/<run_id>/`에 `manifest.json`·`grades.json`·`observations.jsonl`·`traces/`·`attempts/`(M2와 같은 구조). manifest에는 M2 항목 외에 `profile`("writes"), `hypotheses_m3_sha256`, `hypotheses_m3_registration_commit`, `models_seen`(**호출마다 `ChatResult.model`**), `unmapped_models`, `fallback_used`, `costs_by_purpose`, **`mixed_model_tasks`**(task별 `models_seen`이 둘 이상인 task의 목록과 수)와 **`mixed_model_tcr`**(그 task들의 TCR 통과 수/전체, 없으면 0/0; 장애 주입 결과와 따로 보고하는 값이다), `absolute_rule_violations`, 그리고 **`supersedes`**(`docs/results/m3-dev-supersedes.json`의 내용 그대로: 사유, 이전 run_id, 이전 결과 파일 sha256, 이전 커밋). 마지막에 M2 `explore.candidate`로 N 후보를 출력한다("잠정치이며 등록값이 아님"). 
- 출력(콘솔)은 task별 TCR·누락·중복·미승인과 요약뿐이다. 본문·키·URL을 출력하지 않는다.
- **`--smoke`** 옵션(다음 step들이 쓰는 확장 지점): 합성 요청 목록을 받아 기록·manifest·가드(`hypotheses`) 없이 돌린다. 이 step에서는 옵션과 `run_dev_m3(specs, ...)` 함수 경계만 만들고 smoke 요청 자체는 다음 step이 정한다.
- `Makefile`: `eval-run-dev`가 `python -m govops.evalrun.dev_m3`를 부르도록 바꾼다(`uv run --env-file .env`). 주석: "live A·LiteLLM·mock·Postgres·`hypotheses-m3.md` 커밋 필요, dev 20 `WITH_WRITES`, 사용자 실행". `tests/test_makefile.py`를 이에 맞게 갱신한다(기존 M2 타깃 의미 변경이므로 테스트 기대를 고치되 약화하지 않는다).

### 테스트 (가짜 chat·in-process mock·메모리 저장소·가짜 실행기, 키·네트워크 불필요)

- 20개 task 전 경로(가짜 chat이 task별 스크립트로 도구·부작용을 내게 한다): 부작용 task의 효과·승인 기록·TCR, 읽기 task, 차단·거부 task, 전사 공지 승인자, 인프라 실패 1회 재실행과 두 번째 실패, 예산 중단, `profile_mismatch`, `postgres_required`, test 문항 거부, 가드(미커밋·등록 이후 변경·청결하지 않음), **preflight 이유 코드 3종**(`stale_mock_image`, `litellm_config_stale`, `service_down:<이름>`: 이미지 시각이 커밋 시각보다 앞선 경우, 4개 중 하나가 없는 `/model/info` 응답, 서비스별 실패)과 통과 경로.
- manifest: `supersedes` 포함, `models_seen`이 제공자 모델 ID, **모델이 섞인 task의 수와 그 TCR**(가짜 chat이 한 task 안에서 두 모델을 응답하게 해서 검증), `unmapped_models`.
- 절대 규칙: 중복 1건·미승인 1건을 주입한 관측이 `absolute_rule_violations`에 표시된다. 누락도 보고된다.

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
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- M2 러너(`evalrun/dev.py`)와 그 테스트를 수정하지 마라. 이유: M2 결과 재현과 기존 테스트가 의존한다.
- 이전 dev 7개 결과 디렉토리(`data/results/dev/8c18df86…`)를 수정·삭제하지 마라. 이유: 사유·sha256·커밋과 함께 보존한다(`hypotheses.md` §10).
- 환경 변수 기본값으로 `WITH_WRITES`를 선택하게 하지 마라. 이유: 프로필이 기본값으로 바뀌어도 러너가 단일 프로필을 스스로 강제해야 한다.
- 환경 변수를 `os.environ.get(이름, 비어 있지 않은 기본값)`으로 읽지 마라(FP-030). 이유: 빈 값이 기본값을 덮는다(`env_or`).
- preflight를 건너뛰는 옵션을 만들지 마라. 이유: 낡은 mock 이미지·LiteLLM 설정으로 실행하면 결과가 가짜가 된다(옛 환경 실행). 실패 이유 코드만 출력하고 시작하지 않는다.
- 관측 필드(`tool_calls`·`tool_results` 등)를 기본값 빈 목록으로 두거나 비어 있는 채로 관측을 만들지 마라(FP-030·FP-021 계열). 이유: 빈 입력이 탐색 예산 N(연속 무정보 호출이 0이 되어 N이 1로 등록됨)과 금지 도구 판정(`forbidden_tool_called`)을 조용히 통과시킨다. 가짜로 통과하는 입력은 "모든 task가 0이면 실패"하는 테스트로 막는다.
- M2 러너의 `_Capture`·`_structure`를 복사해 새로 만들지 마라. 이유: 추출 함수가 두 갈래가 되면 M2와 m3가 다른 N을 계산한다. 공용 함수로 옮기고 M2는 그것을 쓴다(M2 동작·테스트 불변).
- 중복 건수 0만으로 합격시키지 마라(FP-040). 누락·최종 상태·미승인 건수를 함께 낸다.
- 러너·에이전트 실행 경로가 `data/eval/`의 정답지를 읽게 하지 마라(FP-042). 채점은 `eval/`(`grade_run`)에서만 한다.
- 기대값·가설 파일이 등록 커밋 이후 바뀐 상태로 실행하지 마라(FP-043).
- 사용자 실행 산출물(비용 원장 등)이 작업 트리를 더럽히는 상황은 사용자가 별도 커밋으로 처리한다(FP-031). 러너는 작업 트리가 깨끗하지 않으면 시작하지 않는다.
- 도구 출력·mock 상태에 대한 판단은 원자료(`data/replay/a_run/`, mock `/state` 실제 출력)로만 한다(FP-022).
- `--smoke`를 평가 결과(`data/results/`)로 채택하지 마라. 이유: smoke는 기록·manifest를 남기지 않는다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

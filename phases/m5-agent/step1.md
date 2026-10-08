# Step 1: turn-limits

## 읽어야 할 파일

- `/AGENTS.md`(비용·탐색 예산 규칙), `/docs/ADR.md`(ADR-013·ADR-014의 2026-10-08 변경 기록, ADR-007), `/docs/ARCHITECTURE.md`(상태 전이 절의 MAX_STEPS·MAX_COST·탐색 예산 항목)
- `/docs/results/n-registration-m5.md`, `/docs/results/hypotheses-m5-budget.md`(등록 문서: 읽기만 한다. N=4, 소진 규칙 §3)
- 이전 step 산출물: `/src/govops/agent/turns.py`(step 0: `turn_defaults`, `TURN_KEYS`), `/tests/test_agent_turns.py`
- `/src/govops/agent/graph.py`(`MAX_STEPS`, `model_body`, `read_tools_body`, 라우팅), `/src/govops/gateway/client.py`(`ChatResult.cost_usd`), `/src/govops/env.py`(`env_or`)
- `/src/govops/evalrun/explore.py`(`identifiers`·`longest_streak`·`candidate`·`NOTICE`), `/src/govops/evalrun/tool_trace.py`(`structure`, `extract_tool_trace`), `/src/govops/evalrun/regrade.py`(`n_candidate`), `/src/govops/evalrun/dev_m3.py`(`_TaskTools.call`이 어떤 호출을 기록하는지)
- `/tests/test_agent_graph.py`(`test_max_steps`), `/tests/test_obs_instrument.py`(`test_failure_trace`), `/tests/test_evalrun_explore.py`, `/tests/test_evalrun_regrade.py`, `/tests/test_repo_rules.py`, `/tests/conftest.py`, `/tests/test_env_defaults.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-030·043·044·045·056·060·061) — 읽기만 한다
- **외부 계약 실측**(저장소 원자료, 2026-10-08):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| `regrade --n`이 세는 호출 집합(`n_candidate` → `extract_tool_trace(obs.tool_results)[1]`) | 동작 | **쓰기 도구를 뺀, `tools.call`까지 도달한 읽기 호출**이다. 호출이 예외로 끝나도(`ok=False`, 결과 `{}`) 행이 남고, 인자 검증 실패·`tool_not_available`로 `tools.call`에 도달하지 못한 호출은 행이 없다 |
| `identifiers(structure(payload))`와 `identifiers(payload)` | 동일 | `structure`는 `doc_id`·`key`·`ticket_id` 문자열과 dict·list만 남기므로 원본 payload에 `identifiers`를 써도 같은 집합이다 |
| 공식 dev 두 실행의 `n_candidate` | 동작 | `26c85539…`: T2-02=2·T5-05=2, `candidate=3`. `e3658977…`: T2-02=3, `candidate=4` (등록: `n-registration-m5.md`) |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/exploration.py`(신규), `src/govops/agent/graph.py`, `src/govops/evalrun/explore.py`(`identifiers`·`longest_streak` 정의를 `agent/exploration.py`로 옮기고 그곳에서 import해 다시 내보내는 변경에 한함, `candidate`·`NOTICE`·기존 동작 불변), `tests/test_agent_limits.py`(신규), `tests/test_agent_turns.py`(step 0 산출물: 탐색 예산이 기본으로 켜진 것을 반영하는 입력 갱신에 한함, 단언 약화 금지), `tests/test_agent_graph.py`(`test_max_steps`에 한함), `tests/test_obs_instrument.py`(`test_failure_trace`의 `max_steps` 사례에 한함), `tests/test_repo_rules.py`(비용 변수 금지 검사의 예외를 `GOVOPS_EXPLORATION_BUDGET` 하나로 정확히 고정하는 변경에 한함), `tests/conftest.py`(`GOVOPS_EXPLORATION_BUDGET`을 지우는 autouse 픽스처 추가에 한함), `.env.example`(`GOVOPS_EXPLORATION_BUDGET=` 한 줄과 주석). 계층: `agent/`는 `evalrun`을 import할 수 없고 `evalrun/`은 `agent/`를 import할 수 있다. 정의의 단일 출처는 `agent/exploration.py`다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/agent/exploration.py`, `src/govops/agent/graph.py`, `src/govops/evalrun/explore.py`, `tests/test_agent_limits.py`, `tests/test_agent_turns.py`, `tests/test_agent_graph.py`, `tests/test_obs_instrument.py`, `tests/test_repo_rules.py`, `tests/conftest.py`, `.env.example`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 기존 평가·골든 테스트가 실행 시 `data/eval/`을 프로그램으로 읽는 것은 대상이 아니다. 이 step의 코드(`agent/`)는 평가 데이터를 읽지 않는다. 새 테스트는 `data/results/dev/<실행>/observations.jsonl`·`manifest.json`·`grades.json`(커밋된 공식 기록)과 `docs/results/n-registration-m5.md`를 읽을 수 있고 `data/eval/`·`data/interim/`은 읽지 않는다.

**배경**: 턴 단위 한도 둘을 구현한다. (1) `MAX_COST_USD = 0.40`(턴 단위, 모델 호출 직전에 `turn_cost_usd >= MAX_COST_USD`이면 중단), (2) 탐색 예산 N=4(MAX_STEPS와 별도). 값과 근거는 ADR-013·ADR-014 변경 기록과 등록 문서에 있다.

### 1. `agent/exploration.py`

- `EXPLORATION_N = 4`, `EXPLORATION_ANSWER = "요청하신 정보를 찾을 수 없습니다."`, `STOP_REASON = "exploration_exhausted"`.
- `identifiers(value) -> set[str]`·`longest_streak(calls) -> int`: `evalrun/explore.py`의 현재 정의를 **그대로** 옮긴다(`doc_id`·`key`·`ticket_id` 문자열 키만, dict·list 재귀). 같은 정의가 두 곳에 있으면 안 된다(FP-044). `evalrun/explore.py`는 이 두 함수를 import해 다시 내보내고 `candidate`·`NOTICE`와 기존 import 경로(`from govops.evalrun.explore import longest_streak`)는 그대로 동작한다.
- `ExplorationCounter`: 상태 `seen: set[str]`, `streak: int`. `observe(payload) -> int`는 호출 하나를 센다: `ids = identifiers(payload)`, `ids - seen`이 비어 있지 않으면 `streak = 0`, 아니면 `streak += 1`, 그 뒤 `seen |= ids`. `from_state(state)`·`to_state()`는 `explored_ids`(정렬 리스트)와 `no_new_info_streak`로 왕복한다. `exhausted(n) -> bool`은 `streak >= n`.
- `exploration_budget_from_env() -> int | None`: `env_or("GOVOPS_EXPLORATION_BUDGET", "")`가 빈 값이면 `EXPLORATION_N`, `"0"`이면 `None`(끔), `"1"`이면 `EXPLORATION_N`, 그 밖은 `ValueError`. FP-030: `os.environ.get`에 비어 있지 않은 기본값을 넘기지 않는다.

### 2. `graph.py`

- `MAX_COST_USD = 0.40`. `build_graph(..., exploration_budget=<미지정 표식>)`: 인자를 주지 않으면 `exploration_budget_from_env()`를 쓰고, `None`이면 끔, 정수면 그 값이다(테스트가 직접 값을 줄 수 있다).
- `model_body`: `MAX_STEPS` 검사 바로 뒤에 `state.get("turn_cost_usd", 0.0) >= MAX_COST_USD`이면 모델을 호출하지 않고 `{"status": "failed", "error": "max_cost"}`로 끝낸다. 모델 호출이 성공한 경로의 반환 dict에 `"turn_cost_usd": 이전값 + result.cost_usd`를 더한다. 상한을 한 번 넘긴 호출의 결과는 그대로 쓴다(초과는 최대 한 호출). 승인 대기 시간은 호출이 없어 자동으로 제외된다.
- `read_tools_body`: 켬이면 호출마다 `ExplorationCounter.observe`로 센다. **센 대상은 `regrade --n`과 같은 집합**이다: `tools.call`까지 도달한 호출만 세고(결과 payload로 센다, `is_error` 결과도 센다), `tools.call`이 예외를 던지면 빈 payload로 센다. 인자 검증 실패(`invalid_argument`)와 `tool_not_available`은 세지 않는다. 한 모델 단계의 호출을 모두 처리한 뒤 `exhausted(N)`이면 상태에 `"status": "completed"`, `"answer": EXPLORATION_ANSWER`, `"cited_doc_ids": []`, `"stop_reason": STOP_REASON`을 더한다. 카운터 상태(`explored_ids`·`no_new_info_streak`)는 매번 반환 dict에 넣는다. `set_attributes(stop_reason=…)`를 span에 남긴다.
- 라우팅: `read_tools` 뒤 간선을 조건부로 바꾼다. `status == "running"`이면 `model`, 아니면 `END`. 쓰기 프로필에서도 소진 턴은 `finish`를 거치지 않고 `END`다. `stop_reason`은 API 응답 view에 넣지 않는다(이 step은 `api/`를 고치지 않는다).

### 3. 테스트 (`tests/test_agent_limits.py`)

- **재현(정의 단일 출처)**: 공식 dev 두 실행(`data/results/dev/26c8553905bb4396b20ef2b2ff6399f1`, `data/results/dev/e36589778c2243388f09d9b4d3b60f2b`)의 `observations.jsonl` `tool_results`를 `ExplorationCounter`에 호출 순서대로 넣어 task별 최대 연속이 `n_candidate(...)["inputs"]`와 모두 같고(예: 세 번째 실행 T2-02=3), 두 실행의 후보가 3과 4이며, 성공 task 전체의 최댓값 + 1이 `EXPLORATION_N`(4)과 같다. `identifiers(원본 payload) == identifiers(structure(payload))`를 합성 payload 몇 개로 확인한다(텍스트·숫자 필드가 섞인 payload 포함). 구조 필드가 없는 결과(예: `run_sql`의 ID 없는 열 값)는 무정보다.
- **등록 문서 대조**: `docs/results/n-registration-m5.md`에서 `**N = 4**`를 읽어 `EXPLORATION_N`과 같음을 확인하고, 문서에 적힌 두 실행의 `observations.jsonl` sha256이 실제 파일의 해시와 같음을 확인한다.
- **그래프**: ① 새 정보 없는 호출이 4번 연속이면 그 단계에서 끝나고 모델은 더 호출되지 않는다(`completed`, `answer == EXPLORATION_ANSWER`, `cited_doc_ids == []`, `stop_reason == "exploration_exhausted"`, `steps < MAX_STEPS`). ② 3번 연속 뒤 새 ID가 나오면 카운터가 0으로 돌아가 계속된다. ③ 한 단계에 여러 호출이 있어도 호출 단위로 센다. ④ 인자 검증 실패와 `tool_not_available`은 세지 않고 `tools.call`이 예외를 던지는 호출은 센다. ⑤ 쓰기 프로필에서 소진되면 `finish`를 거치지 않고 끝난다. ⑥ 끔(`exploration_budget=None`)이면 무정보 호출이 `MAX_STEPS`까지 이어지고 `failed`·`max_steps`다. ⑦ 카운터는 턴 범위라서 다음 턴 시작 상태(`build_turn_state`)에서 0이다.
- **MAX_COST**: `FakeChat`이 `cost_usd`를 돌려주도록 해서 ① 누적 비용이 정확히 상한과 같을 때 다음 호출 전에 `failed`/`max_cost`로 끝나고 모델 호출 수가 늘지 않음, ② 상한을 넘기는 마지막 호출의 응답은 쓰임(초과 한 호출 허용), ③ 승인 대기를 거쳐 재개해도 `turn_cost_usd`가 이어서 누적됨, ④ 상한 미만이면 영향 없음.
- **환경 플래그**: `exploration_budget_from_env`의 빈 값·`0`·`1`·잘못된 값. `tests/conftest.py`의 autouse 픽스처가 `GOVOPS_EXPLORATION_BUDGET`을 지워 개발자 환경이 테스트에 새지 않게 한다. 이 변수는 이름에 `BUDGET`이 들어 있어 `test_env_example_has_no_cost_limit_variables`에 걸린다. 그 테스트의 예외를 **`{"GOVOPS_EXPLORATION_BUDGET"}` 하나로 정확히 고정**한다(다른 이름은 계속 실패해야 하고, 예외 변수가 `.env.example`에 빈 값으로 있음을 단언한다). 이유: 탐색 예산은 비용 상한이 아니라 루프 차단 플래그다.
- **기존 테스트 수리(단언 약화 금지)**: `test_agent_graph.py::test_max_steps`와 `test_obs_instrument.py::test_failure_trace`의 `max_steps` 사례는 같은 ID나 빈 결과를 `MAX_STEPS`번 반복하는데, 탐색 예산이 켜져 있으면 N번째에서 먼저 끝난다. 호출마다 **payload에 서로 다른 `doc_id`**를 주도록 입력을 바꾼다(`ToolOutcome({"doc_id": f"syn-{i}"}, False, ())` 형태). `steps == MAX_STEPS`와 `error == "max_steps"` 등 기존 단언은 그대로 둔다. `exploration_budget=None`(끔)은 "끔 동작이 기존과 같음"을 보이는 새 테스트에만 쓴다.
- **단일 턴 불변(골든, 켬 상태)**: 골든 14 재생 테스트(`test_golden_m3.py`·`test_golden_trace.py`·`test_golden_harness.py`)가 수정 없이 통과해야 한다(기본값이 켬이다). `data/replay/golden/`의 파일 바이트가 바뀌지 않는다(AC의 `git diff`).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
git diff --exit-code -- data/replay/golden   # 탐색 예산 켬 상태에서도 골든 재기록 없음, status.json 불변
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

- 탐색 예산 카운터의 대상·정의를 `evalrun/explore.py`와 다르게 구현하지 마라. 이유: N=4는 그 정의로 산출한 등록값이다. 정의가 둘이면 같은 trace에서 다른 task가 잘린다(FP-044·045). 세는 대상은 `tools.call`에 도달한 읽기 호출이다.
- 소진을 `failed`로 끝내지 마라. 이유: 소진은 `completed`와 고정 답변이다(권한 판정 은닉, 등록 문서 §3). `stop_reason`을 API 응답 view에 넣지 마라(화면 비노출).
- `n-registration-m5.md`·`hypotheses-m5-budget.md`와 `docs/` 전체를 고치지 마라. 이유: 등록 뒤 바꾸지 않는다(FP-043).
- `MAX_COST_USD`·`EXPLORATION_N`을 환경 변수로 읽지 마라. 이유: 비용 상한은 코드 설정이다(ADR-007). 환경 변수는 켬/끔 플래그 `GOVOPS_EXPLORATION_BUDGET` 하나뿐이다.
- 기존 테스트의 단언을 약화하지 마라. 호출 입력을 바꾸는 것(새 `doc_id` 공급)은 허용하지만 기대값(`steps`·`error`·`status`)은 같은 강도로 둔다(FP-056·060).
- `evalrun/explore.py`에서 `candidate`·`NOTICE`나 출력 형식을 바꾸지 마라. 이유: `regrade --n`의 재현 출력이 등록 문서의 근거다.

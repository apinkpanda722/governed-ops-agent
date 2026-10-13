# Step 6: finalize-on-step-limit

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/agent/graph.py`(`MAX_STEPS`, `MAX_COST_USD`, `model_body`, `read_tools_body`, `route_model`, `AgentState`)
- `/src/govops/agent/turns.py`(`CONVERSATION_KEYS`, `TURN_KEYS`, `turn_defaults`, `build_turn_state`), `/src/govops/agent/prompts.py`(`SYSTEM_PROMPT`, `SEND_RULES`)
- `/src/govops/evalrun/regrade.py`(`n_candidate`, `regrade_run`), `/src/govops/evalrun/explore.py`(`candidate`, `longest_streak`), `/src/govops/eval/observation.py`(`stop_reason` 필드)
- `/src/govops/replay/llm.py`(`prompt_hash`, `request_key`: 8단계 이후에만 도는 새 호출은 기존 기록의 키를 바꾸지 않는다)
- `/tests/test_agent_limits.py`(`test_disabled_and_explicit_budget`, `test_cost_exact_limit_before_next_call`), `/tests/test_agent_graph.py`(`FakeChat`, `FakeTools`, 프로필 해시 단언), `/tests/test_agent_turns.py`(키 집합 단언), `/tests/test_evalrun_regrade.py`(`test_n_candidate_excludes_saved_mismatches`), `/tests/test_obs_instrument.py`(`test_failure_trace`)
- `/docs/results/hypotheses-m6-finalize.md`(안내문 sha256, 불변식, 사전 등록 — 읽기만 한다), `/docs/results/n-registration-m5.md`(읽기만 한다. 수정하지 않는다)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/graph.py`, `src/govops/agent/turns.py`, `src/govops/agent/prompts.py`, `src/govops/evalrun/regrade.py`, `tests/test_agent_finalize.py`, `tests/test_agent_limits.py`, `tests/test_agent_graph.py`, `tests/test_agent_turns.py`, `tests/test_evalrun_regrade.py`, `tests/test_obs_instrument.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/agent/graph.py`, `src/govops/agent/turns.py`, `src/govops/agent/prompts.py`, `src/govops/evalrun/regrade.py`, `tests/test_agent_finalize.py`, `tests/test_agent_limits.py`, `tests/test_agent_graph.py`, `tests/test_agent_turns.py`, `tests/test_evalrun_regrade.py`, `tests/test_obs_instrument.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`을 읽지 않는다. 회귀 테스트가 `data/results/dev/`·`data/eval/`·`data/seed/`를 읽는 것은 기존 `regrade` 테스트와 같고 이 금지의 대상이 아니다.

**배경**: 개방형 요청이 8단계 상한(`max_steps`)에서 `failed`로 끝나는 문제를 고친다(근거: `docs/results/2026-10-09-m5-dev5-live.md` "알려진 한계"). 8단계에 도달하고 이번 턴에 성공한 읽기 결과가 1건 이상이면, 모은 근거로 마지막 답변을 **한 번** 만들어 `completed`로 끝낸다. 그래프 동작만 바뀌며 시스템 프롬프트·도구 정의·모델·파라미터는 바뀌지 않는다(프롬프트 해시와 골든 14개 불변, dev 재실행 없음: `hypotheses-m6-finalize.md`).

이 이름들(`turn_read_ok`, `max_steps_finalized`, `FINALIZE_NOTICE`, `excluded_finalized_tasks`)의 기존 사용처는 없다. `grep`으로 확인했다.

### 1. 고정 안내문 (`agent/prompts.py`)

`FINALIZE_NOTICE`를 아래 문장 **그대로**(139자, UTF-8 sha256 `f12f48773bd515212c472c54ace59093d3563419488d5bcd97b96cadf6f1b831`) 상수로 둔다. 문구를 바꾸면 사전 등록과 어긋나므로 바꾸지 않는다.

```
조사 단계 한도에 도달했습니다. 더 이상 도구를 호출할 수 없습니다. 지금까지 조회한 결과만으로 요청에 대한 최종 답변을 작성하세요. 확인하지 못한 내용은 추측하지 말고 확인하지 못했다고 밝히세요. 근거로 쓴 문서는 [문서ID] 형식으로 표기하세요.
```

### 2. 판정 순서 (`graph.py`의 `model_body`)

`steps >= MAX_STEPS`일 때 위에서 아래로 판정한다:
1. `state.get("turn_read_ok", 0) < 1` → 마지막 호출 없이 `{"status": "failed", "error": "max_steps"}`.
2. `turn_cost_usd >= MAX_COST_USD` → 마지막 호출만 **생략**하고 결과는 기존과 같은 `failed/max_steps`다(판정 순서는 바꾸지 않는다: max_steps가 max_cost보다 먼저. 이 경우 오류 코드는 `max_cost`가 아니다).
3. 그 밖이면 마지막 호출 1회.

`steps < MAX_STEPS`이면 기존 흐름(기존 `max_cost` 판정 포함)이 바이트 단위로 그대로다.

### 3. 마지막 호출

- 기존 모델 호출 경로(`span`, `chat.complete`, 예외 처리, `model.call` 감사, Guardrail 정보 반영, 비용 누적)를 **그대로 거친다**. 코드를 복제하지 말고 `model_body` 안에서 `finalizing` 플래그로 분기한다. `chat.complete`의 인자는 `messages=[*state["messages"], {"role": "user", "content": FINALIZE_NOTICE}]`, 같은 `tools`·`task_id`·`user_id`다(시그니처·`tool_choice` 추가 없음). 안내문 메시지는 상태의 `messages`에 저장하지 않는다.
- `steps`는 8을 유지한다(증가시키지 않는다).
- 응답에 도구 호출이 **없고** 본문이 공백이 아니면: `status="completed"`, `answer=content`, `cited_doc_ids`는 기존 방식(`sorted(i for i in observed_ids if i in answer)`), `stop_reason="max_steps_finalized"`, assistant 메시지를 `messages`에 덧붙인다.
- 응답에 도구 호출이 있으면(쓰기 도구 포함) **실행하지도 초안을 만들지도 않는다**. 본문이 비면 마찬가지로 `failed/max_steps`다. 이 경우에도 비용·감사·Guardrail 정보는 반영한다.
- 마지막 호출에서 기존 예외 경로(`GuardrailBlocked`·`GuardrailUnavailable`·예산·게이트웨이 오류)가 나면 기존 처리 결과를 따른다(새 분기를 만들지 않는다).

### 4. 턴 범위 계수 `turn_read_ok`

- `AgentState`에 `turn_read_ok: int`, `turns.py`의 `TURN_KEYS`와 `turn_defaults`(초기값 0)에 같은 이름을 더한다. 대화 범위 상태(`evidence_ids`)는 마지막 호출 조건에 쓰지 않는다(앞 턴 근거로 마지막 호출이 열리면 안 된다). `observed_ids`도 쓰지 않는다(ID 없는 성공 결과를 세지 못한다).
- `read_tools_body`에서 `success = not outcome.is_error`가 참인 호출마다 1씩 더해 반환 딕셔너리의 `"turn_read_ok"`로 낸다. 읽을 때는 `state.get("turn_read_ok", 0)`(손으로 만든 옛 상태에는 키가 없다).

### 5. N 재계산 (`evalrun/regrade.py`의 `n_candidate`)

- 관측의 `stop_reason == "max_steps_finalized"`인 task는 `passed`(성공 task)에서 빼서 `inputs`와 `candidate` 계산에서 제외한다(성공 task로 섞이면 연속 무정보 횟수 최댓값이 부풀어 N이 커진다). 결과에 키 `excluded_finalized_tasks`(정렬 목록)를 더한다. 기존 키와 값은 바뀌지 않는다.
- **회귀 테스트(TDD, 코드를 바꾸기 전에)**: 현재 코드로 아래 네 실행의 `n_candidate(...)` 결과(`inputs`, `candidate`)를 구해 테스트에 리터럴로 고정한다. 변경 뒤에도 같고 `excluded_finalized_tasks == []`여야 한다. 대상(`data/results/dev/` 아래): `e36589778c2243388f09d9b4d3b60f2b`(dev3), `d2cd927c073447728c76c040d7bfb3bc`(dev4 켬), `710d8b6e56794dca8e4b027d47b8b5bf`(dev4 끔), `02ef5d5a64394671b3c99767c38e4e17`(dev5). 규칙 인자는 각 실행의 `manifest.json`이 말하는 `grading_rules`를 따른다. `n-registration-m5.md`와 결과 문서의 값은 수정하지 않는다.
- 합성 관측으로도 단언한다: `max_steps_finalized` task가 TCR을 통과해도 `inputs`에 없고 `excluded_finalized_tasks`에 나타나며 그 task의 큰 연속 무정보 횟수가 `candidate`를 올리지 않는다.

### 6. 테스트 (`tests/test_agent_finalize.py` 신규 + 기존 수정)

`test_agent_graph`의 `FakeChat`·`FakeTools`·`reply`·`call`을 쓴다.

- 권한 실패(오류 결과)만 8번 → `failed/max_steps`, `len(chat.calls) == 8`(마지막 호출 0회).
- **앞 턴에는 근거가 있고(`evidence_ids` 있음) 이번 턴은 실패만**(`turn_read_ok == 0`) → `failed/max_steps`, 마지막 호출 0회. `build_turn_state`로 다음 턴 상태를 만들면 `turn_read_ok == 0`으로 초기화되고 `evidence_ids`는 누적됨을 함께 단언한다.
- 성공 읽기 8번 + 마지막 응답 본문 → `completed`, `stop_reason == "max_steps_finalized"`, `steps == 8`, `len(chat.calls) == 9`(모델 호출 9회), 마지막 호출의 `messages[-1] == {"role": "user", "content": FINALIZE_NOTICE}`이고 `tools`가 첫 호출과 같다.
- 마지막 응답이 도구 호출(읽기·쓰기 모두) → `failed/max_steps`, 도구 실행 0건·초안 0건. 마지막 응답 본문이 비면 `failed/max_steps`.
- **비용 상한 + 8단계**: 도구 결과는 **성공 읽기**로 둔다(`turn_read_ok >= 1`이어야 비용 판정까지 간다). 응답당 비용은 `0.055`로 한다(`assert MAX_COST_USD == 0.40`을 함께 둔다: 7회 합 0.385 < 0.40, 8회 합 0.44 ≥ 0.40). 단언: 7단계까지는 `max_cost`로 끝나지 않고, 8단계에서 마지막 호출 0회(`len(chat.calls) == 8`), `status == "failed"`, `error == "max_steps"`(`max_cost`가 아님).
- 마지막 호출도 `MemoryAuditLog`에 `model.call` 감사를 남긴다(9건), `chat.complete`에 같은 키워드 인자를 쓴다(`messages` 제외 동일).
- **Guardrail 사전 훅 입력**: `screen_request([... , {"role": "user", "content": FINALIZE_NOTICE}])`의 판정이 `allow`다(안내문이 규칙에 걸리지 않음).
- 해시: `hashlib.sha256(FINALIZE_NOTICE.encode("utf-8")).hexdigest() == "f12f48773bd515212c472c54ace59093d3563419488d5bcd97b96cadf6f1b831"`이고 이 값이 `docs/results/hypotheses-m6-finalize.md`에 같은 문자열로 있다.
- 8단계 미만의 기존 흐름은 변하지 않는다: 골든 재생 테스트(`test_golden_m3`·`test_regolden`·`test_golden_trace`)와 프로필 해시 단언이 **수정 없이** 통과한다.
- **`tests/test_agent_limits.py::test_disabled_and_explicit_budget`**: 첫 부분의 설정을 `FakeTools(*(ToolOutcome({}, True, ()) for _ in range(MAX_STEPS)))`처럼 **오류 결과**로 바꾸고 단언(`status == "failed"`, `error == "max_steps"`, `steps == len(chat.calls) == MAX_STEPS`)은 **그대로** 둔다(`exploration_budget=None`). 둘째 부분(`exploration_budget=1`)은 그대로다. 원래 설정(성공 읽기 8개, `exploration_budget=None`, `FakeChat`에 8개의 도구 호출 응답 + 마지막 본문 응답 1개)은 **새 테스트**로 남겨 `status == "completed"`, `stop_reason == "max_steps_finalized"`, `steps == 8`, 모델 호출 9회(`len(chat.calls) == 9`)를 단언한다. 이 변경은 이 테스트의 설정에 한한다.
- **`tests/test_agent_graph.py::test_max_steps`**(~에 한함, 단언 약화 금지): 이 테스트는 성공 읽기(`ToolOutcome(..., False, ...)`) 8개로 8단계에 도달하므로 새 경로가 9번째 호출을 연다. 단언(`status == "failed"`, `error == "max_steps"`, `steps == MAX_STEPS`, `observed_ids`)을 그대로 두고 설정만 바꾼다: 도구 결과를 오류 결과로 바꿔 단언이 그대로 성립하면 그렇게 한다. `observed_ids` 단언이 오류 결과에서 성립하지 않으면 `FakeChat`에 도구 호출 응답을 하나 더 두어(마지막 호출이 도구를 부르면 `failed/max_steps`다) `len(chat.calls) == MAX_STEPS + 1`로만 고친다.
- **`tests/test_obs_instrument.py::test_failure_trace[max_steps]`**(~에 한함, 단언 약화 금지): 같은 이유로 `FakeChat`이 8개 응답만 가지면 9번째 호출에서 소진된다. 단언(`result["error"] == "max_steps"`, `records[-1].status == "error"`, `error_code == result["error"]`, `closed == [ident]`)은 그대로 두고 `max_steps` 변형의 설정만 위와 같은 방식으로 바꾼다. `gateway` 변형은 건드리지 않는다.
- `tests/test_agent_turns.py`: 키 집합 단언의 차이는 정확히 `{"turn_read_ok"}`이다(`turn_defaults()`의 키 집합 == `TURN_KEYS`, `CONVERSATION_KEYS | TURN_KEYS == set(AgentState.__annotations__)` 유지). 다른 키를 더하거나 단언을 느슨하게 하지 않는다.

**사용자 실행(이 step에서 실행하지 않는다)**: 없음. live 확인은 phase 머지 뒤 브라우저에서 사용자가 한다.

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

- 마지막 응답에 도구 호출이 와도 그 도구를 호출하지 말고 쓰기 초안도 만들지 마라. 이유: 승인 없는 부작용 우회 경로가 된다. 마지막 응답에 도구 호출이 있으면 `failed/max_steps`다.
- `evidence_ids`나 `observed_ids`로 마지막 호출 조건을 판정하지 마라. 이유: `evidence_ids`는 대화 범위 누적이라 앞 턴 근거로 열리고, `observed_ids`는 ID 없는 성공 결과를 못 센다. 조건은 턴 범위 `turn_read_ok >= 1`이다.
- `SYSTEM_PROMPT`·`WITH_WRITES`·`PROFILES`·도구 정의·모델·호출 파라미터를 바꾸지 마라. 이유: 프롬프트 해시와 골든 14개 기록의 `request_key`가 그대로여야 재기록이 필요 없다(`hypotheses-m6-finalize.md`). `ChatClient.complete`의 시그니처나 `tool_choice`도 추가하지 않는다.
- `max_cost` 판정이 `max_steps` 판정보다 먼저 오게 바꾸지 마라. 이유: 8단계에서 비용 상한을 넘었으면 마지막 호출만 생략하고 오류 코드는 기존과 같은 `max_steps`다. `tests/test_agent_limits.py`의 기존 단언(`max_steps`, `max_cost`)은 약화하지 않는다.
- `docs/results/n-registration-m5.md`와 `data/replay/`·`data/results/`의 기록을 수정하거나 골든을 재기록하지 마라. 이유: 이 변경은 경로 한정이고 사전 등록 기록은 보존한다. N 재계산 변경은 `evalrun/regrade.py` 코드와 회귀 테스트만이다.

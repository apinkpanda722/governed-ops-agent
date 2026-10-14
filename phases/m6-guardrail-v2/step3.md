# Step 3: v2-graph-wiring

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-guardrail-v2.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/agent/graph.py`(`build_graph`, `model_body`의 `steps` 계산·`GuardrailBlocked` 처리, `read_tools_body`의 도구 호출 루프와 `update`, `guardrail_record`·`guardrail_success`, `quarantined_ids`)
- `/src/govops/agent/turns.py`(`TURN_KEYS`, `turn_defaults`), `/src/govops/api/wiring.py`(`build_task_service`, `open_task_service`), `/src/govops/api/tasks.py`(`blocked_input` 뷰 — 읽기만 한다)
- `/src/govops/audit/log.py`(`GUARDRAIL_CODES`), `/src/govops/replay/llm.py`(`ReplayChatClient.__init__`), `/src/govops/evalrun/dev_m3.py`(`build_task_service` 호출, manifest 작성), `/src/govops/evalrun/faults.py`, `/src/govops/evalrun/faults_smoke.py`(`build_task_service` 호출)
- `/src/govops/guardrails/semantic.py`, `/src/govops/gateway/client.py`(step 1·2 산출물: `select_user_item`, `select_tool_items`, `build_request`, `apply_quarantine`, `classify`, `ClassifyResult`, `status_counts`)
- `/tests/test_agent_guardrail.py`, `/tests/test_agent_turns.py`, `/tests/test_audit_events.py`, `/tests/test_audit.py`, `/tests/test_api_wiring_build.py`, `/tests/test_api_wiring.py`, `/tests/test_evalrun_dev_m3.py`, `/tests/test_evalrun_faults.py`, `/tests/test_evalrun_faults_smoke.py`, `/tests/test_obs_instrument.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/graph.py`, `src/govops/api/wiring.py`, `src/govops/audit/log.py`, `src/govops/replay/llm.py`, `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `src/govops/evalrun/faults_smoke.py`, `tests/test_agent_semantic.py`, `tests/test_api_wiring_semantic.py`. 단, 아래 **소비자 점검 대상** 단락의 파일은 그 단락의 조건(단언이 실제로 깨질 때, 새 값을 반영하는 것에 한함)에서만 이 목록에 포함된다. TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/agent/graph.py`, `src/govops/api/wiring.py`, `src/govops/audit/log.py`, `src/govops/replay/llm.py`, `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `src/govops/evalrun/faults_smoke.py`, `tests/test_agent_semantic.py`, `tests/test_api_wiring_semantic.py`, `tests/e2e_backend.py`, `tests/test_agent_finalize.py`, `tests/test_agent_graph.py`, `tests/test_agent_guardrail.py`, `tests/test_agent_limits.py`, `tests/test_agent_model_call_event.py`, `tests/test_agent_requester_prompt.py`, `tests/test_agent_retention.py`, `tests/test_agent_turns.py`, `tests/test_api_conversation_delete.py`, `tests/test_api_conversation_delete_route.py`, `tests/test_api_conversation_views.py`, `tests/test_api_conversations.py`, `tests/test_api_events.py`, `tests/test_api_guardrail_view.py`, `tests/test_api_tasks.py`, `tests/test_api_ui_contract.py`, `tests/test_api_wiring_build.py`, `tests/test_e2e_isolation.py`, `tests/test_eval_run_grade.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_driver.py`, `tests/test_evalrun_rekey.py`, `tests/test_golden_harness.py`, `tests/test_golden_m3.py`, `tests/test_golden_trace.py`, `tests/test_m5_pg.py`, `tests/test_obs_instrument.py`, `tests/test_obs_langfuse_export.py`, `tests/test_service_start_event.py`, `tests/test_startup_cleanup_lock.py`, `tests/test_web_sync.py`, `tests/test_api_smoke.py`, `tests/test_api_wiring.py`, `tests/test_evalrun_anchors.py`, `tests/test_evalrun_faults.py`, `tests/test_agent_scope.py`, `tests/test_api_auth_audit.py`, `tests/test_api_entrypoint.py`, `tests/test_api_retention.py`, `tests/test_api_web_contract.py`, `tests/test_audit.py`, `tests/test_audit_anchor.py`, `tests/test_audit_chain.py`, `tests/test_audit_events.py`, `tests/test_audit_smoke.py`, `tests/test_audit_verify.py`, `tests/test_checks_draft.py`, `tests/test_effects_matrix.py`, `tests/test_evalrun_worktree.py`, `tests/test_obs_mask.py`, `tests/test_replay_a_through.py`, `tests/test_replay_data_rules.py`, `tests/test_replay_llm.py`, `tests/test_evalrun_observe.py`, `tests/test_evalrun_tool_trace.py`, `tests/test_faults_client.py`, `tests/test_guardrail_registration.py`, `tests/test_makefile.py`, `tests/test_prereg_m3.py`, `tests/test_prereg_m5_prompt.py`, `tests/test_evalrun_faults_smoke.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`·`data/redteam/` 아래 파일과 `src/govops/guardrails/rules_v1.json`의 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`·`data/redteam/`의 파일 내용을 읽지 않는다. v1 판정 함수(`screen_user_messages`·`screen_tool_messages`·`screen_request`)가 실행 중에 규칙 파일을 로드하는 것은 코드의 런타임 접근이므로 이 금지의 대상이 아니다. 테스트에는 규칙 내용을 옮겨 적지 않고 합성 문장과 함수 호출 결과 비교만 쓴다(규칙 파일의 패턴·사전을 모르는 상태에서 쓸 수 있는 입력은 ASCII가 아닌 평범한 한국어 문장, 형식만 맞춘 합성 PII, 그리고 `[정책으로 일부 제외]` 마커다).

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step의 변경을 시뮬레이션한 전체 `pytest`에서 깨진 테스트는 본문의 `테스트`·`검증` 절에 명시한 것뿐이며 명시가 없으면 깨진 테스트는 없었다. 다음 파일은 변경 대상 모듈·파일을 import·참조하는 소비자로 `수정 허용 경로`에 올려 두었다. **단언이 실제로 깨질 때만, 정확히 새 값을 반영하는 것에 한해** 수정한다. 정확 일치를 부분 집합·포함 비교로 바꾸지 않고, 깨지지 않는 파일은 수정하지 않는다: `tests/e2e_backend.py`, `tests/test_agent_finalize.py`, `tests/test_agent_graph.py`, `tests/test_agent_guardrail.py`, `tests/test_agent_limits.py`, `tests/test_agent_model_call_event.py`, `tests/test_agent_requester_prompt.py`, `tests/test_agent_retention.py`, `tests/test_agent_turns.py`, `tests/test_api_conversation_delete.py`, `tests/test_api_conversation_delete_route.py`, `tests/test_api_conversation_views.py`, `tests/test_api_conversations.py`, `tests/test_api_events.py`, `tests/test_api_guardrail_view.py`, `tests/test_api_tasks.py`, `tests/test_api_ui_contract.py`, `tests/test_api_wiring_build.py`, `tests/test_e2e_isolation.py`, `tests/test_eval_run_grade.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_driver.py`, `tests/test_evalrun_rekey.py`, `tests/test_golden_harness.py`, `tests/test_golden_m3.py`, `tests/test_golden_trace.py`, `tests/test_m5_pg.py`, `tests/test_obs_instrument.py`, `tests/test_obs_langfuse_export.py`, `tests/test_service_start_event.py`, `tests/test_startup_cleanup_lock.py`, `tests/test_web_sync.py`, `tests/test_api_smoke.py`, `tests/test_api_wiring.py`, `tests/test_evalrun_anchors.py`, `tests/test_evalrun_faults.py`, `tests/test_agent_scope.py`, `tests/test_api_auth_audit.py`, `tests/test_api_entrypoint.py`, `tests/test_api_retention.py`, `tests/test_api_web_contract.py`, `tests/test_audit.py`, `tests/test_audit_anchor.py`, `tests/test_audit_chain.py`, `tests/test_audit_events.py`, `tests/test_audit_smoke.py`, `tests/test_audit_verify.py`, `tests/test_checks_draft.py`, `tests/test_effects_matrix.py`, `tests/test_evalrun_worktree.py`, `tests/test_obs_mask.py`, `tests/test_replay_a_through.py`, `tests/test_replay_data_rules.py`, `tests/test_replay_llm.py`, `tests/test_evalrun_observe.py`, `tests/test_evalrun_tool_trace.py`, `tests/test_faults_client.py`, `tests/test_guardrail_registration.py`, `tests/test_makefile.py`, `tests/test_prereg_m3.py`, `tests/test_prereg_m5_prompt.py`, `tests/test_evalrun_faults_smoke.py`. 이 단락의 파일을 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 호스트 수집 지점에 v2를 끼우고 **실제 진입점에 결선**한다. 스위치 `GOVOPS_GUARDRAIL_V2`(기본 off)는 호스트만 읽는다. 스위치가 off이면 그래프·메시지·체크포인트·골든이 변경 전과 **바이트 단위로 같아야 한다.** 이 step은 가짜 분류기(`classify`를 가진 가짜 객체)로 결정론 테스트를 한다.

### 1. `agent/graph.py`

- `build_graph(..., semantic=None)`: 선택 인자(기본 `None`). `semantic`은 `classify(self, *, request, task_id, user_id) -> ClassifyResult`를 가진 객체다. 컴파일된 그래프에 `graph.agent_semantic = semantic`을 설정한다(`agent_profile` 등과 같은 방식, 진입점 결선 테스트가 읽는다).
- **사용자 입력 지점**: `model_body`에서 비용 상한 검사 뒤, `steps = …` 줄과 `span(SpanKind.MODEL_CALL, …)` **앞**에서 `semantic is not None and state["steps"] == 0 and not finalizing`일 때만 실행한다. `select_user_item(state["request"], config)` → `None`이면 건너뛴다(게이트웨이가 기존대로 막는다). 항목이면 `span(SpanKind.GUARDRAIL, "semantic")` 안에서 `semantic.classify`를 부른다.
  - `unsafe` → 모델 호출 없이 반환: `status="blocked_input"`, `error="injection_direct"`, `guardrail_block={"code": "injection_direct"}`, `answer=None`, `steps`는 **변경하지 않는다**. 감사 `guardrail.input_blocked`(`code="injection_direct"`, `input_sha256=sha256(state["request"])`, `v2=True`)를 기존 `guardrail_record`로 쓴다(`rules_digest`·`code_digest`는 넣지 않는다).
  - `ok`가 아닌 상태(`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`)는 **통과**하고 감사 `guardrail.error`(`code="guardrail_v2_error"`, `count=1`)를 쓴다. `BudgetExceededError`·`LedgerMissingError`는 기존 `except` 경로와 같은 코드(`budget_exceeded`·`ledger_missing`)로 `failed`다.
- **도구 결과 지점**: `read_tools_body`에서 도구 호출 `for` 루프가 끝난 직후, `update = {…}` 구성 **앞**에서 `semantic is not None`일 때만 실행한다. 이번 라운드에 추가된 도구 메시지들만 `select_tool_items`로 선별하고(`messages`는 이미 `content` 잘라내기까지 끝난 상태), 항목을 `asyncio.gather`로 병렬 분류한다(동시 `config.concurrency`개, `asyncio.Semaphore`). `unsafe` 항목은 `apply_quarantine`으로 치환한 새 `messages`를 쓰고, 치환된 `tool_call_id`마다 `sha256(id)`가 `quarantined_ids`에 없으면 추가하고 감사 `guardrail.tool_quarantined`(`tool_call_sha256`, `count=1`, `v2=True`)를 쓴다. `ok`가 아닌 상태와 라운드 항목 초과 개수는 라운드당 최대 1건의 `guardrail.error`(`guardrail_v2_error`, `count`=실패·초과 합)로 알린다. 분류기 예산 예외는 위와 같이 `failed`다.
  - 반환 `update`에 `messages`(치환 반영)와 `quarantined_ids`(추가분)만 더한다. **새 상태 키를 만들지 않는다**(`tests/test_agent_turns.py`의 키 집합 불변). `observed_ids`·`evidence_ids`·탐색 예산 계산·`turn_read_ok`는 원본 `payload` 기준 그대로다.
  - 분류 span 속성에는 상태별 건수·라벨 건수·토큰 합·ms만 둔다(`guardrail.v2_n`·`_ok`·`_flag`·`_fail`·`_in`·`_out`·`_ms`). 분류기 입력 원문·응답 본문·점수를 속성·감사·상태에 남기지 않는다.
- `FINALIZE_NOTICE`·에이전트가 만든 오류 안내 tool 메시지·이력은 분류하지 않는다.

### 2. `audit/log.py`

- `GUARDRAIL_CODES`에 `"guardrail_v2_error"` **하나만** 더한다. 다른 이벤트·필드 검증은 바꾸지 않는다. 불리언 필드 `v2`는 기존 검증이 통과시키므로 코드 변경이 필요 없다(테스트로 확인).

### 3. 결선(FP-070): 실제 진입점이 같은 분류기를 쓴다

- `api/wiring.py`의 `build_task_service(..., semantic=_FROM_ENV)`: 인자를 주지 않으면 `env_or("GOVOPS_GUARDRAIL_V2", "off") == "on"`일 때 `chat`(분류 메서드 `classify`를 가진 `LiteLLMChatClient`)을 `semantic`으로 쓰고 아니면 `None`이다. 스위치가 on인데 `chat`에 `classify`가 없으면 `StartupError("guardrail_v2_unavailable")`다(조용히 꺼지지 않는다). 명시 인자 `semantic=None`은 v2 off를 뜻한다. 그래프 조립은 `build_graph(..., semantic=semantic)`로 넘긴다. 독스트링의 "설정을 읽지 않는다"를 이 스위치 한 가지가 예외임을 적도록 고친다.
- `open_task_service`·`evalrun/dev_m3.py`의 서비스 조립(`service_factory or build_task_service`)은 `semantic` 인자를 **주지 않아** 환경 변수를 따른다(`make serve`·평가 러너가 같은 결선을 쓴다). `evalrun/faults.py`·`evalrun/faults_smoke.py`는 `build_task_service(..., semantic=None)`을 **명시**한다(장애 주입 평가는 v1 단독이다).
- 호출 지점의 분류기 객체는 `chat` 하나다. 별도 키·별도 클라이언트를 만들지 않는다. 호스트는 `ANTHROPIC_API_KEY`를 읽지 않는다.
- `evalrun/dev_m3.py`: dev manifest에 `guardrail_v2`(`{"switch": "on"|"off", "cfg_sha256": <chat.config_sha256() 또는 None>, "status_counts": <chat.status_counts() 또는 {}>, "runner_concurrency": 1}`)를 더한다(`finished_at` 앞, 기존 키는 그대로). dev 러너는 task를 순차 실행(`for spec in specs` 안에서 `await`)하므로 동시성은 1이다. phase ③이 v2 켠 실행마다 상태 분포와 러너 동시성을 결과에 적는다. `evalrun`은 `govops.guardrails`를 import할 수 없다. cfg 해시는 `chat.config_sha256()`로만 얻는다.
- `replay/llm.py`: `ReplayChatClient.__init__`이 `GOVOPS_GUARDRAIL_V2`가 `on`이면 `ValueError("guardrail_v2_replay")`를 던진다(`env_or`와 같은 정규화: 빈 문자열·공백은 off). 스위치 off에서는 동작이 변하지 않는다. 기록 재생·CI는 스위치 off 고정이다.

### 4. 테스트(가짜 분류기·가짜 게이트웨이, 네트워크 없음)

- **`tests/test_agent_semantic.py`**: ① `semantic=None`에서 `chat.complete`에 전달된 `messages`·인자가 변경 전과 바이트 단위로 같고 `quarantined_ids`·감사 이벤트가 같다(스위치 off 불변). ② 사용자 `unsafe` → 모델 호출 0건·`blocked_input`·`error="injection_direct"`·`steps == 0`, 감사 `guardrail.input_blocked`가 게이트웨이 경로(`GuardrailBlocked`)와 같은 필드 집합에 `v2=True`만 더한 것, `input_sha256`은 요청의 sha256. ③ 도구 `unsafe` → 해당 문자열이 마커로 치환되어 **다음 모델 호출의 `messages`에 반영**되고 `quarantined_ids`에 sha256이 1번만 들어가며 이후 라운드에서 같은 항목의 분류기 호출이 0건. ④ 분류 상태 `timeout`·`rate_limited`·`error`·`parse`·`v1_blocked` 모두 task가 `completed`(또는 정상 종료)로 이어지고 `guardrail.input_blocked`·`blocked_input`이 생기지 않으며 `guardrail.error`(`guardrail_v2_error`)만 남는다(**검사 대상이 1건 이상**임을 먼저 단언한다, FP-069). ⑤ 분류기 예산 예외 → `failed`/`budget_exceeded`·모델 호출 0건. ⑥ `evidence_ids`·탐색 예산 카운터·`turn_read_ok`가 치환과 무관하다. ⑦ `FINALIZE_NOTICE` 마무리 호출 경로에서 분류기 호출이 0건이다. ⑧ trace 속성·감사에 분류 대상 텍스트(합성 센티널)가 없다. ⑨ 라운드 항목 16개 초과 시 초과분은 분류되지 않고 경보 건수에 합산된다. ⑩ 감사 쓰기 실패가 방어 결과(치환·차단)를 바꾸지 않는다(기존 `guardrail_record` 동작).
- **`tests/test_api_wiring_semantic.py`(결선, FP-070)**: `GOVOPS_GUARDRAIL_V2`가 `on`일 때 `build_task_service`(인자 생략)로 조립한 서비스의 `graph.agent_semantic is chat`, off·미설정·빈 문자열이면 `None`, `on`이고 `chat`에 `classify`가 없으면 `StartupError`, 명시 `semantic=None`은 환경 변수와 무관하게 `None`. 소스 스캔(AST): `src/govops` 안의 모든 `build_task_service(` 호출이 `semantic=None`을 명시하는 곳(`evalrun/faults.py`·`evalrun/faults_smoke.py`)이거나 인자를 생략하는 곳이다(다른 값을 주는 호출이 없다). `open_task_service`와 `evalrun/dev_m3.py`의 호출이 `semantic` 인자를 생략함을 단언한다. `ReplayChatClient`는 스위치 on에서 `ValueError`, off에서 정상.
- 기존 소비자(**~에 한함, 단언 약화 금지**): 변경을 시뮬레이션한 전체 `pytest`에서 깨진 테스트는 없었다. 아래는 소비자 점검 대상이며 단언이 실제로 깨질 때만 새 값을 **더하는 것에 한해** 수정한다: `tests/test_audit.py`·`tests/test_audit_events.py`·`tests/test_agent_guardrail.py`의 `guardrail.error` 코드 목록(`guardrail_v2_error`), `tests/test_evalrun_dev_m3.py`의 manifest(새 키 `guardrail_v2`), `tests/test_api_wiring_build.py`의 `build_task_service` 서명. 키 집합·코드 집합의 정확 일치를 부분 집합 비교로 바꾸지 않는다.

**사용자 실행(이 step에서 실행하지 않는다)**: 실제 서버(`make serve`)·평가 러너에서의 호출·Langfuse에 원문 부재·분류 응답 형식은 가짜로 재현할 수 없다. 머지 뒤 `make smoke-semantic`(step 4)과 사용자 live가 확인한다.

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

- 스위치 off(`semantic=None`)에서 `messages`·체크포인트·감사·trace 구조를 바꾸지 마라. 이유: 골든 14개와 dev 기록은 재기록하지 않는다. `test_golden_m3`·`test_regolden`·`test_golden_trace`는 수정 없이 통과해야 한다.
- 그래프에 새 노드·새 상태 키를 만들지 마라. 이유: 토폴로지·`TURN_KEYS`가 바뀌면 골든 trace와 `tests/test_agent_turns.py`가 깨진다. 기존 `quarantined_ids`·`messages`만 쓴다.
- 분류기 입력 원문·응답 본문·점수·상태 문자열을 감사 필드·상태·trace 속성에 남기지 마라. 이유: 감사는 해시·건수만 허용하고 trace는 원문 부재를 smoke가 검사한다(설계 2-8).
- 라우트·서비스 생성 시점의 값을 클로저로 붙잡지 마라. 이유: 실제 서버 진입점이 lifespan에서 의존성을 교체해 자리표시자 결함이 생긴 사례(FP-070). 분류기는 `build_task_service`가 조립한 `graph.agent_semantic` 한 곳만 쓴다.
- `make serve`·`make smoke-semantic`·`make smoke-guardrail`을 실행하지 마라. 이유: 실제 서비스에 접속하는 사용자 실행 항목이다.

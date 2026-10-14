# Step 2: v2-classifier-client

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-guardrail-v2.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/gateway/client.py`(`LiteLLMChatClient.complete`, `_send`, `GuardrailBlocked`, `GuardrailUnavailable`, `_protect_transport_logs`, `_IN_CALL`, 예약·정산 블록)
- `/src/govops/costs.py`(`CostLedger.reserve`·`settle`, `CLASSIFIER_DEPLOYMENTS`, `classifier_cost_usd`, `BudgetExceededError`), `/src/govops/guardrails/semantic.py`(step 1 산출물: `ClassifierRequest`, `load_config`, `parse_label`)
- `/tests/test_gateway_client.py`(요청 본문·원장 행·`_send` 관련 단언), `/tests/test_smoke_gateway.py`(`body["tools"] == []` 단언), `/tests/test_architecture.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/gateway/client.py`, `tests/test_gateway_classifier.py`. 단, 아래 **소비자 점검 대상** 단락의 파일은 그 단락의 조건(단언이 실제로 깨질 때, 새 값을 반영하는 것에 한함)에서만 이 목록에 포함된다. TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/gateway/client.py`, `tests/test_gateway_classifier.py`, `tests/e2e_backend.py`, `tests/test_agent_finalize.py`, `tests/test_agent_graph.py`, `tests/test_agent_guardrail.py`, `tests/test_agent_model_call_event.py`, `tests/test_agent_turns.py`, `tests/test_api_conversation_views.py`, `tests/test_api_guardrail_view.py`, `tests/test_api_wiring_build.py`, `tests/test_architecture.py`, `tests/test_evalrun_dev.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults.py`, `tests/test_evalrun_faults_smoke.py`, `tests/test_gateway_client.py`, `tests/test_obs_instrument.py`, `tests/test_replay_llm.py`, `tests/test_team_keys.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`·`data/redteam/` 아래 파일과 `src/govops/guardrails/rules_v1.json`의 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`·`data/redteam/`의 파일 내용을 읽지 않는다. v1 판정 함수(`screen_user_messages`·`screen_tool_messages`·`screen_request`)가 실행 중에 규칙 파일을 로드하는 것은 코드의 런타임 접근이므로 이 금지의 대상이 아니다. 테스트에는 규칙 내용을 옮겨 적지 않고 합성 문장과 함수 호출 결과 비교만 쓴다(규칙 파일의 패턴·사전을 모르는 상태에서 쓸 수 있는 입력은 ASCII가 아닌 평범한 한국어 문장, 형식만 맞춘 합성 PII, 그리고 `[정책으로 일부 제외]` 마커다).

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step의 변경을 시뮬레이션한 전체 `pytest`에서 깨진 테스트는 본문의 `테스트`·`검증` 절에 명시한 것뿐이며 명시가 없으면 깨진 테스트는 없었다. 다음 파일은 변경 대상 모듈·파일을 import·참조하는 소비자로 `수정 허용 경로`에 올려 두었다. **단언이 실제로 깨질 때만, 정확히 새 값을 반영하는 것에 한해** 수정한다. 정확 일치를 부분 집합·포함 비교로 바꾸지 않고, 깨지지 않는 파일은 수정하지 않는다: `tests/e2e_backend.py`, `tests/test_agent_finalize.py`, `tests/test_agent_graph.py`, `tests/test_agent_guardrail.py`, `tests/test_agent_model_call_event.py`, `tests/test_agent_turns.py`, `tests/test_api_conversation_views.py`, `tests/test_api_guardrail_view.py`, `tests/test_api_wiring_build.py`, `tests/test_architecture.py`, `tests/test_evalrun_dev.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults.py`, `tests/test_evalrun_faults_smoke.py`, `tests/test_gateway_client.py`, `tests/test_obs_instrument.py`, `tests/test_replay_llm.py`, `tests/test_team_keys.py`. 이 단락의 파일을 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 호스트가 분류 요청을 **기존 게이트웨이 클라이언트**(`LiteLLMChatClient`)로 보낸다(AGENTS.md CRITICAL: 에이전트의 모델 호출은 `gateway` 클라이언트로만). 이 step은 `classify` 메서드, 호출별 타임아웃·`tools`·`tool_choice` 선택 인자, `guardrail_v2` 예약·정산을 만든다. 이름 `classify`·`ClassifyResult`의 기존 사용처는 없다. **실제 LiteLLM·Anthropic에는 접속하지 않는다**(가짜 SDK 응답·`httpx.MockTransport`만).

### 1. `client.py`

- `@dataclass(frozen=True) class ClassifyResult: label: Literal["safe", "unsafe"] | None; status: Literal["ok", "timeout", "rate_limited", "error", "parse", "v1_blocked"]; input_tokens: int; output_tokens: int; cost_usd: float`.
- `LiteLLMChatClient.classify(self, *, request: ClassifierRequest, task_id: str, user_id: str) -> ClassifyResult`:
  - 사용자 팀 키(`key_resolver(user_id)`)와 `require_guardrail`·`expected_digests` 헤더 검증을 `complete`와 같이 쓴다. 본문 `model`은 설정의 `model`(`guardrail-v2`), `metadata`는 `task_id`·`trace_user_id`·`generation_name="guardrail-v2"`·`existing_trace_id`(task ID가 32 hex일 때)·`parent_observation_id`(있을 때). `max_completion_tokens`는 설정값, **호출별 타임아웃**은 설정의 `timeout_s`(연결 `connect_timeout_s`), 재시도 없음.
  - **예약**: `CostLedger.reserve(purpose="guardrail_v2", model="claude-haiku-5-5", task_id=task_id, estimate_usd=…)`를 `asyncio.to_thread`로 부른다. 추정액은 `classifier_cost_usd(input_tokens=2 * len(json.dumps(body, ensure_ascii=True).encode("utf-8")) + 1024, output_tokens=max_completion_tokens)`다. 상한 초과(`BudgetExceededError`)와 `LedgerMissingError`는 **호출 없이 그대로 올린다**(이 둘만 예외다).
  - **정산**: 정상 응답은 `usage`(`prompt_tokens`·`completion_tokens`)와 `classifier_cost_usd`, 응답 없음(타임아웃·연결 오류)은 예약 추정액, 429·4xx·`v1_blocked`는 0으로 `settle`한다. 정산은 `asyncio.shield`로 보호한다(`complete`와 같음).
  - **응답 배포 ID**: 응답 헤더 `x-litellm-model-id`가 `CLASSIFIER_DEPLOYMENTS`의 키가 아니면(예: 에이전트 배포로 라우팅됨) `status="error"`로 두고 라벨을 쓰지 않는다.
  - **상태 분류**: 정상 응답이고 `parse_label(...)`이 라벨이면 `ok`, `APITimeoutError`는 `timeout`, HTTP 429는 `rate_limited`, `GuardrailBlocked`(400 `injection_direct`·`pii_input`)는 `v1_blocked`, `GuardrailUnavailable`·연결 오류·그 밖의 4xx/5xx·응답 형식 오류는 `error`, 어휘 밖·`finish_reason == "length"`는 `parse`. **어떤 경우에도 예외 메시지·URL·요청 본문·키를 반환값·로그에 남기지 않는다.**
  - `ClassifyResult`에는 응답 헤더·원문을 넣지 않는다(`ChatResult.guardrail`을 만들지 않는다).
  - 상태별 건수를 세는 `status_counts() -> dict[str, int]`를 더한다(프로세스 수명 동안 누적, 스레드 안전).
- `_send`: `tools`·`tool_choice`·호출별 `timeout_s`를 **키워드 선택 인자**로 더한다. **기존 `complete`와 smoke 경로가 보내는 요청 본문은 바이트 단위로 같아야 한다**(`tools`가 빈 목록 `[]`이면 `[]`를 그대로 보낸다. `tests/test_smoke_gateway.py`가 `body["tools"] == []`를 단언한다). `classify`의 사용자형 요청은 `tools`를 보내지 않는다.
- `LiteLLMChatClient.config_sha256()`을 더한다(`guardrails.semantic.config_sha256()`의 얇은 위임, 평가 러너 계층이 `govops.guardrails`를 직접 import하지 못하므로 이 경로로 얻는다).

### 2. 테스트(`tests/test_gateway_classifier.py`, 가짜 SDK 응답·임시 원장)

- 상태 매핑 표: 200 정상 라벨(`safe`/`unsafe` 자리표시 어휘)·어휘 밖·`length`·429·500·타임아웃·400 `injection_direct`·400 `pii_input`·403 `key_model_access_denied`·응답 배포 ID 불일치 → `ok`·`parse`·`parse`·`rate_limited`·`error`·`timeout`·`v1_blocked`·`v1_blocked`·`error`·`error`. **예산 초과 외에는 예외가 올라오지 않는다.**
- 요청 본문: 사용자형은 `tools` 키가 없고 도구형은 고정 도구 정의 1개와 `tool_choice == "none"`이다. 모델 `guardrail-v2`, 호출별 타임아웃이 설정값이며 재시도 0회. 팀 키 resolver가 호출된다.
- 원장: 예약 `purpose="guardrail_v2"`·`model="claude-haiku-5-5"`, 정산 행(성공: usage 기반, 타임아웃: 예약액, 429·`v1_blocked`: 0). 상한 초과 시 SDK 호출 0회. 응답 헤더의 `q`·`m`·`ids`가 반환값에 없다.
- 키·본문·예외 문장이 반환값·캡처한 로그(`caplog`)·`status_counts`에 없다(키와 대상 텍스트로 합성 센티널을 넣어 확인한다).
- **기존 경로 불변**: 같은 입력으로 `complete`를 부를 때 SDK에 전달되는 요청 인자(모델·메시지·`tools`·`extra_body`)가 변경 전과 같고 원장 행이 예약 2건·정산 2건이다(기존 `tests/test_gateway_client.py`가 덮는다).
- 기존 소비자(**~에 한함, 단언 약화 금지**): `tests/test_gateway_client.py`·`tests/test_smoke_gateway.py`는 수정 없이 통과해야 한다(변경을 시뮬레이션한 전체 `pytest`에서 `_send`에 선택 인자를 더한 것만으로는 깨지지 않지만 `tools`가 빈 목록일 때 키를 빼면 `tests/test_smoke_gateway.py::test_fallback`이 깨진다).

**사용자 실행(이 step에서 실행하지 않는다)**: 실제 별칭 라우팅·키 허용 모델·`tool_use` 요청 형식의 수용·응답 `usage` 형태는 가짜 응답으로 재현할 수 없다. 머지 뒤 `make smoke-semantic`(step 4)이 첫 확인이다.

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

- `classify`에서 `GuardrailBlocked`·`GuardrailUnavailable`·SDK 예외를 호출자에게 올리지 마라(`BudgetExceededError`·`LedgerMissingError`는 예외). 이유: 분류 요청이 v1에 막혀도 원래 task를 막으면 안 된다. 그래프가 `blocked_input`으로 오인한다(설계 0-3).
- 예외 문장·응답 본문·키·요청 텍스트를 반환값·로그·trace 속성에 남기지 마라. 이유: SDK 예외는 요청 정보를 담을 수 있다(FP-065). 상태 문자열과 숫자만 남긴다.
- SDK가 올려 준 예외 형태에만 기대어 타임아웃을 분류하지 마라. 이유: 같은 오류를 타이밍에 따라 다르게 감싸 올린다(FP-023). `APITimeoutError`와 연결 오류를 모두 `timeout`·`error`로 매핑하는 두 경로를 테스트한다.
- `complete`의 요청 본문·예약 순서·정산을 바꾸지 마라. 이유: 골든·dev 기록과 비용 원장 테스트가 정확 일치를 단언한다. `_send`의 새 인자는 기본값이 기존 동작이다.
- `REGISTERED_MODELS`·`DEPLOYMENT_MODELS`에 Haiku를 넣어 비용을 계산하지 마라. 이유: 분류 비용은 `classifier_cost_usd`와 `CLASSIFIER_DEPLOYMENTS`로만 해석한다(step 0).

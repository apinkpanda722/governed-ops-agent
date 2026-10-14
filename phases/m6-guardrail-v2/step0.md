# Step 0: v2-alias-and-prices

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-guardrail-v2.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`(`model_list`, `router_settings.fallbacks`, `guardrails`)
- `/src/govops/gateway/agent_key.py`(`AGENT_KEY_MODELS`), `/src/govops/gateway/team_keys.py`(읽기만 한다. 같은 목록을 쓴다), `/src/govops/costs.py`(`PRICES`, `REGISTERED_MODELS`, `DEPLOYMENT_MODELS`, `resolve_deployment`, `resolve_model`), `/src/govops/obs/mask.py`(`DEPLOYMENT_IDS`)
- `/tests/test_agent_key.py`(`test_success`의 `models` 목록, `test_agent_key_models_match_config`), `/tests/test_costs.py`(`test_prices_and_constants`), `/tests/test_audit_events.py`(`test_header_sets_and_model_aliases`), `/tests/test_compose.py`(`test_litellm_has_primary_model_and_environment_settings`, `test_gateway_fallback_configs_match`), `/tests/test_gateway_client.py`(`test_deployment_configuration_ids`), `/tests/test_obs_mask.py`(`test_deployment_identifier_registry`)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `infra/litellm/config.yaml`, `infra/litellm/config.obs.yaml`, `src/govops/gateway/agent_key.py`, `src/govops/costs.py`, `src/govops/obs/mask.py`, `tests/test_v2_alias_config.py`, `tests/test_agent_key.py`, `tests/test_costs.py`, `tests/test_audit_events.py`, `tests/test_compose.py`, `tests/test_gateway_client.py`. 단, 아래 **소비자 점검 대상** 단락의 파일은 그 단락의 조건(단언이 실제로 깨질 때, 새 값을 반영하는 것에 한함)에서만 이 목록에 포함된다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `infra/litellm/config.yaml`, `infra/litellm/config.obs.yaml`, `src/govops/gateway/agent_key.py`, `src/govops/costs.py`, `src/govops/obs/mask.py`, `tests/test_v2_alias_config.py`, `tests/test_agent_key.py`, `tests/test_costs.py`, `tests/test_audit_events.py`, `tests/test_compose.py`, `tests/test_gateway_client.py`, `tests/test_obs_mask.py`, `tests/test_docs_consistency.py`, `tests/test_makefile.py`, `tests/test_team_keys.py`, `tests/_cost_worker.py`, `tests/test_agent_finalize.py`, `tests/test_agent_graph.py`, `tests/test_api_smoke.py`, `tests/test_architecture.py`, `tests/test_costs_report.py`, `tests/test_costs_reservation.py`, `tests/test_eval_grading.py`, `tests/test_eval_human.py`, `tests/test_evalrun_dev.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults_smoke.py`, `tests/test_obs_instrument.py`, `tests/test_smoke.py`, `tests/test_smoke_gateway.py`, `tests/test_e2e_isolation.py`, `tests/test_gateway_hook_imports.py`, `tests/test_replay_llm.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다. 테스트는 임시 디렉터리·합성 데이터·테스트용 키만 쓴다. 기본 경로 문자열이 코드에 있는 것은 이 금지의 대상이 아니다.

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step의 변경을 시뮬레이션한 전체 `pytest`에서 깨진 테스트는 본문의 `테스트`·`검증` 절에 명시한 것뿐이며 명시가 없으면 깨진 테스트는 없었다. 다음 파일은 변경 대상 모듈·파일을 import·참조하는 소비자로 `수정 허용 경로`에 올려 두었다. **단언이 실제로 깨질 때만, 정확히 새 값을 반영하는 것에 한해** 수정한다. 정확 일치를 부분 집합·포함 비교로 바꾸지 않고, 깨지지 않는 파일은 수정하지 않는다: `tests/test_obs_mask.py`, `tests/test_docs_consistency.py`, `tests/test_makefile.py`, `tests/test_team_keys.py`, `tests/_cost_worker.py`, `tests/test_agent_finalize.py`, `tests/test_agent_graph.py`, `tests/test_api_smoke.py`, `tests/test_architecture.py`, `tests/test_costs_report.py`, `tests/test_costs_reservation.py`, `tests/test_eval_grading.py`, `tests/test_eval_human.py`, `tests/test_evalrun_dev.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults_smoke.py`, `tests/test_obs_instrument.py`, `tests/test_smoke.py`, `tests/test_smoke_gateway.py`, `tests/test_e2e_isolation.py`, `tests/test_gateway_hook_imports.py`, `tests/test_replay_llm.py`. 이 단락의 파일을 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: Guardrail v2의 분류 호출은 게이트웨이 별칭 `guardrail-v2`(Anthropic Haiku 5.5)로 간다. 이 step은 설정·키 허용 모델·단가 등록만 한다. 클라이언트 호출 코드는 step 2, 그래프 결선은 step 3이다. 이 이름 `guardrail-v2`·`anthropic-claude-haiku-5-5`·`CLASSIFIER_DEPLOYMENTS`·`classifier_cost_usd`의 기존 사용처는 없다.

### 1. LiteLLM 설정(두 파일을 같은 내용으로)

- `model_list`에 배포 하나를 **끝에** 더한다: `model_name: guardrail-v2`, `model_info.id: anthropic-claude-haiku-5-5`, `litellm_params`: `model: anthropic/claude-haiku-5-5`, `api_key: os.environ/ANTHROPIC_API_KEY`, `timeout: 4`, `num_retries: 0`.
- `router_settings.fallbacks`는 **바꾸지 않는다**(두 줄 그대로). 새 별칭은 fallbacks의 키와 값 어디에도 없어야 한다(분류기 장애가 OpenAI·Sonnet으로 넘어가면 안 된다).
- `config.obs.yaml`의 `litellm_settings`·주석, 두 파일의 `guardrails`·`general_settings`는 그대로 둔다.

### 2. 키 허용 모델

- `AGENT_KEY_MODELS`(`agent_key.py`) **끝에** `"guardrail-v2"`를 더한다. `team_keys.py`는 같은 객체를 쓰므로 수정하지 않는다.

### 3. 단가와 분류기 매핑(`costs.py`)

- `PRICES`에 `anthropic.claude-haiku-5-5.input`(0.10/1e6)·`anthropic.claude-haiku-5-5.output`(0.50/1e6)을 더한다. `source_url`은 `anthropic.claude-sonnet-5-5.*`와 같은 URL(`https://platform.claude.com/docs/en/about-claude/pricing`), `checked_on="2026-10-13"`. `cached_input` 단가는 두지 않는다.
- 별도 상수 `CLASSIFIER_DEPLOYMENTS: Mapping[str, str] = MappingProxyType({"anthropic-claude-haiku-5-5": "claude-haiku-5-5"})`와 함수 `classifier_cost_usd(*, input_tokens: int, output_tokens: int) -> float`를 더한다(`_tokens`로 검증, 음수·비정수 거부).
- **`REGISTERED_MODELS`·`DEPLOYMENT_MODELS`에는 Haiku를 넣지 않는다.** 이 둘은 에이전트 응답 모델 매핑과 "미등록은 최대 단가" 계산의 입력이다.

### 4. 관측 마스크(`obs/mask.py`)

- `DEPLOYMENT_IDS`에 `"anthropic-claude-haiku-5-5"`를 더한다(배포 ID를 trace 속성에서 보존하는 집합이며 `tests/test_obs_mask.py::test_deployment_identifier_registry`가 설정의 `model_info.id` 집합과 정확히 일치를 단언한다).

### 5. 테스트

- 새 `tests/test_v2_alias_config.py`: ① 두 설정 모두 `guardrail-v2` 배포가 정확히 1개이고 위 필드 값을 가진다. ② `router_settings.fallbacks`에 `guardrail-v2`가 키·값 어디에도 없다. ③ 두 설정의 `model_list`가 서로 같다. ④ `"guardrail-v2" in AGENT_KEY_MODELS`이고 `set(AGENT_KEY_MODELS)` = 두 설정의 `model_name` 집합. ⑤ `set(costs.DEPLOYMENT_MODELS)`가 변경 전 3개(`openai-gpt-6-luna`·`openai-gpt-6-luna-only`·`anthropic-claude-sonnet-5-5`)이고 `set(costs.REGISTERED_MODELS)`가 `{"gpt-6-luna", "claude-sonnet-5-5"}`다. ⑥ `costs.resolve_deployment("anthropic-claude-haiku-5-5").mapped`와 `costs.resolve_model("claude-haiku-5-5").mapped`가 모두 거짓이다. ⑦ `classifier_cost_usd(input_tokens=1_000_000, output_tokens=1_000_000)`가 `0.60`(근사)이고 음수 토큰은 `ValueError`다. ⑧ `mask.DEPLOYMENT_IDS`에 `anthropic-claude-haiku-5-5`가 있다.
- 기존 소비자(**~에 한함, 단언 약화 금지**). 변경을 시뮬레이션한 전체 `pytest`에서 아래 테스트가 깨졌다. 각각 정확히 이 변경만 반영한다:
  - `tests/test_agent_key.py::test_success`(4 파라미터): 핸들러가 단언하는 `models` 리터럴 목록 끝에 `"guardrail-v2"`를 더한다.
  - `tests/test_costs.py::test_prices_and_constants`: `len(costs.PRICES) == 10`으로 바꾸고 Haiku 두 항목의 단가·`checked_on`·`source_url`을 단언하는 줄을 더한다. 기존 단가 단언은 그대로 둔다.
  - `tests/test_audit_events.py::test_header_sets_and_model_aliases`: 별칭 집합 단언을 `{"agent-primary", "agent-fallback", "agent-primary-only", "agent-primary-down", "guardrail-v2"}`로 바꾸고, `model.call` 이벤트를 방출하는 순회는 **`guardrail-v2`를 뺀 4개**에만 한다(분류 호출은 `model.call` 이벤트가 아니고 `model` 필드 형식 `agent-…`을 따르지 않는다).
  - `tests/test_compose.py::test_litellm_has_primary_model_and_environment_settings`: `len(config["model_list"]) == 5`로 바꾼다. `model_list[0]` 단언은 그대로 둔다.
  - `tests/test_compose.py::test_gateway_fallback_configs_match`: `set(models)`에 `"guardrail-v2"`를 더하고 `fallbacks == expected`는 **그대로** 둔다. 새 별칭의 `models["guardrail-v2"]`가 `anthropic/claude-haiku-5-5`·`timeout 4`·`num_retries 0`·`api_key os.environ/ANTHROPIC_API_KEY`임을 단언하는 줄을 더한다.
  - `tests/test_gateway_client.py::test_deployment_configuration_ids`: `expected`에 `costs.CLASSIFIER_DEPLOYMENTS`의 키를 더한다(설정의 배포 ID 집합 = `DEPLOYMENT_MODELS` + down 배포 + 분류기 배포).
  - `tests/test_obs_mask.py::test_deployment_identifier_registry`는 위 4항(`obs/mask.py`)으로 통과해야 한다. 이 파일은 소비자 점검 대상이며 이 테스트가 통과하면 수정이 필요 없다.

**사용자 실행(이 step에서 실행하지 않는다)**: 머지 뒤 LiteLLM 컨테이너 재생성(`docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate --no-deps litellm`)과 `make agent-key-update`·`make team-keys-update`가 필요하다. 이 step에서는 설정·상수만 바꾼다.

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

- `router_settings.fallbacks`에 `guardrail-v2`를 키로도 값으로도 넣지 마라. 이유: 분류기 장애가 OpenAI 주 모델이나 Sonnet으로 넘어가면 v2 판정이 다른 모델의 응답이 된다(설계 0-1의 9, 2-4).
- `REGISTERED_MODELS`·`DEPLOYMENT_MODELS`·`required_deployments`에 Haiku 배포를 추가하지 마라. 이유: 에이전트 응답이 Haiku로 매핑되는 경로가 열린다. 분류기 매핑은 `CLASSIFIER_DEPLOYMENTS`에만 둔다.
- 기존 소비자 테스트의 단언을 부분 집합·포함 비교로 바꾸지 마라. 이유: 정확 일치를 정확히 새 값으로만 갱신한다(FP-071).
- `make agent-key-update`·`make team-keys-update`를 실행하지 마라. 이유: 실제 LiteLLM에 접속하는 사용자 실행 항목이다(FP-052).

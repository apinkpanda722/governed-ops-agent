# M6 phase ② m6-guardrail-v2 설계 초안 (개정 4: 호스트 수집 지점 + 게이트웨이 경유 호출)

기준: main `7412852`(clean). 이 문서는 untracked이며 코드·phase 파일·사전 등록 파일·커밋·브랜치·PR은 아직 없다. 실제 API는 호출하지 않았다. 상위 문서는 `docs/M6_DESIGN_DRAFT.md`(M6 전체 구성, 7절 정상 대조 test 40 규칙)다.

이력: 개정 3은 지연 측정(1절)으로 A 위치(LiteLLM 훅 안)를 기각하고 v2를 에이전트 수집 지점으로 옮겼다. 개정 4는 그 분류 호출을 **게이트웨이 경유**로 바꾼다. 개정 3의 "호스트가 `httpx`로 Anthropic 직접 호출"과 호스트 키 리더는 AGENTS.md:57("에이전트의 모델 호출은 `gateway` 클라이언트로만")·ADR-007("v2 분류기는 LiteLLM 경유")과 충돌해 철회했다. **AGENTS.md·ADR-007 문구는 바꾸지 않는다.**

## 요약

- **구분**: v1 = 게이트웨이 사전 훅(에이전트 경유 여부와 무관하게 **모든 호출**), v2 = 에이전트 수집 지점(**에이전트 그래프를 거친 호출만**)에서 분류하고 그 분류 호출은 **게이트웨이 별칭 `guardrail-v2`** 로 보낸다. v1이 최종 권한이고 v2는 v1에 더하기만 한다.
- **끼우는 지점**: 사용자 입력은 `graph.py:293` 앞(`model_body`, `steps == 0`), 도구 결과는 `graph.py:507` 앞(`read_tools_body`, 호출 루프 종료 직후). 판정은 메시지에 반영되어 이후 호출에서 다시 분류하지 않는다.
- **호출**: 기존 `gateway/client.py`의 `LiteLLMChatClient`에 분류 메서드를 더한다. 사용자의 **팀 키**로 별칭 `guardrail-v2`(→ `anthropic/claude-haiku-5-5`, router fallback 없음)를 부른다. 호스트는 `ANTHROPIC_API_KEY`를 읽지 않는다(공급사 키는 게이트웨이 컨테이너에만).
- **v1이 분류 요청도 검사한다**: 호스트는 분류기로 보낼 텍스트에서 PII 6종을 모두 마스킹하고 v1 격리분은 보내지 않는다. **사용자 입력 항목은 `role:user`, 도구 결과 항목은 `role:tool`(고정 가짜 assistant `tool_call` 1개 + `tool` 메시지)로** 보내 게이트웨이 v1이 주 모델 경로와 같은 scope 규칙을 적용하게 한다. 호스트가 같은 규칙으로 미리 거른 항목은 분류 요청에서 v1에 막히지 않으며 이 동치성을 결정론 테스트로 단언한다. 그래도 막히면(`injection_direct`·`pii_input`) 그 항목은 `unclassified` + 경보이며 원래 task를 차단하지 않는다.
- **비용**: 호스트 클라이언트가 `purpose=guardrail_v2`로 `CostLedger.reserve`·`settle`한다. 에이전트 응답 모델 매핑(`REGISTERED_MODELS`·`DEPLOYMENT_MODELS`)은 열지 않고 별도 매핑을 둔다.
- **변경되는 게이트웨이 설정**: `infra/litellm/config.yaml`·`config.obs.yaml`에 별칭 추가, `AGENT_KEY_MODELS`에 별칭 추가(키 갱신 필요), 훅 코드·헤더·`HOOK_FILES`는 불변(훅 `code` 해시 불변).
- **성립 기준(3절)** 은 지연을 이미 본 뒤에 쓴다. 측정이 끝난 호출 지연은 기준에서 빼고 아직 재지 않은 것만 둔다.
- 순서: 코드(step 0~5) → 사용자 live(재기동·키 갱신 포함) → smoke → (성립 시) 사전 등록 확정 → phase ③.

## 0. 외부 계약 실측

### 0-1. 실측한 사실

| # | 사실 | 근거 | 이 설계에 주는 의미 |
|---|---|---|---|
| 1 | `model_body`는 `steps >= MAX_STEPS`이면 마무리 호출 분기, 아니면 비용 상한 검사 뒤 `steps = steps + 1`을 하고 `span(MODEL_CALL)` 안에서 `chat.complete`를 부른다. 턴 시작은 `steps == 0`인 첫 호출 한 번이다 | `src/govops/agent/graph.py:283-296` | 사용자 입력 분류를 `:293` 앞에 둘 수 있다. 새 노드·새 상태 키가 필요 없다 |
| 2 | 턴 요청 원문은 `state["request"]`이다. 이전 턴은 `request`·`answer`만 이력으로 이월된다 | `src/govops/agent/turns.py:100-106,141,151` | 분류 대상은 `state["request"]` 하나다 |
| 3 | `read_tools_body`는 호출마다 `payload` → `content = json.dumps` → 20,000자 초과 시 잘라냄 → `messages.append` 순이고 루프 뒤 `update`를 반환한다 | `graph.py:491-497,507-513`, `TOOL_RESULT_MAX_CHARS`(`graph.py:67`) | 루프 직후 `:507` 앞에서 라운드의 도구 메시지를 모아 병렬 분류·치환한다 |
| 4 | 탐색 예산·`evidence_ids`·`observed_ids`는 원본 `payload`에서 만들어진다 | `graph.py:473-490` | 격리 치환이 근거 ID·탐색 예산 계산을 바꾸지 않는다 |
| 5 | 게이트웨이 차단 처리: 감사 `guardrail.input_blocked`(`code`, 선택 `rules_digest`·`code_digest`, `injection_direct`이면 `input_sha256`), 반환 `status="blocked_input"`·`error=code`·`guardrail_block={"code": …}`·`answer=None` | `graph.py:307-329`, `src/govops/audit/log.py:85-95,259-261` | 호스트 v2 차단이 같은 이벤트·상태·코드(`injection_direct`)를 쓴다 |
| 6 | API 차단 뷰와 격리 요약은 상태(`guardrail_block`, `quarantined_ids`)에서만 만든다 | `src/govops/api/tasks.py:305-326` | v2 차단·격리가 API·웹 변경 없이 보인다 |
| 7 | 도구 격리 감사 `guardrail.tool_quarantined`(`tool_call_sha256`, `count=1`)는 `state["quarantined_ids"]`로 task 단위 중복 제거한다. 이미 턴 상태 키다 | `graph.py:248-269`, `src/govops/agent/turns.py:30,70` | 같은 이벤트·중복 제거를 쓴다. 새 상태 키 없음 |
| 8 | 감사 필드 이름은 `[a-z][a-z0-9_]*`이고 `_FORBIDDEN`이 아니며, 값이 `int`·`bool`이면 통과한다. `code`는 `GUARDRAIL_CODES` 집합이어야 한다 | `audit/log.py:100-125,262-290` | 불리언 `v2=True`로 귀속을 남긴다. `GUARDRAIL_CODES`에 `guardrail_v2_error` 한 개만 더한다 |
| 9 | **fallback은 별칭별이다.** `router_settings.fallbacks`는 `agent-primary: [agent-fallback]`·`agent-primary-down: [agent-fallback]` 두 줄뿐이고, `default_fallbacks`·`context_window_fallbacks` 등 전역 키는 없다. 두 설정 파일이 같다 | `infra/litellm/config.yaml:36-39`, `infra/litellm/config.obs.yaml:36-39`, `rg fallback infra/litellm` | 새 별칭을 `fallbacks`에 넣지 않으면 분류기 장애가 OpenAI로 넘어가지 않는다 |
| 10 | 각 배포는 `model_name`·`model_info.id`·`api_key: os.environ/…`·`timeout`·`num_retries: 0`을 가진다. Anthropic 배포 키는 컨테이너 환경 변수다 | `infra/litellm/config.yaml:10-17`, `infra/docker-compose.yml:31` | 별칭 배포를 같은 형식으로 더한다. 공급사 키는 컨테이너에만 있다 |
| 11 | `AGENT_KEY_MODELS`(에이전트 키·팀 키 8개의 허용 모델 단일 출처)는 4개 별칭이다. smoke `allowed_models`는 이 목록을 순회해 `probe digest`로 모델별 접근을 확인한다(비용 없음) | `src/govops/gateway/agent_key.py:16-21,64,117`, `src/govops/gateway/team_keys.py:138,154,156,181`, `src/govops/gateway/smoke_guardrail.py:184-196` | 별칭을 더하면 키 갱신이 필요하고 `smoke-guardrail`이 새 별칭 접근을 자동으로 확인한다 |
| 12 | **client.py의 purpose 경로는 에이전트 호출에 고정이다.** `complete`는 `agent_primary`와 `agent_fallback` 두 예약을 잡고, 요청 본문 `model`은 `model_fault_alias`로 정해지며 `tools`를 항상 보낸다. `_send`는 클라이언트 전체 타임아웃(`timeout_s`, 기본 60s)과 `tools=body["tools"]`를 쓴다. 비용은 `response_cost_usd`(등록 모델만, 미등록은 최대 단가 + 경보)로 계산한다 | `src/govops/gateway/client.py:167-176,203-211,221-259,333-348,363-380,428-462` | 기존 경로로 `guardrail_v2`를 예약·정산할 수 없다. 최소 변경이 필요하다(2-6) |
| 13 | `PURPOSES`·`PURPOSE_SUPPLIER`에 `guardrail_v2`(anthropic)가 이미 있고, `reserve`·`settle`은 purpose로 공급사를 정한다 | `src/govops/costs.py:22-25,456-548` | 원장 코드는 변경 없다 |
| 14 | `PRICES`에 Haiku가 없고 `len(costs.PRICES) == 8`을 단언한다. `REGISTERED_MODELS`·`DEPLOYMENT_MODELS`는 에이전트 응답 매핑과 "미등록은 최대 단가" 계산의 입력이다 | `costs.py:40-79,130-146,193-212`, `tests/test_costs.py:45` | 단가 두 줄은 `PRICES`에만, 분류기 배포 매핑은 별도 상수에 둔다(2-6) |
| 15 | preflight는 `required_deployments()`(= `DEPLOYMENT_MODELS` 키 + down 배포)가 `/model/info`의 배포 ID 집합에 포함되지 않으면 `litellm_config_stale`이다 | `src/govops/evalrun/preflight.py:34-43,106-119`, `tests/test_evalrun_preflight.py:36` | 낡은 컨테이너에 분류기 배포가 없으면 조용히 v2가 꺼지므로 스위치 on일 때 분류기 배포 ID를 필수에 더해야 한다 |
| 16 | v1은 **`role:user`만** 인젝션·PII 6종 차단 대상으로 보고, `role:tool`은 마스킹·격리, `system`은 검사하지 않는다. 사전 훅은 모든 게이트웨이 요청(별칭 무관)에 돈다 | `src/govops/guardrails/screen.py:76-85,152-183`, `src/govops/gateway/guardrail_hook.py:26-72`, `infra/litellm/config.yaml:45-55` | 분류 요청도 v1을 거친다. 데이터 텍스트의 role을 원래 경로와 맞추면 같은 scope 규칙이 적용된다(0-3) |
| 17 | `screen_user_messages`는 `scope="input"`, `screen_tool_messages`는 `scope="tool"` 인젝션 규칙을 쓴다. 규칙 파일 내용은 열람하지 않으므로 두 범위의 차이는 **미실측**이다 | `screen.py:82,89` | 도구 결과를 `role:user`로 보내면 입력 범위 규칙을 받아 호스트의 도구 범위 선별과 어긋날 수 있다. role을 원래 경로와 맞추면 어긋나지 않는다 |
| 18 | 호스트는 사용자별 팀 키를 `key_resolver`로 고르고 `require_guardrail=True`·`expected_digests`로 응답 헤더를 검증한다 | `src/govops/api/wiring.py:173-179`, `client.py:177-183,295-305` | 분류 호출도 같은 키·헤더 검증을 쓴다. 호스트에 공급사 키가 필요 없다 |
| 19 | `agent`는 `govops.guardrails`를 import할 수 있다. `evalrun`은 못 한다. `gateway`는 `agent`·`tools`·`broker`·`api`를 못 쓴다 | `tests/test_architecture.py:35-70`, `src/govops/agent/checks.py:8-9` | v1 순수 판정은 `guardrails/`에서 호스트가 쓰고, 분류 호출은 `gateway/client.py`에 둔다 |
| 20 | `guardrails/`는 표준 라이브러리와 `govops.guardrails.*`만 import하고 모든 파일에 "redteam"·"data/eval"·"meta.json"·"data/replay"가 없어야 한다. `guardrails`·`gateway`·`agent`·`api`의 `.py`에는 "redteam"이 없어야 한다 | `tests/test_guardrails_boundary.py:12-37`, `tests/test_redteam_boundary.py:74-80` | 순수 로직은 `guardrails/semantic.py`에 두고 새 파일에 이 단어를 쓰지 않는다 |
| 21 | v1 공개 순수 함수: `screen_user_messages`(`screen.py:76`), `screen_tool_messages`(`screen.py:152`), `INPUT_PII_KINDS`(`screen.py:12`), `find_pii`·`mask_pii`(`pii.py:75`). `screen.py`·`injection.py`·`pii.py`·`rules_v1.json`은 `HOOK_FILES`에 있다 | `src/govops/guardrails/screen.py:12,76,152`, `src/govops/gateway/hook_digest.py:9-24` | 호스트는 이 함수들을 **수정 없이** 호출한다. 새 파일은 `HOOK_FILES`에 넣지 않는다(훅 `code` 해시 불변) |
| 22 | 기록 재생 클라이언트는 `ReplayChatClient`이고 `golden.py`와 `tests/e2e_backend.py:284`가 재생 서비스를 조립한다. 재생 키는 호출마다 `hash(messages, tools, max_output_tokens)`다 | `src/govops/replay/llm.py:219-232,26,242,262`, `src/govops/golden.py:31`, `tests/e2e_backend.py:284` | v2가 메시지를 치환한 live 기록은 스위치 off 재생에서 키가 어긋난다. 재생은 스위치 off 고정 규칙이 필요하다(2-9) |
| 23 | `build_task_service`는 "명시 주입된 의존성만 조립하고 모델·프로필 설정을 읽지 않는다"고 선언하며 프로덕션 호출은 `open_task_service`와 `evalrun/dev_m3.py:1217` 두 곳이다. `build_graph` 프로덕션 호출은 `wiring.py:86` 한 곳, 테스트 호출은 약 60곳이다 | `src/govops/api/wiring.py:44-55,86,173`, `src/govops/evalrun/dev_m3.py:1217` | 새 인자는 기본값 `None`이어야 하고 스위치는 `build_task_service` 바깥(두 호출부)에서 읽어 주입한다 |
| 24 | obs 설정만 `success_callback: ["langfuse"]`·`turn_off_message_logging: true`다. `existing_trace_id` 메타데이터로 호출이 task trace에 묶인다 | `infra/litellm/config.obs.yaml:45-54`, `client.py:192-193` | 분류 호출도 Langfuse generation으로 남는다(원문은 자리 표시자). smoke의 원문 부재 단계가 이 generation을 조회해야 한다 |
| 25 | 기존 `langfuse_no_raw_input` 단계는 v1이 차단한 요청의 trace만 본다 | `src/govops/gateway/smoke_guardrail.py:380-406` | v2 경로에는 새 단계가 필요하다 |
| 26 | `service.start` 감사 이벤트는 필수 필드 집합이 고정이다 | `audit/log.py` `_NEW_REQUIRED["service.start"]`, `tests/test_service_start_event.py` | 스위치 값은 여기에 넣지 않고 dev manifest·trace 속성에 기록한다 |
| 27 | 지연 측정은 **호스트 → `api.anthropic.com` 직접 호출**이고 게이트웨이 홉·v1 훅을 거치지 않았다 | 1절 | **측정값은 게이트웨이 경유 지연의 하한이다.** 홉 추가분은 미실측(3절 (c)·(b)가 본다) |

### 0-2. 미실측

| 항목 | 확인 방법 |
|---|---|
| 게이트웨이 경유(호스트→LiteLLM→Anthropic) 분류 호출의 지연·429 비율 | 3절 (b)·(c) smoke |
| 입력 범위(`input`)와 도구 범위(`tool`) v1 인젝션 규칙의 차이 → 분류 요청이 `injection_direct`로 막히는 비율 | 규칙은 열람하지 않는다. smoke 상태 집계의 `v1_blocked` 비율(3절 (b))로 본다 |
| 개정 프롬프트의 출력 규격 준수, `stop_reason` 값 | 3절 C4 |
| `anthropic/claude-haiku-5-5`가 LiteLLM v1.103.3 라우팅·응답 정규화에서 `usage`를 `prompt_tokens`·`completion_tokens`로 주는지 | step 2 가짜 SDK 응답은 형식 가정이다. 실제는 smoke `v2_ledger_settled` |
| `max_completion_tokens`·`stop` 전달 지원 | smoke C4 |
| **`role:tool` 분류 요청 형식**: Anthropic은 `tool_use`·`tool_result` 블록이 있는 요청에 `tools` 정의를 요구하는 것으로 알려져 있으나 LiteLLM v1.103.3 변환에서 필요한지 미실측이다. `tools`를 줄 때 모델이 라벨 대신 도구를 호출하려 하는지, `tool_choice: "none"`이 허용되는지도 미실측이다 | **C4 smoke가 첫 확인이다.** 이 형식이 API 오류를 내면 3절 C4 미충족 처리 규칙(새 등록 파일로 1회 개정)을 따른다 |

### 0-3. 분류 요청에 v1 규칙이 적용될 때의 충돌과 구성

분류 요청은 일반 게이트웨이 요청이므로 사전 훅이 그대로 적용된다(0-1의 16). v1은 `role:user` 메시지만 차단 대상으로 보고 `role:tool`은 마스킹·격리만 하며 `system`은 검사하지 않는다. **그래서 분류 대상의 role을 원래 경로와 맞춘다.**

| 분류 대상 | 분류 요청의 구성 | 게이트웨이 v1이 적용하는 규칙 |
|---|---|---|
| 사용자 입력 | `[system(고정 지시), user(대상 텍스트)]` | 호스트가 `state["request"]`에 적용한 것과 같은 `screen_user_messages`(`scope="input"`) |
| 도구 결과 항목 | `[system(고정 지시), user(고정 안내 문장), assistant(고정 `tool_call` 1개), tool(대상 텍스트)]` | 주 모델 경로의 도구 메시지와 같은 `screen_tool_messages`(`scope="tool"`, 마스킹·격리만, 차단 없음) |

- 대상 텍스트는 **호스트가 이미 v1 규칙으로 걸러 마스킹한 문자열을 그대로** 보낸다(래퍼·무작위 태그를 붙이지 않는다). 그래서 같은 항목에 대한 호스트 사전 선별 결과와 게이트웨이 `screen_request` 결과가 일치한다. 이 동치성을 결정론 테스트로 단언한다(step 1).
- 고정 `system`·`user` 안내 문장은 상수이며 `screen_request`를 통과(허용·무변화)해야 한다. 같은 테스트가 단언한다.
- 레드팀 공격 문장에서 `v1_blocked`로 빠질 항목을 줄이기 위한 선택이며, **레드팀 결과를 본 뒤 구성을 바꾸는 것은 사전 등록 위반**이라 이 구성을 지금 기본으로 확정한다.

| 남는 충돌 | 상황 | 처리 |
|---|---|---|
| 금지 PII | 마스킹 누락으로 6종 PII가 남으면 사용자 역할 요청이 `pii_input`으로 400 | 호스트가 보내기 전에 6종을 모두 마스킹한다. 그래도 막히면 `unclassified`(상태 `v1_blocked`) + 경보 |
| 직접 인젝션 | 동치성이 깨지는 경우(규칙 비결정·호스트와 게이트웨이의 규칙 파일 불일치) | 같은 처리. 동치성 테스트가 현재 코드에서의 일치를 보증한다 |
| 의미 중복 | 막힌 분류 요청이 호스트 감사에 `guardrail.input_blocked`로 남으면 원래 task가 막힌 것으로 오인된다 | `classify`는 `GuardrailBlocked`를 **삼키고** 그래프에 올리지 않는다. 감사는 `guardrail.error`(`guardrail_v2_error`)만 쓴다 |
| 헤더 집계 오염 | 분류 응답 헤더의 `q`·`m`·`ids`가 task 집계에 섞임 | 헤더는 정합 검사(`require_guardrail`·해시)에만 쓰고 `ChatResult.guardrail`을 그래프에 넘기지 않는다 |
| tool 형식 | `tool_use` 이력이 `tools` 정의를 요구하거나 모델이 도구를 부르려 함 | 0-2 미실측. 요청에 고정 도구 정의 1개와 `tool_choice: "none"`을 포함하는 형태를 기본으로 하고 smoke C4가 확인한다 |

## 1. 측정 기록과 위치 결정

### 1-1. 측정 원문

호스트에서 `api.anthropic.com`을 직접 호출했다(`claude-haiku-5-5`, 크기별 20회, 순차, `max_tokens=8`, `temperature` 미지정, 합성 한국어 입력, 시스템 지시는 자리표시 문구). 사용자 실행 결과 그대로:

```text
short chars= 200 p50=1.15s p95=1.33s max=1.35s >1.2s=6/20 status={200: 20} mean_in=1064tok mean_out=8.0tok cost=$0.00221
p50   chars=1200 p50=1.18s p95=1.42s max=1.53s >1.2s=9/20 status={200: 20} mean_in=2054tok mean_out=7.8tok cost=$0.00419
max   chars=2400 p50=1.10s p95=1.43s max=2.58s >1.2s=3/20 status={200: 20} mean_in=3235tok mean_out=7.5tok cost=$0.00655
temperature_probe status=400
total_cost=$0.01294 (원장 purpose=guardrail_v2 4행, 미커밋 — 사전 등록 PR에 함께 커밋)
```

### 1-2. 판정 기록 (개정 1의 기준·처리 규칙을 그대로 적용, 측정 뒤 바꾸지 않음)

| 기준 | 결과 | 판정 |
|---|---|---|
| C1 크기별 p50 ≤ 0.6s, p95 ≤ 1.0s | p50 1.10~1.18s, p95 1.33~1.43s | **미충족**(p50 > 0.9s → "A 불성립" 행) |
| C2 60호출 중 1.2s 초과 ≤ 3건 | 18/60 | **미충족** |
| C3 비-200 0건 | 60/60이 200 | 충족 |
| 처리 | 대안 ①(호스트 측 호출)·②(사후 비동기)·③(v2 제외) 중 **① 선택** | |

- 지연은 입력 길이와 거의 무관하다(200자 p50 1.15s, 2,400자 p50 1.10s). 고정 왕복·첫 토큰 시간이 지배하므로 입력 상한 축소는 해법이 아니다.
- `mean_in` 1,064 → 2,054 → 3,235토큰: 1,000자당 약 1,000토큰, 고정분(시스템 지시 자리표시 문구) ≈ 860토큰. 입력의 대부분이 시스템 지시다(8절 비용 계산에 사용).
- `mean_out` 7.5~8.0은 `max_tokens=8` 상한에 도달했다. 한 단어 출력 규격을 지키지 않았을 가능성이 높다(응답 본문은 기록하지 않아 확인 불가). 프롬프트를 고치고 출력 규격(C4)을 핵심 기준으로 둔다.
- `temperature=0`은 **거부**(400)다. 비결정성을 한계로 기록한다(`temperature`를 지정하지 않는다).
- 측정 원장 4행은 사전 등록 PR에 함께 커밋한다(`scripts/exempt_paths.json`이 원장을 청결 검사 예외로 두지만, 커밋하지 않으면 첫 step 커밋의 `git add -A`가 섞어 담는다: `scripts/execute.py:222-280`).

### 1-3. 기각한 안

- **A 위치(훅 안)**: 훅 통합(두 번째 헤더, 전용 풀, 안쪽 타임아웃, v2 probe)을 설계했으나 단일 호출 p50 1.1~1.2s가 사전 훅 예산(`PRE_CALL_TIMEOUT_S = 2.0`, `guardrail_hook.py:21`)의 약 60%이고 모든 모델 호출에 붙는다. 안쪽 타임아웃 1.2s에서 `>1.2s`가 18/60이라 정상 응답을 자주 끊는다. 폐기했다.
- **호스트 직접 호출**(개정 3): AGENTS.md:57·ADR-007과 충돌하고 호스트가 공급사 키를 갖게 되어 철회했다.

## 2. 설계 (호스트 수집 지점 + 게이트웨이 경유 분류)

### 2-1. v1과 v2의 구분

| | v1 | v2 |
|---|---|---|
| 위치 | LiteLLM 사전 훅(`gateway/guardrail_hook.py`) | 에이전트 수집 지점(`agent/graph.py`)에서 분류를 판정하고 호출은 `gateway/client.py`가 한다 |
| 적용 범위 | 게이트웨이로 들어오는 **모든** 호출(팀 키 직접 호출 포함, 분류 요청 포함) | **에이전트 그래프를 거친** 사용자 입력·도구 결과만 |
| 판정 | 정규식·패턴(결정론) | 소형 분류 모델(확률적, `guardrail-v2` 별칭) |
| 권한 | 최종 권한(차단·격리·마스킹) | v1에 더하기만, v1 결과를 줄이지 못함 |
| 장애 | 사전 fail-closed | 오류 시 v1만 남기고 통과 + 경보, fallback 없음 |

호스트의 v1 순수 판정은 **v2 입력 선별용**이다. 게이트웨이 v1이 여전히 모든 요청(분류 요청 포함)에 적용된다.

### 2-2. 끼우는 지점

| 대상 | 지점 | 동작 |
|---|---|---|
| 사용자 입력 | `src/govops/agent/graph.py:293` 앞(`model_body`, 비용 검사 뒤, `span(MODEL_CALL)` 앞), 조건 `state["steps"] == 0 and not finalizing` | `state["request"]`를 v1 순수 판정 → 통과분만 분류 → `INJECTION`이면 모델 호출 없이 `blocked_input` 반환(`graph.py:307-329`와 같은 이벤트·상태) |
| 도구 결과 | `src/govops/agent/graph.py:507` 앞(`read_tools_body`, 호출 루프 종료 직후, `update` 구성 전) | 이번 라운드의 도구 메시지를 모아 v1 순수 판정 → 통과분 항목을 병렬 분류 → `INJECTION` 항목은 해당 문자열을 `[정책으로 일부 제외]`로 치환한 뒤 `messages`(이미 `:495`에서 들어간 항목)를 갱신하고 `update`에 반영 |

- 노드를 더하지 않는다(그래프 토폴로지·골든 trace·재생 불변). 스위치가 off이면 두 지점 모두 아무것도 하지 않는다.
- 판정 결과(치환)는 `messages`와 체크포인트에 남아 이후 모델 호출에서 다시 분류하지 않는다.
- `FINALIZE_NOTICE`는 그래프가 직접 넣는 문구라 분류 대상이 아니다. 에이전트가 생성한 오류 안내 tool 메시지도 대상이 아니다.

### 2-3. 항목 선별과 전송 텍스트 (결정 4 유지)

1. 도구 메시지: 호스트가 `content`(잘린 뒤 문자열)에 `screen_tool_messages`(게이트웨이와 같은 함수)를 적용해 v1 변환 뒤 텍스트를 얻는다. 사용자 입력: `screen_user_messages([{"role": "user", "content": request}])`.
2. 사용자 입력이 v1 차단이면 v2를 호출하지 않는다. 호스트는 막지 않고 정상 경로로 두어 게이트웨이가 기존대로 막는다(기존 감사·상태 불변).
3. 항목: 변환 뒤 `content`가 JSON이면 문자열 값 중 **40자 이상이고 식별자 형식(`syn-…`·티켓 ID·UUID·날짜)이 아니며 `[정책으로 일부 제외]`만으로 이루어지지 않은** 값. JSON이 아니면(잘려서 깨진 경우 포함) 본문 전체가 항목 하나다.
4. **전송 텍스트에서 PII 6종(`INPUT_PII_KINDS`)을 모두 `mask_pii`로 마스킹한다**(v1의 비-JSON 본문 마스킹은 3종뿐이므로 분류 전용으로 더 엄격히 한다). v1이 격리한 문장은 이미 마커로 바뀌어 빠진다. 마스킹·격리 결과는 **전송 선별에만** 쓰고 메시지에는 쓰지 않는다(게이트웨이가 같은 변환을 다시 한다).
5. 항목 상한: 길이 2,500자(앞 1,600 + 뒤 900), 라운드당 최대 16개(동시 8개, 2-4). 초과 항목은 이번 라운드에서 분류하지 않고(`unclassified`, 경보) 잔여 위험으로 둔다.

### 2-4. 분류 호출 (게이트웨이 경유)

- **별칭**: `guardrail-v2` → `anthropic/claude-haiku-5-5`. `infra/litellm/config.yaml`·`config.obs.yaml`에 같은 `model_list` 항목을 더한다(`model_info.id: anthropic-claude-haiku-5-5`, `api_key: os.environ/ANTHROPIC_API_KEY`, `timeout: 4`, `num_retries: 0`). **`router_settings.fallbacks`에는 넣지 않는다**(0-1의 9). 분류기 장애가 OpenAI(`agent-primary`)나 Sonnet으로 넘어가지 않는다.
- **호출자**: `LiteLLMChatClient`(`client.py`)에 메서드 `classify(text, kind, task_id, user_id)`를 더한다. 사용자의 팀 키(`key_resolver(user_id)`), `require_guardrail`·`expected_digests` 검증, `metadata`(`task_id`, `existing_trace_id`, `generation_name="guardrail-v2"`, `parent_observation_id`)를 기존 `complete`와 같이 쓴다. `tools`는 보내지 않는다.
- **요청 형태**(0-3의 표): 사용자 입력은 `[system, user(대상 텍스트)]`, 도구 결과는 `[system, user(고정 안내), assistant(고정 tool_call 1개), tool(대상 텍스트)]`와 고정 도구 정의 1개·`tool_choice: "none"`. `max_completion_tokens` 16(제안), 재시도 없음(`_send`의 `max_retries=0`), 호출별 타임아웃 **4.0s(연결 2.0s)** — 클라이언트 기본 60s를 이 호출에서만 덮어쓴다. 요청 메시지 구성은 순수 함수(`guardrails/semantic.py`)가 만들고 클라이언트는 보내기만 한다.
- **타임아웃 근거**: 호스트 직접 호출 측정의 최대 2.58s(p95 1.33~1.43s)의 약 1.55배다. 게이트웨이 홉과 v1 훅(dev3 실측 p50 3.0ms·p95 20.7ms, `docs/results/2026-10-08-m4-dev3-live.md:57`)이 더해지므로 4.0s는 측정값보다 빠듯해질 수 있다(0-1의 27). (b)가 `timeout` 비율을 본다.
- **병렬**: 라운드 안 항목은 `asyncio.gather`로 동시 최대 8개(`Semaphore(8)`), 라운드당 16개 상한이므로 최악 2파(약 8s)다. 사용자 입력은 항목 1개다.
- **상태 분류**(`ok`·`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`): 정상 응답이 두 어휘 중 하나면 `ok`, `APITimeoutError`는 `timeout`, HTTP 429는 `rate_limited`, `GuardrailBlocked`(분류 요청이 v1에 막힘)는 `v1_blocked`, 그 밖의 연결 오류·HTTP 4xx/5xx(403 `key_model_access_denied` 포함)·`GuardrailUnavailable`은 `error`, 어휘 밖·`stop_reason`이 `length`면 `parse`. `ok`가 아닌 것은 모두 `unclassified`다.
- 출력 해석은 앞뒤 공백 제거 후 두 어휘 정확 일치만 라벨이다(긴 문장 안의 단어를 차단으로 해석하지 않는다).
- 결과 전달: 메서드는 `(label | None, status, usage, cost)`만 돌려주고 응답 헤더·원문은 올리지 않는다(0-3).

### 2-5. 판정 반영

| 상황 | 동작 |
|---|---|
| 사용자 입력 `INJECTION` | 모델 호출 없이 task를 `blocked_input`(`error="injection_direct"`, `guardrail_block={"code": "injection_direct"}`, `answer=None`, `steps` 불변)으로 끝낸다. 감사 `guardrail.input_blocked`(`code="injection_direct"`, `input_sha256=sha256(request)`, `v2=True`). API·웹은 기존 `blocked_input` 뷰 그대로 |
| 도구 항목 `INJECTION` | 그 문자열 값을 `[정책으로 일부 제외]`로 치환(항목 전체). 해당 `tool_call_id`의 sha256을 `quarantined_ids`에 더하고 중복이 아니면 감사 `guardrail.tool_quarantined`(`tool_call_sha256`, `count=1`, `v2=True`)를 쓴다. 감사 실패는 방어 결과를 바꾸지 않고 `guardrail.audit_gap` 속성만 남긴다(`graph.py:228-237`) |
| 분류 성공 `SAFE` | 변경 없음 |
| `unclassified`(상태 5종 모두) | v1만 남기고 통과. 감사 `guardrail.error`(`code="guardrail_v2_error"`)(2-7). **원래 task는 막히지 않는다** |

- 단조성(테스트): 호스트는 v1 변환 결과를 메시지에 쓰지 않으므로 v2는 원본에 마커만 더한다. v1 결과를 줄이는 경로가 없다.
- 항목 단위 격리는 오탐 비용이 크다. 문장 단위 격리는 라벨 한 줄 출력 규칙과 양립하지 않아 하지 않는다.

### 2-6. 비용 기록 (기존 경로로 안 되는 부분의 최소 변경)

기존 `complete` 경로는 `agent_primary`·`agent_fallback` 예약과 에이전트 응답 매핑에 고정이다(0-1의 12). 분류 메서드는 **별도의 짧은 경로**로 같은 `CostLedger.reserve`·`settle`을 부른다.

| 변경 | 위치 | 내용 |
|---|---|---|
| 분류 메서드 | `src/govops/gateway/client.py`(`complete` 아래, 새 메서드) | `reserve(purpose="guardrail_v2", model="claude-haiku-5-5", task_id, estimate_usd)` → 호출 → `settle`. 추정 상한은 `(2 × 입력 바이트 + 1,024)` 토큰 × 입력 단가 + 출력 상한. 정산: 정상 응답은 `usage`, 응답 없음(타임아웃·연결 오류)은 예약액, 429·4xx·`v1_blocked`는 0 |
| 전송 인자 | `client.py:363-380`(`_send`) | `tools` 인자와 호출별 `timeout`을 선택 인자로 바꾼다. 기존 호출은 인자 기본값이 기존 동작이다 |
| 비용·매핑 | `src/govops/costs.py`(`PRICES` 아래) | `anthropic.claude-haiku-5-5.input`·`.output`을 `PRICES`에만 더한다. `CLASSIFIER_DEPLOYMENTS = {"anthropic-claude-haiku-5-5": "claude-haiku-5-5"}`와 `classifier_cost_usd(...)`를 **별도 상수·함수**로 둔다 |
| 단가 출처 | `PRICES` | `source_url="https://platform.claude.com/docs/en/about-claude/pricing"`(`costs.py:62`와 같음), `checked_on="2026-10-13"` |

**에이전트 응답 매핑을 열지 않는 방법과 테스트**

- `REGISTERED_MODELS`·`DEPLOYMENT_MODELS`에 Haiku를 **넣지 않는다**. 분류 비용은 `CLASSIFIER_DEPLOYMENTS`로만 해석한다.
- 테스트(step 0): ① `set(DEPLOYMENT_MODELS)`·`set(REGISTERED_MODELS)`가 변경 전과 같다. ② `resolve_deployment("anthropic-claude-haiku-5-5").mapped is False`, `resolve_model("claude-haiku-5-5").mapped is False`(에이전트 경로는 Haiku를 알지 못함). ③ `required_deployments()`(preflight)에 분류기 ID가 스위치 off에서는 들어가지 않는다.
- 테스트(step 2): 에이전트 `complete`의 응답 헤더가 `anthropic-claude-haiku-5-5`이면 기존대로 미등록 경고 + 최대 단가 정산(변경 없음). 분류 메서드가 응답 배포 ID `anthropic-claude-sonnet-5-5`(등록된 에이전트 배포)를 받으면 **분류기 매핑 밖**이므로 `error`로 처리한다(별칭이 의도한 배포가 아님).
- 이로써 에이전트 응답이 Haiku로 매핑되어 fallback 정산 등이 잘못 열리는 경로가 없다.
- 예약이 상한에 걸리면 `BudgetExceededError` → task `failed`/`budget_exceeded`(`graph.py:334-343`의 기존 처리). v2를 조용히 끄지 않는다.

### 2-7. 오류 경보

`guardrail.error` 이벤트에 새 코드 `guardrail_v2_error` 하나를 `GUARDRAIL_CODES`에 더한다(`audit/log.py:85-95`). 라운드·사용자 입력마다 최대 1건(`count`=실패 항목 수). 원문·점수·상태 문자열은 기록하지 않는다(상태 분포는 trace 속성과 manifest에만).

### 2-8. 관측

- 호스트는 `span(SpanKind.GUARDRAIL, "semantic")` 안에서 분류하고 속성에는 **상태 건수(ok·timeout·rate_limited·error·parse·v1_blocked)·라벨 건수·토큰 합·ms**만 둔다. 분류 입력 원문·응답 본문은 속성으로 두지 않는다.
- 분류 호출은 게이트웨이를 거치므로 obs 설정에서 **Langfuse generation으로 남을 수 있다**(`config.obs.yaml:45-54`, `turn_off_message_logging: true`로 내용은 자리 표시자, 호출은 `existing_trace_id` = task ID로 task trace에 묶임). 내용 가림은 전역 설정에 의존한다.
- **새 smoke 단계 `v2_no_raw_in_trace`**: v1을 통과하는 sentinel이 든 사용자 입력·도구 텍스트로 task를 실행한 뒤 ① 호스트 JSONL trace 전체, ② obs가 켜진 경우 Langfuse `GET /api/public/traces/{task_id}`의 모든 observation(분류 generation `guardrail-v2` 포함)과 최근 trace 목록에서 sentinel이 없음을 확인한다. 기존 `smoke_guardrail`의 `langfuse_no_raw_input`(`:380-406`)은 건드리지 않는다. trace를 못 찾으면 `INCONCLUSIVE`, obs 꺼짐은 `SKIP`(기존 규칙과 같음).
- 소비자: `tests/test_obs_instrument.py:33,85,105,165,201`, `src/govops/api/smoke.py:46,422`(obs 비밀 목록 — 호스트가 공급사 키를 읽지 않으므로 갱신 불필요).

### 2-9. 스위치·재생 규칙·실행 산출물

- `GOVOPS_GUARDRAIL_V2=on|off`(기본 off, 빈 문자열은 off, `env_or`, FP-030)는 **호스트만** 읽는다. 컨테이너·`docker-compose` 변경 없음. 읽는 곳은 `open_task_service`와 `evalrun/dev_m3.py:1217` 두 호출부이며 `build_task_service`에는 선택 인자 `semantic`(기본 `None`)로 주입한다(`build_task_service`는 설정을 읽지 않는다는 선언을 유지, `wiring.py:55`).
- **규칙: 기록 재생·CI는 스위치 off 고정.** `ReplayChatClient.__init__`(`replay/llm.py:220`)이 스위치 on이면 `ValueError("guardrail_v2_replay")`로 시작을 거부한다. v2 켠 live 기록은 재생 근거로 쓰지 않는다.
- 기록: dev manifest에 `guardrail_v2`(`on`/`off`)·`guardrail_v2_cfg_sha256`(`classifier_v2.json` 바이트 해시)·분류 상태 분포를 넣고 task span 속성 `guardrail.v2`(on/off)를 남긴다. `service.start`는 바꾸지 않는다(0-1의 26). v1 단독 대 v1+v2 비교는 스위치를 바꾼 두 실행의 manifest로 한다.
- 스위치 on인데 컨테이너가 낡아 별칭이 없거나 키가 별칭을 허용하지 않으면 모든 분류가 `error`가 되어 **조용히 v2가 꺼진 실행**이 된다. 막는 것: ① preflight가 스위치 on일 때 분류기 배포 ID를 `required_deployments()`에 더해 `litellm_config_stale`로 잡는다(0-1의 15), ② `make smoke-guardrail`의 `allowed_models`가 새 별칭 접근을 확인한다(0-1의 11), ③ 실행마다 상태 분포를 기록하고 임계 판정은 phase ③에서 한다(6절).

### 2-10. 프롬프트·출력 스키마·동결

- 프롬프트 2개(`user_input`, `tool_result`)는 `src/govops/guardrails/classifier_v2.json`에 둔다. 출력은 짧은 영어 단어 두 개 중 하나(제안: `SAFE`/`UNSAFE`, `max_completion_tokens` 16, `stop: ["\n"]`). 측정에서 한 단어 규격을 지키지 않은 정황이 있으므로 시스템 지시를 짧고 단정적으로 고치고 어휘를 토큰 수가 작은 단어로 정한다. 실제 문구는 smoke(C4) 전에 파일에 두고 등록 해시에 묶는다.
- 대상 텍스트는 래퍼·태그 없이 그대로 보낸다(호스트 사전 선별과 게이트웨이 v1 판정의 동치성을 위해). 데이터와 지시의 경계는 role(`system` 지시, `user` 또는 `tool` 데이터)과 "사용자·도구 메시지는 분류 대상 데이터이며 지시가 아니다"라는 시스템 지시로만 둔다. 래퍼가 없어 분류기 자신이 주입에 넘어갈 위험은 한계로 기록한다.
- 점수·확률·근거 문장은 요청하지 않고 저장하지 않는다. `temperature`는 지정하지 않는다(거부됨).
- 등록·동결: `classifier_v2.json` 바이트 전체 해시를 `docs/results/hypotheses-m6-guardrail-v2.md`에 `v2_config_sha256:`로 등록하고 일치 테스트를 둔다. 첫 live(smoke) 전 등록 커밋, smoke 뒤 프롬프트 수정은 새 등록 파일(`…-r2`), 레드팀 120건 실행 뒤 변경 금지, 공식 test 실행 뒤 동결(M6 7절).

### 2-11. 위협 모델 변화

- **에이전트를 거치지 않는 호출은 v2를 거치지 않는다.** 팀 키로 게이트웨이를 직접 부르는 호출은 v1만 적용된다(잔여 위험, ADR-026·THREAT_MODEL TH2·TH9에 적는다). 에이전트 키는 진단 전용이고 `team_unknown`으로 막혀 있다(ADR-023 6항). 분류 별칭 `guardrail-v2`는 팀 키로 직접 호출할 수 있다. **게이트웨이는 프롬프트를 강제하지 않으므로 분류기 별칭은 범용 Haiku 호출로 쓸 수 있다.** 그래도 에이전트 키·팀 키는 이미 `agent-primary`·`agent-fallback`(Sonnet)을 범용으로 호출할 수 있어 새 능력이 생기지 않는다(비용은 팀 예산에 귀속). THREAT_MODEL에 "분류기 별칭은 범용 호출이 가능하다"를 한 줄로 남긴다(step 5).
- **이력 이월 한계(재서술)**: 호스트는 새 입력(턴 요청, 수집 직후 도구 결과)만 분류하고 치환을 메시지·체크포인트에 남긴다. 이력을 다시 분류하지 않으므로 판정 뒤집힘으로 대화가 영구 차단되는 경로가 없다. 남는 한계: ① 스위치 off 상태나 `unclassified`로 처리된 턴의 내용은 이후에도 v2 판정 없이 이월된다. ② 이전 턴 내용은 그 시점의 판정을 그대로 가진다. dev·test는 단일 턴이라 평가 범위 밖이다.
- **라운드당 항목 16개 초과**·분류 실패·`v1_blocked`일 때 그 호출은 v1만 적용된다. 피해 지점은 계속 결정론 검사와 승인이다(ADR-008).
- **데이터 흐름 변화**: Anthropic으로 나가는 내용이 fallback 호출분에서 **v1을 통과한 모든 에이전트 요청의 사용자 입력·도구 결과(PII 6종 마스킹 뒤)** 로 늘어난다. 호스트→LiteLLM→Anthropic 경로이며 호스트는 공급사 키를 갖지 않는다. ARCHITECTURE 데이터 흐름·THREAT_MODEL 2절 다이어그램·TH10·EVAL 공급사 표(step 5)에 적는다.
- **원문 노출 경로**: 분류 요청 본문은 LiteLLM을 거치므로 Langfuse generation으로 남을 수 있고 내용 가림은 obs 전역 설정(`turn_off_message_logging`)에 의존한다(2-8, smoke로 확인).

## 3. 성립 기준 (개정)

**이 기준은 지연을 이미 본 뒤에 쓴다.** 호출 지연(1절)은 측정이 끝났으므로 기준으로 쓰지 않는다(그 값은 타임아웃 제안의 근거일 뿐이다). 아래는 **아직 재지 않은 것**만이다. 수치는 승인된 값이며 코드 머지 뒤 smoke 전에 사전 등록 문서에 확정한다.

| 기준 | 내용 | 측정 |
|---|---|---|
| **C4(핵심)** 출력 규격 | 개정 프롬프트로 합성 정상 20문장 + 합성 인젝션형 5문장을 **두 형식(`role:user` 사용자 입력형, `role:tool` 도구 결과형) 모두**로 보내 응답이 두 어휘 중 하나로 **정확 일치하고 `stop_reason`이 정상 종료인 것 각 25/25**(규격 준수만 본다, 정확도 주장 아님). `role:tool` 형식이 API 오류를 내는 것도 미충족이다 | smoke |
| (b) 분류 완료율 | smoke의 분류 시도 ≥ 100건에서 상태 `ok` 비율 **≥ 95%**. 나머지(`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`)는 상태별로 함께 보고한다 | smoke 상태 분포 |
| (c) task당 추가 지연 | v2 span 시간 합(Δ) **p95 ≤ 8.0s**(≥ 20 task, 사용자 입력 1회 + 도구 라운드 최대 4회). 근거: 측정 단일 호출 p95 1.43s × (1 + 4) = 7.2s에 여유 0.8s | smoke의 trace span |
| (d) C6 비용 | smoke 실측 항목당 평균 정산액으로 계산한 최대 시나리오(항목 16 + 입력 1, 300 task)가 Anthropic 잔여 한도(`$11.47 − 원장 현재액`)의 **50% 이하** | 원장 + 8절 |

(앞 개정에는 "(c) p95 8.0s 초과 12.0s 이하는 범위 축소, 12.0s 초과는 제외"라는 이중 임계가 있었다. 12.0s는 범위 축소 안과 제외 안을 가르는 상한이었고, 확정에 따라 기준에서 뺐다. (c)는 p95 ≤ 8.0s 하나다.)

**처리 규칙(결과를 보기 전 고정)**

| 결과 | 처리 |
|---|---|
| C4 미충족(`role:tool` 형식의 API 오류 포함) | 프롬프트·어휘·요청 형식(고정 도구 정의·`tool_choice`)을 새 등록 파일(`…-r2`)로 고쳐 smoke를 **1회만** 다시 한다. 다시 미충족이면 v2를 공식 구성에서 제외한다(ADR-013 ②, 사용자 확인) |
| (b) 미충족 | 원인을 상태별로 본다. `rate_limited`가 지배적이면 동시 호출 8 → 4로 줄여 1회 재측정, `timeout`이 지배적이면 타임아웃을 6.0s까지 올려 1회 재측정, `v1_blocked`가 지배적이면 구성은 바꾸지 않고(0-3, 레드팀 결과 전 확정) 비율을 보고해 동치성 테스트의 누락 사례를 조사한다. 그래도 미충족이면 v2 제외 검토 |
| (c) 미충족 | 적용 범위를 사용자 입력만 또는 도구 결과만으로 줄이는 안을 사용자가 고른다. 범위 축소 시 등록 문서를 새 파일로 고친다. 그래도 미충족이면 v2 제외 |
| (d) 미충족 | 라운드당 항목 상한·항목 길이 상한을 줄여 재계산, 그래도 미충족이면 v2 제외 검토 |
| 모두 충족 | 성립. phase ③으로 |

- 기준을 본 뒤 바꾸지 않는다. 재측정은 위 표의 조건 변경에 한하고 통과·실패를 가리지 않고 사전 등록 문서의 "측정 기록" 절에 모두 남긴다.
- 이 phase의 판단 근거는 규격·완료율·지연·비용뿐이다. 차단율·오탐률은 phase ③에서 본다.

## 4. 사용자 실측 (코드 머지 뒤, 사용자 실행)

앞 개정의 지연 명령은 이미 실행되었다(1절). 진행 순서는 다음과 같다.

0. phase(`m6-guardrail-v2`) 머지 → (조언 세션 검토) → **`classifier_v2.json` 최종 문구 확정과 `v2_config_sha256` 등록 PR**(코드는 문구·해시만, 프롬프트 문구는 이 단계에서 따로 검토받는다) → 아래 live → 결과 PR.

아래 1~7은 사용자 live 순서다. 설정 파일이 바뀌므로 **컨테이너 재생성과 키 갱신이 필요하다**(FP-052).

1. LiteLLM 재생성(바인드 마운트 설정은 내용만 바뀌면 compose가 재생성하지 않는다): `docker compose --env-file .env -f infra/docker-compose.yml up -d --force-recreate --no-deps litellm`. obs 구성을 쓰면 `make obs-up`이 litellm을 항상 재생성한다.
2. `make agent-key-update`(에이전트 키 허용 모델 갱신, `.env` 키 값 유지)와 `make team-keys-update`(팀 키 8개 허용 모델 갱신).
3. 기존 smoke 회귀: `make smoke-guardrail`(`allowed_models`가 새 별칭 접근을 확인, 훅 해시 불변), `make smoke-fallback`(fallback 설정 불변 확인).
4. `.env`에 `GOVOPS_GUARDRAIL_V2=on`을 두고 `make serve`를 재시작한다. `ANTHROPIC_API_KEY`는 호스트에 필요 없다(컨테이너에만).
5. `make smoke-semantic`(신규 타깃, 실제 API 호출): 단계 `v2_off_switch`(off일 때 분류 호출 0건), `v2_alias_access`(별칭 접근), `v2_format`(C4 25문장), `v2_user_block`·`v2_tool_quarantine`(합성 인젝션형 입력·도구 텍스트에 분류기가 `unsafe`를 돌려주면 `blocked_input`·치환, `safe`이면 FAIL이 아니라 `INCONCLUSIVE(label_safe)` — 배선은 step 3 결정론 테스트가 담당), `v2_v1_blocked_passthrough`(task를 실행하지 않고 `classify`를 직접 1회 호출: 선별을 거치지 않은 형식만 맞춘 합성 PII 사용자형 요청이 `v1_blocked`·예외 없음·정산 0. 실제 task에서는 호스트 사전 선별이 게이트웨이 규칙과 같아 `v1_blocked`가 생기지 않으며 "v1에 막혀도 task가 이어진다"는 step 3 테스트가 담당), `v2_ledger_settled`(원장 `guardrail_v2` 행 증가·토큰 > 0), `v2_latency`((c)), `v2_ok_rate`((b), 상태 분포 출력), `v2_no_raw_in_trace`.
6. `make obs-up` 후 `make smoke-semantic` 재실행(Langfuse 단계 활성). 기존 `make smoke-obs`로 회귀를 확인한다.
7. `make cost-report`.

## 5. phase 구성

phase 이름 `m6-guardrail-v2`, step 6개(개정 3의 5개에서 게이트웨이 설정·클라이언트 step이 돌아와 1개 늘었다). 한 폴더 순차 운영(live 중 폴더 수정 금지). 소비자 목록은 FP-071에 따라 `rg`와 파일 읽기로 모았고 **step 파일 작성 직전에 변경을 시뮬레이션한 전체 `pytest`·`vitest`로 다시 확인한다.**

| step | 이름 | 한 줄 | 수정 허용 파일 후보 | 기존 테스트 소비자(정확 일치 단언 등) |
|---|---|---|---|---|
| 0 | `v2-alias-and-prices` | LiteLLM 두 설정에 별칭·배포를 더하고(fallback 없음) `AGENT_KEY_MODELS`에 별칭을 더하며 Haiku 단가와 분류기 매핑을 `costs.py`에 둔다 | `infra/litellm/config.yaml`, `infra/litellm/config.obs.yaml`, `src/govops/gateway/agent_key.py`(`AGENT_KEY_MODELS`에 한함), `src/govops/costs.py`, `tests/test_compose.py`·`tests/test_agent_key.py`·`tests/test_team_keys.py`·`tests/test_costs.py`(각 ~에 한함, 단언 약화 금지) | `tests/test_compose.py:177`(`len(config["model_list"]) == 4` → 5), `:261-300`(두 설정 동일·`fallbacks` 기대값·모델 매핑 딕셔너리), `tests/test_agent_key.py:214-223`(`set(AGENT_KEY_MODELS) == model_name 집합`), `:229-269`(키 갱신 본문 `models`·출력 문자열), `tests/test_team_keys.py:32,80-85`(목록 동일성·본문), `tests/test_docs_consistency.py:357`(`AGENT_KEY_MODELS` 문서 언급), `tests/test_costs.py:45`(`len(PRICES) == 8` → 정확히 10)·`:46-56,213-229`, `tests/test_smoke_guardrail.py`(모델 순회 단계는 목록 자동 반영 — 확인) |
| 1 | `v2-semantic-core` | 항목 선별(v1 먼저 적용)·6종 PII 마스킹·길이 상한·**role별 분류 요청 메시지 구성(user / assistant tool_call + tool)**·라벨 해석·치환·단조성과 호스트 선별 ↔ 게이트웨이 `screen_request` 동치성을 순수 함수·테스트로 구현하고 `classifier_v2.json` 구조를 둔다. 가짜 분류기로 결정론 테스트 | `src/govops/guardrails/semantic.py`(신규), `src/govops/guardrails/classifier_v2.json`(신규), `tests/test_guardrails_semantic.py`(신규) | `tests/test_guardrails_boundary.py:12-37`, `tests/test_redteam_boundary.py:74-80`, `tests/test_guardrail_registration.py:12-18`(`rules_v1.json` 불변), `tests/test_gateway_hook_digest.py`·`test_gateway_hook_imports.py`(`HOOK_FILES` 불변이라 변경 없음 — 확인만) |
| 2 | `v2-classifier-client` | `LiteLLMChatClient.classify`와 `_send`의 `tools`·호출별 타임아웃 선택 인자, 상태 분류, `guardrail_v2` 예약·정산 | `src/govops/gateway/client.py`, `tests/test_gateway_client.py`(~에 한함), `tests/test_gateway_classifier.py`(신규) | `tests/test_gateway_client.py`(요청 본문·원장 행 수·예약 호출 수·`_send` 인자 단언 — **기존 `complete` 동작 불변**을 단언), `tests/test_smoke_gateway.py`·`tests/test_agent_key.py`(`LiteLLMChatClient` 사용), `tests/test_architecture.py`(gateway 경계, LLM SDK 목록은 기존 `openai` 사용 그대로) |
| 3 | `v2-graph-wiring` | `build_graph`에 선택 인자 `semantic`(기본 `None`)과 두 지점 삽입, `open_task_service`·`dev_m3`의 스위치 읽기·주입, 재생 거부 규칙, `GUARDRAIL_CODES` +1 | `src/govops/agent/graph.py`(두 지점에 한함), `src/govops/api/wiring.py`, `src/govops/evalrun/dev_m3.py`(스위치 읽기·manifest에 한함), `src/govops/replay/llm.py`(`ReplayChatClient.__init__`에 한함), `src/govops/audit/log.py`(`GUARDRAIL_CODES` +1에 한함), `tests/test_agent_semantic.py`(신규), 관련 기존 테스트(~에 한함) | `tests/test_agent_guardrail.py:128`·`tests/test_audit.py:290`(`guardrail.error` 코드), `tests/test_audit_events.py`(코드·필드 정확 일치), `tests/test_agent_turns.py`(턴 키 집합 — 새 키 없음), `tests/test_obs_instrument.py:33,85,105`, `tests/test_api_wiring_build.py`·`tests/test_api_wiring.py`(`build_task_service` 서명), `tests/test_evalrun_dev_m3.py`(manifest 키 집합), `tests/test_golden_m3.py`·`test_regolden.py`·`test_golden_trace.py`·`test_golden_harness.py`(수정 없이 통과), `tests/test_agent_limits.py`·`test_agent_finalize.py`, `build_graph(` 테스트 약 60곳(기본값 `None`일 때 동작 동일) |
| 4 | `v2-smoke-and-preflight` | `make smoke-semantic` 진입점과 단계, preflight의 분류기 배포 필수 검사(스위치 on일 때) | `src/govops/api/smoke_semantic.py`(신규), `Makefile`(타깃 한 개에 한함), `src/govops/evalrun/preflight.py`, `tests/test_api_smoke_semantic.py`(신규), `tests/test_makefile.py`·`tests/test_evalrun_preflight.py`(~에 한함) | `tests/test_makefile.py`(타깃 목록·`.PHONY` 정확 일치), `tests/test_evalrun_preflight.py:36`(`sorted(required_deployments())` 정확 일치 — 스위치 off일 때 불변), `tests/test_docs_consistency.py`(AGENTS.md 명령어 표와 Makefile 타깃 대응) |
| 5 | `v2-docs-sync` | ADR-026("A 불성립(측정 인용) → 호스트 수집 지점에서 분류, 호출은 게이트웨이 경유"), ARCHITECTURE·THREAT_MODEL·EVAL 갱신, `EVAL.md:124`를 M6 7절(공식 test 3회)에 맞춤, `.env.example`, AGENTS.md 명령어 표 한 줄(`smoke-semantic`) | `docs/ADR.md`(ADR-026 추가 + ADR-008 변경 기록 한 줄), `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/EVAL.md`, `AGENTS.md`(명령어 표 한 줄에 한함 — **CRITICAL 문구·ADR-007 문구 변경 없음**), `.env.example`(`GOVOPS_GUARDRAIL_V2`) | `tests/test_docs_consistency.py:342,388`(ADR-023 절 문자열 단언 — **ADR-023 본문은 수정하지 않는다**), 문서 변경을 임시 반영해 `tests/test_docs_consistency.py`·`tests/test_env_defaults.py` 전체 실행(FP-071) |

**새 결정론 테스트**

| step | 단언 |
|---|---|
| 0 | `set(DEPLOYMENT_MODELS)`·`set(REGISTERED_MODELS)` 불변. `resolve_deployment("anthropic-claude-haiku-5-5").mapped`·`resolve_model("claude-haiku-5-5").mapped`가 거짓. 두 설정 파일에서 `guardrail-v2`가 `router_settings.fallbacks`의 키·값 어디에도 없음. `AGENT_KEY_MODELS` ⊇ 별칭, 두 설정의 `model_name` 집합과 일치 |
| 1 | v1 차단 사용자 입력은 항목 0개. 전송 텍스트에 `INPUT_PII_KINDS` 6종이 없음(비-JSON 본문의 `landline`·`card`·`account` 포함). v1 격리 문장이 전송 텍스트에 없음. 40자 미만·식별자 형식·마커-only 제외, 길이 상한(앞 1,600 + 뒤 900), 라운드당 16개 상한과 `unclassified` 계수. 라벨 해석(정확 일치만). **동치성**: 같은 항목에 대해 호스트 사전 선별 결과(`screen_user_messages`·`screen_tool_messages`)와 분류 요청 메시지에 대한 `screen_request` 결과가 일치한다 — 사용자형은 `allow`, 도구형은 `allow`이고 메시지 내용 무변화(`quarantined`·`masked` 모두 거짓), 고정 `system`·`user` 안내 문장도 `allow`·무변화. 요청 구성: 사용자형 2메시지, 도구형 4메시지(`assistant.tool_calls` 1개의 `id`가 `tool_call_id`와 같고 `valid_tool_call_id`), 대상 텍스트는 래퍼 없이 그대로 |
| 2 | 가짜 SDK 응답: 200 정상·`SAFE`/`UNSAFE`, 어휘 밖, `length` 종료, 429, 500, 타임아웃, 400 `injection_direct`, 400 `pii_input`, 403 `key_model_access_denied` → 상태 `ok`·`parse`·`parse`·`rate_limited`·`error`·`timeout`·`v1_blocked`·`v1_blocked`·`error`. **어느 경우에도 예외가 호출자(그래프)로 올라가지 않는다**(예산 초과만 예외). 요청 본문: 사용자형은 `tools` 없음, 도구형은 고정 도구 정의 1개와 `tool_choice: "none"`. 모델 `guardrail-v2`·호출별 타임아웃 4.0. 재시도 0회. 예약 `purpose="guardrail_v2"`·정산 행(성공: usage, 타임아웃: 예약액, 429·4xx: 0). 응답 헤더 `q`·`m`·`ids`가 반환값에 없음 |
| 3 | 스위치 off(`semantic=None`)에서 `chat.complete` 인자·메시지가 변경 전과 바이트 단위로 같다. 사용자 `INJECTION` → 모델 호출 0건, `blocked_input`/`injection_direct`, 감사 `guardrail.input_blocked`가 게이트웨이 경로와 같은 필드 + `v2=True`. 도구 `INJECTION` → 치환, `quarantined_ids` 중복 제거, 이후 호출에서 분류 0건. **분류 요청이 v1에 막히거나(`v1_blocked`) 실패해도 task는 `completed`로 이어지고 `guardrail.input_blocked`·`blocked_input`이 생기지 않으며 `guardrail.error`(`guardrail_v2_error`)만 남는다.** 예약 실패 → `failed/budget_exceeded`·분류 호출 0건. `evidence_ids`·탐색 예산 불변. **`GOVOPS_GUARDRAIL_V2=on`에서 `ReplayChatClient`를 만들면 `ValueError("guardrail_v2_replay")`, off에서는 정상** |
| 4 | 스위치 on일 때만 `required_deployments()`에 분류기 ID가 들어감. `v2_*` 단계는 가짜 서비스로 결정론 단언, 검사 대상 0건이면 FAIL(FP-069) |

**결정론 테스트로 못 덮는 것(사용자 실행)**: 실제 `claude-haiku-5-5`의 LiteLLM 경유 응답 형식·`usage`·지연·429, 개정 프롬프트의 규격 준수, 입력/도구 범위 v1 규칙 차이로 인한 `v1_blocked` 비율, 컨테이너 재기동·키 갱신 결과, Langfuse에 원문 부재.

## 6. 사전 등록 대상

**이 phase에서 등록(코드 머지 뒤 smoke 전, `docs/results/hypotheses-m6-guardrail-v2.md`)**

- `v2_config_sha256`(프롬프트 2종·모델 ID·어휘·`max_completion_tokens`·상한·타임아웃을 담은 `classifier_v2.json` 전체 바이트 해시)
- 위치(2-2)·항목 선별과 전송 텍스트(2-3, PII 6종 마스킹)·판정 반영(2-5) 규칙, 타임아웃 4.0s(연결 2.0s)·라운드당 16개·동시 8개·항목 길이 상한(앞 1,600 + 뒤 900)
- 분류기 별칭 설정(`fallbacks` 없음), 상태 분류 6종
- 성립 기준(3절)과 처리 규칙, **지연 측정 원문과 판정(1절)을 "측정 기록" 절에 그대로**
- 스위치 `GOVOPS_GUARDRAIL_V2` 호스트 전용·기본 off, 재생·CI 스위치 off 고정 규칙, 실행 산출물 기록 항목
- 단가·출처·확인일·계산 가정(8절)
- 동결 단계 3개(2-10)
- 이 문서가 탐지 성능을 주장하지 않는다는 명시

**phase ③(`m6-redteam-run`)으로 넘김**

- 레드팀 120건 차단율·오탐률 목표 수치와 v1 대 v2 비교 판정 규칙(`hypotheses-m4-redteam.md`·`hypotheses-m6-redteam-addendum.md`를 보완하는 새 등록). v2는 에이전트 경유 호출에만 적용되므로 비교 실행은 에이전트 경유로만 한다.
- **v2를 켠 실행마다 결과에 기록할 것**: 분류 상태 분포(`ok`·`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`·합계 `unclassified` 수)와 **러너 동시성**(동시 task 수). **"v2가 실질적으로 꺼진 실행"으로 볼 임계값(예: `ok` 비율 하한)은 phase ③ 사전 등록에서 정한다.** 이 문서는 임계값을 정하지 않는다.
- 정상 대조 60건에서 "v2 격리가 일어난 task" 오탐 임계, v2를 공식 구성에서 뺄지 정하는 **수치 임계**(판단 근거 규칙은 `M6_DESIGN_DRAFT.md` 7절 그대로)
- 공식 test 목표(TCR·정확도·지연)는 `hypotheses-test-targets.md`

**주장하지 않는 것**

- v2가 v1보다 차단율을 높인다는 주장 없음. 레드팀 120건·정상 대조·test 40은 이 phase에서 실행하지 않는다.
- v2 오탐률·격리 재현율에 대한 수치·주장 없음.
- 정확도·TCR 결과 수치를 이 phase 문서에 쓰지 않는다. 3절 기준은 규격·완료율·지연·비용의 **성립 여부**만 판단한다.
- 분류기가 간접 인젝션을 막는다는 보장 없음. 피해 지점은 계속 결정론 검사와 승인이다(ADR-008).
- 분류기 판정이 결정론적이라는 주장 없음(`temperature` 지정 불가).
- 에이전트를 거치지 않은 호출에 v2가 적용된다는 주장 없음(2-11).
- 게이트웨이 경유 지연이 직접 호출 측정과 같다는 주장 없음(0-1의 27).

## 7. 첫 live에서 실패할 수 있는 지점과 대응

| # | 지점 | 증상 | 대응 |
|---|---|---|---|
| 1 | 컨테이너가 낡아 별칭 배포가 없음 | 전 항목 `error`(별칭 없음), v2가 조용히 꺼진 실행 | 설정 파일은 바인드 마운트라 내용만 바뀌면 재생성되지 않는다. 4절 1단계 `--force-recreate`. preflight가 스위치 on일 때 분류기 배포 ID 필수(`litellm_config_stale`). 상태 분포 기록 |
| 2 | 키 허용 모델 갱신 누락 | 403 `key_model_access_denied` → 전 항목 `error` | 4절 2단계 `make agent-key-update`·`make team-keys-update`. smoke `allowed_models`·`v2_alias_access` |
| 3 | 별칭이 fallback으로 OpenAI에 넘어감 | 분류 응답이 OpenAI 모델, 응답 배포 ID가 `openai-…` | `fallbacks`에 별칭을 넣지 않는 테스트(step 0). 분류 메서드는 응답 배포 ID가 `anthropic-claude-haiku-5-5`가 아니면 `error` 처리 |
| 4 | 분류 요청이 v1에 막힘 | `v1_blocked` 비율 상승 | role을 원래 경로와 맞추고 호스트가 같은 규칙으로 미리 걸러 동치성 테스트로 보증(0-3). 그래도 막히면 `unclassified` + 경보, task 계속(결정론 테스트). 비율은 (b)가 보고. 구성은 레드팀 결과 전 확정이라 바꾸지 않는다 |
| 4-1 | `role:tool` 분류 요청 형식이 API 오류 | `tool_use`에 `tools` 정의 필요, 모델이 도구를 부르려 함, `tool_choice` 거부 | 0-2 미실측. C4 smoke가 첫 확인, 미충족이면 새 등록 파일로 1회 개정(3절) |
| 5 | v1 격리·감사가 분류 요청 때문에 task 집계를 오염 | task의 `q`·`m` 증가, `guardrail.input_blocked` 오기록 | 분류 메서드가 응답 헤더·예외를 그래프에 올리지 않음(결정론 테스트) |
| 6 | 개정 프롬프트가 규격을 지키지 않음 | C4 미충족, `parse` 비율 높음 | 어휘 정확 일치·`length` 종료는 `parse`(통과 + 경보). 새 등록 파일로 1회 개정 |
| 7 | 429·타임아웃 | `rate_limited`·`timeout` 상승 | 재시도 없음, v1만 남기고 통과 + 경보. (b) 기준이 비율을 본다. 게이트웨이 홉 추가로 4.0s가 빠듯할 수 있음 |
| 8 | task 추가 지연 | (c) 초과 | 3절 처리 규칙(범위 축소). 항목 병렬이라 라운드당 ≈ 단일 호출 지연 |
| 9 | 예약이 Anthropic 상한에 걸림 | task `failed`/`budget_exceeded` | 8절 가정, `make cost-report`. v2를 조용히 끄지 않음 |
| 10 | 분류 generation에 원문 노출 | `v2_no_raw_in_trace` FAIL | `turn_off_message_logging`에 의존. 새 단계가 task trace 전체(분류 generation 포함)와 최근 trace 목록에서 sentinel을 조회. 노출되면 v2 제외 |
| 11 | v2 치환 뒤 기록 재생 불일치 | live 기록을 스위치 off로 재생할 때 요청 키 불일치 | 재생·CI는 스위치 off 고정. `ReplayChatClient`가 스위치 on이면 시작 거부(결정론 테스트) |
| 12 | 정상 문서 문자열 오탐 격리 | 도구 결과 일부가 `[정책으로 일부 제외]`가 되어 task 실패 | 항목 단위 격리의 비용. phase ③ 정상 대조에서 집계 |
| 13 | 항목 16개 초과로 일부 미분류 | 문서를 많이 끌어오는 요청에서 그 라운드는 v1만 적용 | `guardrail.error` 경보와 THREAT_MODEL 잔여 위험 |
| 14 | 원장 변경이 step 커밋에 섞임 | 측정 원장 4행이 첫 step 커밋에 들어가거나 untracked 문서가 청결 검사에 걸림 | 설계 문서·사전 등록 파일·phase 파일·측정 원장을 한 사전 등록 PR로 먼저 머지(`scripts/execute.py:222-280`) |
| 15 | 새 smoke가 공허하게 PASS | v2가 꺼져 있는데 단계가 통과 | 단계는 스위치 on을 확인하고 off이면 `SKIP`, 검사 대상 0이면 FAIL(FP-069) |
| 16 | `build_task_service` 호출부 누락 | dev 러너만 분류기 미주입 | 호출부는 `open_task_service`·`dev_m3` 두 곳(0-1의 23). 둘 다 스위치를 읽고 manifest에 기록 |

## 8. 비용 추정

**단가**(확정): 입력 $0.10/MTok, 출력 $0.50/MTok(프롬프트 100k 이하), 출처 `https://platform.claude.com/docs/en/about-claude/pricing`, 확인일 2026-10-13. 게이트웨이 경유로 호출해도 정산은 호스트 원장(`purpose=guardrail_v2`)이며 LiteLLM 자체 비용 계산은 쓰지 않는다.

**가정(1절 `mean_in` 실측으로 재계산)**

- 한글 1자 ≈ 1토큰(실측: 1,000자당 약 1,000토큰). 고정 입력(시스템 지시)은 자리표시 문구 기준 약 860토큰(실제 개정 프롬프트가 짧으면 줄어든다). 출력은 실측 8토큰 이하(개정 프롬프트는 16 상한).
- 항목당 비용 = (860 + 자수) × $0.10/M + 8 × $0.50/M. 실측 일치: 200자 $0.00011, 1,200자 $0.00021, 2,400자 $0.00033(측정 로그 `cost` ÷ 20).
- **항목당 1회 분류**(치환이 메시지에 남아 재분류 없음). 항목 수: 기록 재생 14 task에서 40자 이상 문자열 59개 → 평균 4.2개/task, 최대 16개. mock-jira 도구 결과의 자유 텍스트 필드(부록 표)도 같은 규칙으로 항목이 되며 티켓당 title·description·comments가 더해질 수 있다. 사용자 입력 1개(약 100~300자)를 더한다.

| 시나리오 | task당 분류 | task당 비용 | 300 task 합계 |
|---|---|---|---|
| 기본(항목 4.2개 × 중앙값 1,200자 + 사용자 입력) | 약 5.2회 | ≈ **$0.0010** | ≈ **$0.3** |
| 최대(항목 16개 × 2,400자 + 사용자 입력) | 17회 | ≈ **$0.0057** | ≈ **$1.7** |

- 300 task 가정(M6 7절 기준): 레드팀 120 + 정상 대조 dev 20 + 공식 test 40 × 3 = 120 + 여유 40. `EVAL.md:124`는 step 5에서 7절에 맞춘다.
- 사용자 입력이 v1 차단인 task(레드팀 직접 인젝션·PII 60건 정도)는 v2를 호출하지 않으므로 실제는 더 작다.
- 분류 요청이 v1에 막히면 모델 호출이 없어 비용 0이다(정산 0).
- 에이전트 주 모델 비용 task당 $0.0006~0.0007(dev 실측) 대비 기본 안의 v2는 약 1.5배다. 비용 비율은 결과 문서에 따로 보고한다.
- 최대 시나리오 $1.7은 Anthropic 잔여(상한 $11.47 − 원장 현재 약 $0.25, 참고값)의 약 15%로 (d) 기준(50%)을 만족하는 추정이다. smoke 실측 평균으로 확정한다.
- 지연 측정 비용은 $0.01294(원장 4행, 사전 등록 PR에 함께 커밋). smoke 예상 호출 약 150건 → $0.03 안팎.

## 부록. mock-jira·mock-slack 도구 결과의 자유 텍스트 필드

저장소 코드에서 확인했다(기록 재생 파일 부재와 무관).

| 도구(모델이 보는 결과) | 자유 텍스트 필드 | 근거 | 비고 |
|---|---|---|---|
| `jira_get_ticket` | `title`(최상위) | `src/govops/mocks/jira.py:19-21,42-43`(`RESPONSE_FIELDS`는 `TICKET_FIELDS`에서 접근 필드를 뺀 것, `TICKET_FIELDS`에 `title`: `src/govops/mocks/seed.py:6-26`) | 짧을 수 있다(40자 미만은 항목 제외) |
| `jira_get_ticket` | `description`, `comments[].body`, `comments[].author`, `children[].title` | `src/govops/mocks/jira.py:44-49,211-231`, 오버레이 필드 `src/govops/mocks/seed.py:89-105`(`description`, `comments`의 `body`·`author`만 허용) | `description`·`comments`는 오버레이 티켓과 상태에 코멘트가 있는 티켓에만 있다. **코멘트 본문은 승인된 에이전트 쓰기(`jira_add_comment`)의 본문도 포함해 외부 입력이 될 수 있다**(`jira.py:248-258`) |
| `jira_get_ticket` / `jira_search_tickets` | `metadata`(문자열 맵) | `jira.py:19-21`(`RESPONSE_FIELDS`에 `metadata`), 쓰기 표식 `src/govops/mocks/jira.py:30-35,256-257` | 에이전트 쓰기 표식 값. 외부 자유 텍스트가 아니지만 문자열이다 |
| `jira_search_tickets` | `tickets[].title`(필터는 `q in title`) | `src/govops/mocks/jira.py:195-209`, `src/govops/tools/registry.py:216-220` | 오버레이 티켓이면 `description`·`comments`도 `_response`에 포함(`jira.py:44-49`) |
| mock-slack | **모델이 보는 읽기 도구가 없다** | 에이전트 도구 정의는 `jira_get_ticket`·`jira_search_tickets`(읽기)와 A MCP 도구뿐이고 `slack_send_message`는 쓰기다(`src/govops/tools/registry.py:50-56,98-110`). 채널 이력 조회는 표식 확인용이며 모델 컨텍스트에 들어가지 않는다(`src/govops/tools/slack.py`) | v2 대상 아님 |

따라서 v2 항목 선별은 필드 이름이 아니라 "JSON 문자열 값 ≥ 40자, 식별자 형식 제외" 규칙으로 위 필드를 모두 포함한다(`description`·`comments[].body`가 주 대상). 도구 결과 JSON의 중첩 깊이가 얕아 선별 규칙에 추가 가정이 없다.

## 9. 결정 현황

**확정**

| # | 결정 |
|---|---|
| 1 | v2 위치는 호스트(에이전트 수집 지점). A 위치(훅 안)는 지연 측정으로 불성립 |
| 2 | 분류 호출은 **게이트웨이 경유**(별칭 `guardrail-v2`, fallback 없음). AGENTS.md:57·ADR-007 문구는 바꾸지 않는다. 호스트는 `ANTHROPIC_API_KEY`를 읽지 않는다 |
| 3 | 사용자 차단 코드 `injection_direct` 재사용, 도구는 항목 전체 격리 |
| 4 | 스위치 `GOVOPS_GUARDRAIL_V2`는 호스트만 읽고 기본 off. 재생·CI는 off 고정, 재생에서 on이면 시작 오류 |
| 5 | 비용은 호스트 `CostLedger` 예약·정산(`purpose=guardrail_v2`), 예약 실패 시 task `failed` |
| 6 | 첫 live 전 등록 커밋, 단가 출처·확인일(`costs.py:62` URL, 2026-10-13) |
| 7 | 비용 기준 M6 7절(공식 test 3회), `EVAL.md:124`는 step 5에서 맞춤 |
| 8 | 제안값 승인: 타임아웃 4.0s(연결 2.0s), 라운드당 16개·동시 8개, 항목 길이 상한(앞 1,600 + 뒤 900), (c) task당 추가 지연 p95 ≤ 8.0s |
| 9 | 감사 불리언 `v2=True`와 코드 `guardrail_v2_error` 추가, 새 진입점 `make smoke-semantic`, 개정 프롬프트·어휘는 코드 머지 뒤 smoke 전에 확정·등록 |
| 10 | `AGENT_KEY_MODELS`에 분류 별칭을 더해 에이전트 키·팀 키가 직접 호출하게 한다. 근거는 "에이전트 키·팀 키는 이미 `agent-primary`·`agent-fallback`(Sonnet)을 호출할 수 있어 새 능력이 생기지 않는다"이며 게이트웨이가 프롬프트를 강제하지 않으므로 범용 호출이 가능하다는 점을 THREAT_MODEL에 한 줄로 남긴다 |
| 11 | 사용자 입력 항목은 `role:user`, 도구 결과 항목은 `role:tool`(고정 가짜 assistant `tool_call` 1개 + `tool` 메시지)로 분류기에 보낸다. 호스트 사전 선별과 게이트웨이 v1의 동치성을 결정론 테스트로 단언한다. 레드팀 결과 전 구성을 확정한다(결과를 본 뒤 변경은 사전 등록 위반) |

**새로 필요한 사용자 결정**: 없음. 이전 개정의 F(별칭 팀 키 승인)와 G(`role:tool` 구성)는 위 확정 10·11로 닫았다.

# Step 0: deployment-model

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·019), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조"), `/docs/THREAT_MODEL.md` (F6)
- 직전 phase(`m3-gateway`) 산출물: `/src/govops/gateway/client.py`, `/src/govops/gateway/faults.py`, `/src/govops/gateway/smoke.py`, `/src/govops/gateway/agent_key.py`, `/src/govops/costs.py`, `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`, `/src/govops/replay/llm.py`
- `/tests/test_gateway_client.py`, `/tests/test_replay_llm.py`, `/tests/test_costs.py`, `/tests/test_evalrun_dev.py`

## 작업

**결함**: `make smoke-fallback`의 normal 단계에서 주 모델 호출(`openai/gpt-6-luna`)은 성공했는데 `ChatResult.model`(응답 본문 `model`)이 매핑되지 않았다. 실제 LiteLLM 응답은 본문 `model`에 **별칭**(`agent-primary` 등)을 넣는다. 단위 테스트는 제공자 모델 ID가 든 가짜 응답만 썼기 때문에 이 차이를 잡지 못했다(가짜 응답으로만 검증한 계약). 실제 응답 모델은 **응답 헤더의 배포 ID**로 정한다. TDD로 테스트를 먼저 쓴다.

### 1. LiteLLM 설정 (`config.yaml`·`config.obs.yaml` 둘 다)

각 배포에 `model_info.id`를 붙인다:

| model_name | `model_info.id` |
|---|---|
| `agent-primary` | `openai-gpt-6-luna` |
| `agent-primary-only` | `openai-gpt-6-luna-only` |
| `agent-primary-down` | `openai-gpt-6-luna-down` |
| `agent-fallback` | `anthropic-claude-sonnet-5-5` |

두 설정 파일의 ID는 같아야 한다(콜백 설정만 다르다). 기존 값(timeout 15초·`num_retries` 0·fallbacks 매핑)은 그대로 둔다. 설정 파싱 테스트에 ID를 추가해 고정한다.

### 2. 배포 ID → 모델 매핑 (`costs.py`)

- 코드 상수 한 곳: `{"openai-gpt-6-luna": "gpt-6-luna", "openai-gpt-6-luna-only": "gpt-6-luna", "anthropic-claude-sonnet-5-5": "claude-sonnet-5-5"}`. **`openai-gpt-6-luna-down`은 매핑하지 않는다**(닫힌 포트를 가리켜 응답할 수 없는 배포다. 이 ID가 응답 헤더로 오면 미등록으로 취급하고 경보한다).
- `resolve_deployment(deployment_id: str | None) -> ResolvedModel`: 등록된 ID면 해당 모델의 `ResolvedModel`(공급사·단가)을 돌려주고 아니면 `mapped=False`.
- **응답 모델 결정 순서**: ① 응답 헤더 `x-litellm-model-id`가 **있으면** 배포 ID 매핑으로만 정한다(없는 ID·매핑 안 된 ID는 미등록 → 비싼 단가 + 경보. 본문 `model`은 이 경우 쓰지 않는다). ② 헤더가 **없으면** 지금처럼 본문 `model`을 `resolve_model`로 해석한다(매핑 안 되면 비싼 단가 + 경보).

### 3. 게이트웨이 클라이언트 (`gateway/client.py`)

- 호출을 OpenAI SDK의 raw 응답(`with_raw_response`)으로 받아 **헤더**와 본문을 함께 읽는다. 읽을 헤더: `x-litellm-model-id`, `x-litellm-model-group`, `x-litellm-attempted-fallbacks`(정수로 파싱, 파싱 실패는 None). 헤더 값 외의 응답 헤더·요청 헤더·키·본문은 보관·출력하지 않는다.
- `ChatResult.model`은 **매핑된 제공자 모델 ID**(`gpt-6-luna`·`claude-sonnet-5-5`)다. 매핑 실패면 본문 `model` 값을 그대로 두고 `unmapped_model=True`. `ChatResult`에 `deployment_id: str | None`, `body_model: str | None`, `model_group: str | None`, `attempted_fallbacks: int | None`를 추가한다(기본 None, 기존 생성처 호환). `fallback_used`는 응답 공급사가 요청한 기본 공급사와 다르거나 `attempted_fallbacks > 0`일 때 True.
- 정산은 위 결정 순서로 얻은 공급사·모델로 한다(이전 phase의 공급사별 예약 2건 규칙 그대로, 미등록이면 최대 단가로 양쪽 정산 + 경보).
- 모델 호출 span 속성에 `deployment_id`·`model_group`·`attempted_fallbacks`를 남긴다(값은 모델명·배포 ID라 비밀이 아니다).

### 4. 기록·manifest

- `replay/llm.py`: LLM 기록 행의 `response_model`은 **매핑 결과**(제공자 모델 ID)이고 `deployment_id`를 추가한다(없으면 `null`). 기존 기록 파일(`data/replay/llm/`)은 수정하지 않고 재생은 필드가 없는 기존 기록도 읽는다. 프롬프트 해시 계산은 바꾸지 않는다.
- `evalrun/dev.py`: `models_seen`은 호출마다 `ChatResult.model`(매핑된 제공자 모델 ID), `unmapped_models`는 미등록으로 정산된 응답의 식별자(배포 ID 또는 본문 모델)를 모은다.

### 5. 테스트 (실제 LiteLLM 응답 형태를 픽스처로 쓴다)

- **픽스처**: 본문 `model="agent-primary"`(별칭), 헤더 `x-litellm-model-id: openai-gpt-6-luna`, `x-litellm-model-group: agent-primary`, `x-litellm-attempted-fallbacks: 0`. fallback 응답: 본문 `model="agent-fallback"`, 헤더 `x-litellm-model-id: anthropic-claude-sonnet-5-5`, `x-litellm-attempted-fallbacks: 1`.
- 위 픽스처에서 `ChatResult.model`이 `gpt-6-luna` / `claude-sonnet-5-5`, `unmapped_model=False`, 올바른 공급사 예약에 정산, `fallback_used` 값.
- 헤더 없음 → 본문 모델로 해석(기존 테스트 유지, 매핑 안 되면 최대 단가 + `unmapped_model=True`). 헤더가 있는데 알 수 없는 ID·`openai-gpt-6-luna-down` → 미등록으로 최대 단가 정산 + 경보. `x-litellm-attempted-fallbacks`가 숫자가 아니면 None.
- 설정 파일 두 개의 `model_info.id` 집합이 코드 상수의 키(+ down)와 일치한다.
- 모델이 섞인 task에서 `models_seen`이 제공자 모델 ID이고 별칭이 들어가지 않는다. LLM 기록에 `response_model`·`deployment_id`가 남고 기존 기록도 재생된다. `test_golden_trace`·`test_golden_harness` 수정 없이 통과.

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

- 응답 모델을 본문 `model`만으로 정하지 마라(헤더가 있을 때). 이유: 실제 LiteLLM은 본문 `model`에 별칭을 넣는다.
- 가짜 응답에 제공자 모델 ID가 든 본문만 써서 계약을 검증하지 마라. 이유: 이 결함이 그렇게 가려졌다.
- `openai-gpt-6-luna-down`을 정상 모델로 매핑하지 마라.
- 키·요청 헤더·응답 본문을 로그·출력·span에 남기지 마라(모델명·배포 ID는 허용).
- `data/replay/llm/`의 기존 기록 파일과 프롬프트 해시 계산을 바꾸지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

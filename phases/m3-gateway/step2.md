# Step 2: gateway-fallback

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·008·009·018), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조", "상태 관리"의 비용 원장), `/docs/THREAT_MODEL.md` (F6)
- `/src/govops/gateway/client.py`, `/src/govops/costs.py`(이전 step의 `resolve_model`·`response_cost_usd`·`anthropic_cost_usd`), `/src/govops/replay/llm.py`, `/src/govops/evalrun/dev.py`(`_Capture`, `models_seen`, manifest), `/src/govops/agent/graph.py`(모델 호출 span), `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`, `/infra/docker-compose.yml`, `/.env.example`, `/tests/test_gateway_client.py`, `/tests/test_replay_llm.py`, `/tests/test_compose.py`, `/tests/test_evalrun_dev.py`
- 이슈 #34: 호출마다 실제 응답 모델명(제공자 모델 ID)을 `ChatResult.model`·LLM 기록의 `response_model`·`models_seen`에 남긴다.

## 작업

LiteLLM에 **fallback**을 설정하고 게이트웨이 클라이언트가 **응답 모델 기준으로 원장을 정산**하게 한다. TDD로 테스트를 먼저 쓴다.

### LiteLLM 설정 (`infra/litellm/config.yaml`과 `config.obs.yaml` **둘 다**)

- `model_list`: `agent-primary`(`openai/gpt-6-luna`), `agent-fallback`(`anthropic/claude-sonnet-5-5`, `api_key: os.environ/ANTHROPIC_API_KEY`), `agent-primary-only`(`agent-primary`와 같은 배포, **fallbacks에 연결하지 않는다**: Anthropic 상한이 소진됐을 때 fallback 없이 부르기 위한 별칭), `agent-primary-down`(`openai/gpt-6-luna`이지만 `api_base: http://127.0.0.1:9`로 닫힌 포트를 가리킨다: 다음 step의 모델 장애 주입 백엔드용, 평소에는 아무도 부르지 않는다).
- **값(config 파싱 테스트로 고정한다)**: `agent-primary`의 `timeout`은 **15초**, `num_retries`는 **명시적으로 0**(주 모델 실패 시 같은 모델 재시도 없이 바로 fallback), `fallbacks`: `agent-primary`→`agent-fallback`, `agent-primary-down`→`agent-fallback`. `agent-fallback`의 `timeout`은 30초. `litellm_params`·`router_settings` 중 LiteLLM이 해당 값을 읽는 위치에 둔다(공식 문서로 위치를 확인하고 최종 응답 summary에 어떤 위치인지 적는다). 클라이언트 쪽 `timeout_s`(60초)는 주 15초 + fallback 30초보다 길게 유지한다.
- `.env.example`에 `ANTHROPIC_API_KEY`가 이미 있다. compose의 litellm 서비스가 이 변수를 받는지 확인하고 없으면 추가한다(`tests/test_compose.py`의 변수 선언 검사를 통과해야 한다).
- 테스트: 두 설정 파일을 YAML로 파싱해 위 값·별칭·fallbacks 매핑이 같다는 것을 단언한다(두 파일의 `model_list`와 fallbacks는 동일해야 하고 `config.obs.yaml`만 콜백 설정이 다르다).

### 게이트웨이 클라이언트 (`gateway/client.py`)

- **원장: 공급사별 예약 2건.** 호출 전에 ① `agent_primary`(OpenAI) 예약(OpenAI 단가 추정)과 ② `agent_fallback`(Anthropic) 예약(Anthropic 단가 추정)을 **둘 다** 잡는다(상한 검사는 예약 안에서 한다). 추정 입력 상한은 지금 방식(`2 * 직렬화 바이트 + 1024`)을 쓴다.
  - OpenAI 예약이 `BudgetExceededError`면 호출하지 않고 그대로 올린다(주 모델 상한 소진은 예산 중단이다).
  - **Anthropic 예약만** `BudgetExceededError`면(fallback 불가) 요청 모델을 `agent-primary-only`로 바꿔 fallback 없이 호출하고 Anthropic 예약은 만들지 않는다(주 모델 호출은 계속된다).
- **정산은 실제 응답 모델로 한다.** 응답의 `completion.model`을 `resolve_model`로 해석한다. 매핑된 공급사가 응답한 쪽 예약에 실제 비용을 `settle`하고, **다른 쪽 예약은 비용 0으로 `settle`한다**(원장 `settle`은 변경하지 않는다. 예약마다 같은 purpose로 정산하므로 기존 규칙으로 충분하다). 호출이 실패하면(응답 없음) 두 예약 모두 확인된 사용량 0으로 정산한다. **매핑되지 않은 응답 모델**이면 `response_cost_usd`의 최댓값 비용으로 **두 예약 모두** 정산하고, `ChatResult`에 `unmapped_model=True`를 표시하며 모델 호출 span에 `unmapped_model` 속성을 남긴다.
- `ChatResult.model`은 응답 본문의 제공자 모델 ID를 그대로 둔다(별칭을 쓰지 않는다). `ChatResult`에 `fallback_used: bool`(응답 공급사가 요청한 기본 공급사와 다름)과 `unmapped_model: bool`을 추가한다(기본값 False, 기존 생성처 호환).
- 감사 로그의 "모델 호출마다 최소 기록"은 이 step에서 연결하지 않는다(M4).

### 기록·manifest

- `replay/llm.py`의 LLM 기록 행에 `response_model`(제공자 모델 ID)을 추가한다. **기존 기록 파일(`data/replay/llm/`)은 수정하지 않는다.** 재생은 `response_model`이 없는 기존 기록을 그대로 읽는다(없으면 기록된 `model` 값을 쓴다). 프롬프트 해시 계산은 바꾸지 않는다.
- `evalrun/dev.py`: `manifest["models_seen"]`에 **호출마다 `ChatResult.model`(제공자 모델 ID)**을 모으도록 고치고(별칭 `agent-primary`가 아니라), `manifest["unmapped_models"]`(매핑 실패 모델 ID 목록, 없으면 빈 목록)와 `manifest["fallback_used"]`를 실제 값으로 기록한다. M2 dev 러너의 다른 동작은 바꾸지 않는다.

### 테스트 (가짜 HTTP·임시 원장, 키·네트워크 불필요)

- 정상 호출: OpenAI 응답 → `agent_primary`에 실제 비용, `agent_fallback`은 0 정산.
- fallback 응답(Anthropic 모델 ID): `agent_fallback`에 실제 비용(Anthropic 단가), `agent_primary`는 0 정산, `fallback_used=True`.
- 날짜 접미사가 붙은 응답 모델 ID는 매핑된다. 매핑되지 않은 모델 ID는 최댓값 비용으로 두 예약 정산 + `unmapped_model=True`.
- OpenAI 상한 소진 → 호출 전 `BudgetExceededError`, 호출 0. Anthropic 상한만 소진 → `agent-primary-only`로 호출되고 Anthropic 예약 없음.
- 호출 실패(타임아웃·5xx) → 두 예약 모두 0 정산(예약이 남지 않는다).
- 모델이 섞인 task(1회차 OpenAI, 2회차 Anthropic)에서 호출마다 모델 ID가 기록되고 `models_seen`에 둘 다 들어간다.
- LLM 기록: 새 기록에 `response_model`이 있고, 기존 기록(없음)도 재생된다. **`test_golden_trace`·`test_golden_harness`가 수정 없이 통과**한다.
- 기존 게이트웨이·원장·dev 테스트 불변.

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

- 원장 `settle`의 규칙(예약과 같은 purpose만 정산)을 바꾸지 마라. 이유: 예약을 공급사별로 2건 잡으면 기존 규칙으로 충분하고, 규칙을 느슨하게 하면 정산 오류가 숨는다.
- 매핑되지 않은 응답 모델을 싼 단가로 정산하거나 무시하지 마라. 이유: 과소 계상은 상한 우회다.
- `data/replay/llm/`의 기존 기록 파일이나 프롬프트 해시 계산을 바꾸지 마라. 이유: 골든 기록이 "재기록 필요"로 실패한다.
- `ChatResult.model`에 LiteLLM 별칭을 넣지 마라. 이유: 모델이 섞인 task를 셀 수 없다(#34).
- fallback을 task 단위로 만들지 마라. 이유: 호출 단위 fallback이 설계다(ADR-007).
- 실제 Anthropic·OpenAI·LiteLLM에 접속하는 테스트를 만들지 마라.
- `infra/litellm/config.yaml`과 `config.obs.yaml`을 서로 다르게 두지 마라(콜백 설정 제외).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

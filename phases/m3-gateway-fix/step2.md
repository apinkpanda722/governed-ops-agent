# Step 2: backend-down-only

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·019), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조"), `/docs/THREAT_MODEL.md` (F6)
- 직전 phase(`m3-gateway`) 산출물: `/src/govops/gateway/client.py`, `/src/govops/gateway/faults.py`, `/src/govops/gateway/smoke.py`, `/src/govops/gateway/agent_key.py`, `/src/govops/costs.py`, `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`, `/src/govops/replay/llm.py`
- `/tests/test_gateway_client.py`, `/tests/test_smoke_gateway.py`, `/tests/test_docs_consistency.py`, `rg -n "mock_param|mock_testing_fallbacks" /src /tests /docs /Makefile /.env.example /AGENTS.md`로 모든 참조 확인

## 작업

**F6 모델 장애 주입 백엔드를 `down_alias` 하나로 확정**하고 `mock_param` 경로를 지운다. LiteLLM 프록시는 `mock_testing_fallbacks`를 기본으로 거부한다(켜려면 `dangerously_allow_mock_testing_request_params`가 필요하고 이 설정은 켜지 않는다). TDD: 지우기 전에 남는 동작(`down_alias`)의 테스트가 모두 통과하는지 확인한다.

- `gateway/faults.py`: `model_fault_mode()`는 `None | "down_alias"`만 허용한다(`mock_param`을 포함한 그 밖의 값은 `ValueError`). `ModelFaultScript`는 그대로 둔다.
- `gateway/client.py`: `mock_testing_fallbacks`를 요청 본문에 넣는 코드와 `fault_mode="mock_param"` 분기를 삭제한다. `down_alias`(실패해야 하는 호출만 모델 별칭 `agent-primary-down`)만 남긴다.
- `gateway/smoke.py`: `mock_param` 단계를 삭제한다(다음 step에서 PASS 조건을 고친다. 이 step에서는 단계 삭제와 `BACKEND=` 값이 `down_alias|none`이 되도록만 한다).
- 테스트에서 `mock_param` 관련 케이스를 삭제하고 `down_alias` 케이스는 유지한다. `GOVOPS_MODEL_FAULT_MODE`가 설정된 상태에서 서비스 시작 거부 동작은 유지한다(`down_alias`·잘못된 값 `mock_param` → `ValueError`/시작 거부 테스트 포함).
- 문서·설명에서 `mock_param`·`mock_testing_fallbacks` 언급을 모두 지운다(`.env.example`의 `GOVOPS_MODEL_FAULT_MODE` 설명은 `down_alias`만 말한다). 이 step에서 ADR 본문의 큰 갱신은 하지 않고(다음 문서 step이 한다) 남은 언급이 없도록 제거만 한다. `rg`로 참조가 0건인지 확인한다.

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

- `dangerously_allow_mock_testing_request_params`를 설정하지 마라. 이유: 프록시의 기본 보호를 끄는 설정이다.
- 이 step에서 장애 주입의 다른 동작(스크립트·서비스 시작 보호)을 바꾸지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

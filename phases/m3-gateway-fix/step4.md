# Step 4: gateway-fix-docs

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·019), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조"), `/docs/THREAT_MODEL.md` (F6)
- 직전 phase(`m3-gateway`) 산출물: `/src/govops/gateway/client.py`, `/src/govops/gateway/faults.py`, `/src/govops/gateway/smoke.py`, `/src/govops/gateway/agent_key.py`, `/src/govops/costs.py`, `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`, `/src/govops/replay/llm.py`
- `/docs/PRD.md`, `/docs/EVAL.md`, `/AGENTS.md`(명령어 블록), `/.env.example`, `/Makefile`, `/tests/test_docs_consistency.py`, `/tests/test_makefile.py`

## 작업

이 수정 phase에서 확정된 결정을 문서에 반영한다. 코드는 수정하지 않는다. 문서에는 **결정 내용만** 쓴다(누가 정했는지·변경 경위는 쓰지 않는다).

- **ADR-019 갱신** (`docs/ADR.md`, 기존 항목을 고친다):
  1. 응답 모델은 응답 헤더 `x-litellm-model-id`(배포 ID)로 정한다. LiteLLM 응답 본문 `model`에는 별칭이 오므로 본문은 헤더가 없을 때만 쓴다. 각 배포에 `model_info.id`(`openai-gpt-6-luna`·`openai-gpt-6-luna-only`·`openai-gpt-6-luna-down`·`anthropic-claude-sonnet-5-5`)를 두고 배포 ID → 모델·단가를 매핑한다. `openai-gpt-6-luna-down`은 응답할 수 없는 배포라 매핑하지 않고 미등록으로 경보한다. LLM 기록에는 `response_model`(매핑 결과)과 `deployment_id`, dev manifest의 `models_seen`·`unmapped_models`는 제공자 모델 ID·미등록 식별자다.
  2. 에이전트 가상 키 허용 모델은 `agent-primary`·`agent-primary-only`·`agent-primary-down`·`agent-fallback`이고 `config.yaml`·`config.obs.yaml`의 `model_name` 집합과 일치함을 테스트로 고정한다. 기존 키는 `make agent-key-update`로 갱신하며 `.env`의 키 값은 바뀌지 않는다.
  3. **F6 모델 장애 주입 백엔드는 `down_alias` 하나다**(닫힌 포트를 가리키는 배포 `agent-primary-down` + fallbacks). 요청 파라미터 `mock_testing_fallbacks`는 프록시가 기본으로 거부하며 이를 켜는 설정은 사용하지 않는다. `GOVOPS_MODEL_FAULT_MODE`는 `down_alias`만 허용한다.
  4. `smoke-fallback` 합격 조건: normal 단계는 OpenAI 증가 > 0·Anthropic 증가 = 0·미등록 0건, fallback 단계는 Anthropic 증가 > 0·OpenAI 증가 = 0·미등록 0건.
  5. 항목 번호가 바뀌거나 앞선 ADR-019 문장(`mock_param`·응답 본문 모델 해석)과 모순되면 기존 문장을 고쳐서 맞춘다.
- `docs/ARCHITECTURE.md`: 게이트웨이 절의 fallback·장애 주입 설명과 `infra/litellm/config.yaml` 설명(`model_info.id`)을 위 결정에 맞게 고친다. `mock_param` 언급은 모두 삭제한다.
- `docs/THREAT_MODEL.md`: F6 행의 주입 방법을 `down_alias`로 고친다.
- `docs/EVAL.md`: 안정성 절의 F6·fallback 보고 문장이 `down_alias`·배포 ID 기준 `models_seen`과 맞는지 확인한다.
- `AGENTS.md`: **명령어 블록만** 수정한다(`make agent-key-update` 추가, `smoke-fallback` 설명 유지). `.env.example`의 `GOVOPS_MODEL_FAULT_MODE` 설명이 `down_alias`만 말하는지 확인한다.
- 문서에 `mock_param`·`mock_testing_fallbacks`가 남아 있지 않아야 한다(`rg`로 확인). `tests/test_docs_consistency.py`·`tests/test_makefile.py`가 통과해야 한다(테스트를 약화하지 말고 문서·Makefile을 맞춘다).

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

- 결정 주체나 변경 경위를 문서에 쓰지 마라. 이유: 문서에는 결정 내용만 쓴다.
- 장애 주입 표본 수의 "미정"을 채우지 마라. 이유: 사전 등록 문서에서 정한다.
- `docs/results/hypotheses.md`를 수정하지 마라.
- `AGENTS.md`는 명령어 블록 외를 수정하지 마라.
- 코드·테스트를 수정하지 마라(문서 일관성 테스트 갱신이 필요하면 이유를 summary에 적는다).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

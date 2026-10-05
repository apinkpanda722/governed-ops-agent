# Step 4: smoke-fallback

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·008·009·018), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조", "상태 관리"의 비용 원장), `/docs/THREAT_MODEL.md` (F6)
- `/src/govops/gateway/client.py`, `/src/govops/gateway/faults.py`, `/src/govops/gateway/agent_key.py`(환경 변수·키 처리 방식), `/src/govops/costs.py`(`cost_report`), `/src/govops/api/smoke.py`·`/src/govops/tools/smoke.py`(스모크 출력 규칙), `/Makefile`, `/tests/test_agent_key.py`, `/tests/test_smoke_tools.py`

## 작업

사용자 실행 `make smoke-fallback`을 만든다. 실제 LiteLLM(compose)에 짧은 호출 몇 번을 보내 fallback 경로와 어느 장애 주입 백엔드가 쓸 수 있는지 확인한다. 이 step에서 **실제 호출을 실행하지 않는다**(코드와 가짜 transport 테스트만).

### `src/govops/gateway/smoke.py` (`python -m govops.gateway.smoke fallback`)

- 전제: LiteLLM 기동(새 설정 반영), `.env`의 `LITELLM_AGENT_KEY`·`LITELLM_BASE_URL`, 비용 원장 존재. 호출은 모두 `max_output_tokens`를 작게(예: 16) 하고 메시지는 합성 한 줄이다. 도구 정의는 보내지 않는다.
- 단계와 출력(한 줄씩, 비밀·본문 없음):
  1. **정상 호출**: 일반 경로로 1회 → 응답 모델이 매핑되고 공급사가 OpenAI인지 확인. `PASS normal family=openai mapped=true`.
  2. **`mock_param` 백엔드**: 실패 스크립트로 1회 → 응답 모델이 Anthropic 계열이고 매핑되면 `PASS mock_param family=anthropic mapped=true`. 요청이 거부되거나(HTTP 4xx) 응답이 여전히 OpenAI면 `INFO mock_param unsupported status=<코드 또는 kind>`로 표시하고 실패로 세지 않는다.
  3. **`down_alias` 백엔드**: 같은 방식으로 1회 → `PASS down_alias family=anthropic mapped=true` 또는 `FAIL down_alias …`.
  4. **원장 확인**: 실행 전후 `cost_report`의 `agent_fallback` 증가분이 0보다 큰지 확인(`PASS ledger agent_fallback_delta>0`)하고 호출 총 비용을 출력한다(센트 단위 상한 경고용).
  5. **요약 줄**: `BACKEND=<mock_param|down_alias|none>`. `mock_param`이 통과하면 그것, 아니면 `down_alias`가 통과하면 그것, 둘 다 실패하면 `none`과 종료 코드 1. 이 값을 사전 등록 문서에 하나로 확정하는 것은 사용자와 이후 phase의 일이다.
- 모든 실패 줄은 `FAIL <단계> error=<예외 클래스> status=<HTTP 상태 또는 kind>` 형식이고 예외 메시지·본문·키·URL을 출력하지 않는다.
- `Makefile` 타깃 `smoke-fallback`(`.PHONY`, 주석: "compose LiteLLM 기동(설정 반영: `docker compose up -d litellm`)·`.env` 키 필요, 사용자 실행, 소액 호출"). `make verify`에는 넣지 않는다.

### 테스트 (가짜 transport·임시 원장)

단계별 성공·실패 조합(두 백엔드 모두 성공, `mock_param`만 거부, 둘 다 실패)에서 출력 줄·`BACKEND` 값·종료 코드를 단언한다. 출력에 키·`Authorization`·메시지 본문이 없다는 스캔을 둔다. 원장 증가분 판정은 임시 원장으로 검증한다.

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

- 이 step에서 실제 LiteLLM·OpenAI·Anthropic을 호출하지 마라. 이유: 실제 호출은 사용자가 실행한다.
- 스모크 출력에 응답 본문·키·URL·예외 메시지를 넣지 마라.
- `BACKEND`를 코드에 고정하지 마라. 이유: 사용자 실행 결과가 정한다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

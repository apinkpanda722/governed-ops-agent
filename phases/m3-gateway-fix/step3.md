# Step 3: smoke-fix

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·019), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조"), `/docs/THREAT_MODEL.md` (F6)
- 직전 phase(`m3-gateway`) 산출물: `/src/govops/gateway/client.py`, `/src/govops/gateway/faults.py`, `/src/govops/gateway/smoke.py`, `/src/govops/gateway/agent_key.py`, `/src/govops/costs.py`, `/infra/litellm/config.yaml`, `/infra/litellm/config.obs.yaml`, `/src/govops/replay/llm.py`
- `/src/govops/gateway/smoke.py`, `/tests/test_smoke_gateway.py`, `/src/govops/costs.py`(`cost_report`), `/Makefile`(`smoke-fallback`), 이전 step 산출물(`ChatResult`의 `deployment_id`·`model_group`·`attempted_fallbacks`·`body_model`·`unmapped_model`)

## 작업

`smoke-fallback`의 **가짜 통과**를 고친다. 지금은 fallback 단계의 원장 검사가 "`agent_fallback` 증가 > 0"이어서, 미등록 모델을 양쪽 공급사에 정산하는 보수 규칙 때문에 우연히 만족됐다. TDD로 테스트를 먼저 쓴다.

### 진단 출력 (실제 값 확인용)

각 단계가 끝나면 한 줄을 더 낸다: `INFO <단계> body_model=<본문 model> model_id=<x-litellm-model-id> model_group=<x-litellm-model-group> attempted_fallbacks=<정수 또는 None> resolved=<ChatResult.model> unmapped=<true|false>`. 값은 모델명·배포 ID·그룹명·정수뿐이다. 키·Authorization·본문·URL은 출력하지 않는다.

### PASS 조건 (단계별 원장 증가분은 그 호출 전후의 `cost_report` 차이로 계산한다)

- **normal 단계**: 응답이 매핑됨(`unmapped=false`), **OpenAI(`agent_primary`) 증가 > 0 이고 Anthropic(`agent_fallback`) 증가 = 0**, `attempted_fallbacks`가 0 또는 None.
- **down_alias 단계(fallback)**: 응답 공급사가 Anthropic, 매핑됨(`unmapped=false`), **Anthropic 증가 > 0 이고 OpenAI 증가 = 0**, 미등록 모델 0건(`unmapped_model`이 한 번도 True가 아님), `attempted_fallbacks`가 1 이상 또는 None(헤더 없음 허용하되 `INFO`에 표시).
- 증가분 비교는 센트 단위 반올림 오차에 걸리지 않게 0 판정만 정확히 하고(`== 0`은 정산 행의 비용이 0.0인지로, `> 0`은 양수인지로) 호출 비용 합계를 출력한다.
- 단계가 실패하면 `FAIL <단계> reason=<model_mismatch|ledger_delta|unmapped|http|…> error=<예외 클래스> status=<HTTP 상태 또는 kind>`로 낸다(원인 코드를 구분한다). 마지막 줄 `BACKEND=down_alias`는 두 단계가 모두 통과했을 때만, 아니면 `BACKEND=none`과 종료 코드 1이다. 단계 이름·출력 형식은 기존 것을 가능한 한 유지한다.

### 테스트 (가짜 transport, 실제 LiteLLM 응답 형태 픽스처: 본문 model=별칭 + 헤더 배포 ID)

- 정상 경로 PASS(normal: OpenAI만 증가, fallback: Anthropic만 증가).
- **가짜 통과 방지**: 미등록 모델로 양쪽 공급사에 정산되어 Anthropic 증가가 > 0이어도 `unmapped`이면 fallback 단계는 FAIL(`reason=unmapped`)이다. fallback 단계에서 OpenAI 증가가 > 0이면 FAIL(`ledger_delta`). normal 단계에서 Anthropic 증가가 > 0이면 FAIL.
- 응답이 요청과 다른 공급사로 매핑되면 FAIL(`model_mismatch`).
- 출력 전체에 키·`Authorization`·본문이 없다(스캔 테스트).

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

- 원장 증가 검사를 "증가 > 0" 하나로 두지 마라. 이유: 미등록 모델의 양쪽 정산 때문에 우연히 만족되어 가짜 통과가 된다. 공급사별 증가량과 미등록 0건을 함께 본다.
- 진단 출력에 키·본문·URL·예외 메시지를 넣지 마라.
- 이 step에서 실제 LiteLLM·공급사에 접속하지 마라(가짜 transport 테스트만).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

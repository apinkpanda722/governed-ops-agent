# Step 1: model-prices

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·008·009·018), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조", "상태 관리"의 비용 원장), `/docs/THREAT_MODEL.md` (F6)
- `/src/govops/costs.py`, `/tests/test_costs.py`, `/tests/test_costs_reservation.py`, `/tests/test_costs_report.py`, `/src/govops/gateway/client.py`(`_MODEL`, `_usage_and_cost`), `/docs/ADR.md`(ADR-007)

## 작업

fallback 모델의 **단가**와 **응답 모델 ID → 공급사·단가 매핑**을 만든다. TDD로 테스트를 먼저 쓴다. 게이트웨이·LiteLLM 설정은 다음 step이다.

### 단가 (`costs.py`의 `PRICES`에 추가)

fallback 모델은 Claude Sonnet 5.5, 모델 ID `claude-sonnet-5-5`(LiteLLM 모델 문자열 `anthropic/claude-sonnet-5-5`)다.

| 키 | 단가 |
|---|---|
| `anthropic.claude-sonnet-5-5.input` | $2 / MTok |
| `anthropic.claude-sonnet-5-5.output` | $10 / MTok |

- 두 항목 모두 `source_url="https://platform.claude.com/docs/en/about-claude/pricing"`, `checked_on="2026-10-05"`. 캐시 입력 단가는 등록하지 않는다. **Anthropic 비용 계산에서는 캐시 입력 토큰도 일반 입력 단가로 센다**(보수적, 과소 계상 방지).
- `anthropic_cost_usd(model, *, input_tokens, cached_input_tokens=0, output_tokens) -> float`를 추가한다(입력 토큰은 캐시 입력을 포함한 전체). 기존 `token_cost_usd`(OpenAI)의 시그니처와 동작은 바꾸지 않는다.
- 4.7 이후 Anthropic 모델은 같은 텍스트에 토큰이 약 30% 더 나온다(새 토크나이저). 이 사실은 ADR(마지막 step)에 적고, 호출 전 추정은 이미 입력 상한을 크게 잡으므로(`estimate_call_usd`의 입력 상한 2배+1024) 별도 보정 계수를 추가하지 않는다.

### 응답 모델 ID 매핑

```python
@dataclass(frozen=True) class ResolvedModel: supplier: str | None; family: str | None; price_key: str | None; mapped: bool
def resolve_model(response_model: str) -> ResolvedModel
def response_cost_usd(response_model: str, *, input_tokens, cached_input_tokens, output_tokens) -> tuple[float, ResolvedModel]
```

- **매핑 규칙: 정확 일치 + 등록된 접두어(날짜 접미사 허용)**. 등록 모델: `gpt-6-luna`(openai), `claude-sonnet-5-5`(anthropic). 응답 모델 ID가 등록 ID와 **같거나**, 등록 ID 뒤에 날짜 접미사(`-YYYYMMDD` 또는 `-YYYY-MM-DD`)만 붙은 것이면 매핑된다. 그 밖(다른 접미사, 다른 모델, 대소문자 다름, 빈 문자열)은 **매핑되지 않음**(`mapped=False`). 부분 문자열·임의 접두어 일치는 허용하지 않는다.
- 매핑되지 않은 모델은 **비싼 단가**로 정산한다: 같은 토큰 수로 등록된 모든 모델의 비용을 계산해 **최댓값**을 비용으로 쓰고(`response_cost_usd`가 `mapped=False`로 돌려준다), 이 경우 호출자가 두 공급사 예약을 모두 이 금액으로 정산한다(다음 step). 경보는 호출자(다음 step)가 낸다.
- 등록 모델 표는 코드 상수 한 곳에 둔다. 새 모델 추가는 이 표와 `PRICES`만 고치면 된다.

### 테스트

- 단가·비용 계산(캐시 입력 포함 일반 입력 단가), 기존 OpenAI 비용·예약·보고 테스트 불변.
- 매핑: 정확 일치, 날짜 접미사 두 형식, 알 수 없는 접미사·다른 모델·대소문자·빈 문자열은 `mapped=False`, 부분 문자열 일치 불가.
- 매핑 실패 시 비용이 등록 모델 중 최대 단가로 계산됨.
- 단가 항목에 `source_url`·`checked_on`이 있다.

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

- `PRICES`에 확인하지 않은 단가·`valid_until`을 넣지 마라. 이유: 이 step의 값은 위 표가 전부다.
- `token_cost_usd`(OpenAI)의 동작을 바꾸지 마라. 이유: 기존 원장·게이트웨이 테스트가 의존한다.
- 접두어 일치를 느슨하게 만들지 마라(`startswith`만으로 매핑). 이유: 알 수 없는 모델이 싼 단가로 정산될 수 있다.
- 이 step에서 게이트웨이 클라이언트·LiteLLM 설정을 고치지 마라(다음 step).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

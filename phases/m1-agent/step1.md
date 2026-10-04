# Step 1: gateway-client

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `gateway`·`agent` 행, 아키텍처 규칙의 "에이전트의 모델 호출은 gateway 클라이언트로만"·비용 상한 항목)
- `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조"의 호출자 식별, "배치도")
- `/docs/ADR.md` (ADR-007, ADR-008의 호출자 식별 부분)
- step 0 산출물: `/src/govops/costs.py`(`CostLedger`, `estimate_call_usd`, `token_cost_usd`, `BudgetExceededError`, `LedgerMissingError`), `/tests/test_costs.py`
- `/infra/litellm/config.yaml`(모델 별칭 `agent-primary`), `/.env.example`(`LITELLM_BASE_URL`, `LITELLM_AGENT_KEY` 이름과 값 형식 주석), `/src/govops/gateway/__init__.py`, `/tests/test_architecture.py`
- 설치된 `openai` SDK(`.venv`의 `site-packages/openai`, 읽기만)의 `AsyncOpenAI`·`chat.completions.create` 시그니처와 예외 클래스

## 작업

에이전트의 **유일한 모델 경로**인 LiteLLM(OpenAI 호환) 비동기 클라이언트를 `gateway` 계층에 만든다. 실제 호출은 하지 않고 가짜 transport로 테스트한다. TDD로 테스트를 먼저 쓴다.

### 설계 (확정)

- 인증: **`LITELLM_AGENT_KEY`(에이전트 전용 가상 키)만** 쓴다. `LITELLM_MASTER_KEY`는 이 클라이언트가 읽거나 참조하지 않는다. 주소는 `LITELLM_BASE_URL`(기본 `http://127.0.0.1:4000`). 모델 별칭은 `agent-primary`.
- 호출자 식별(ADR-008): 요청에 OpenAI `user` 필드 = 사용자 ID, 메타데이터에 task ID(`extra_body={"metadata": {"task_id": ...}}`). 사용자 ID는 **메시지 내용에 넣지 않는다**(요청 메타 필드로만 간다).
- 비용: LiteLLM 응답 헤더가 아니라 **응답의 사용량 토큰 × 단가표**(step 0 `token_cost_usd`)로 B가 계산한다. 호출 **직전**에 `CostLedger.check_before_call`로 상한을 검사한다.
- 이 step에서는 fallback·Guardrail이 없다(M3·M4). 주 모델 `agent_primary`만 기록한다.

### 만들 파일: `src/govops/gateway/client.py`

```python
@dataclass(frozen=True)
class Usage: input_tokens: int; cached_input_tokens: int; output_tokens: int

@dataclass(frozen=True)
class ChatResult:
    message: dict          # {"role": "assistant", "content": str|None, "tool_calls": [{"id","name","arguments"(JSON 문자열)}...]}
    model: str
    usage: Usage
    cost_usd: float

class GatewayError(Exception): ...     # 메시지에 키·헤더·요청 본문을 넣지 않는다(HTTP 상태나 사유 종류만)

class ChatClient(Protocol):
    async def complete(self, *, messages: Sequence[Mapping[str, Any]], tools: Sequence[Mapping[str, Any]],
                       task_id: str, user_id: str, max_output_tokens: int = 1024) -> ChatResult

class LiteLLMChatClient:
    def __init__(self, *, base_url: str | None = None, api_key: str | None = None,
                 ledger: CostLedger | None = None, http_client: httpx.AsyncClient | None = None,
                 timeout_s: float = 60.0)
    # api_key 기본값: 환경 변수 LITELLM_AGENT_KEY (없으면 GatewayError("LITELLM_AGENT_KEY가 설정되지 않았습니다"), 키 값은 포함하지 않는다)
    async def complete(...) -> ChatResult
```
`complete`의 순서:
1. 키 설정 확인 → 요청 본문의 최악 입력 토큰 수 상한(직렬화 길이 기반의 보수적 상한)과 `max_output_tokens`로 `estimate_call_usd` 계산 → `ledger.check_before_call(purpose="agent_primary", estimate_usd=...)`. 상한 초과(`BudgetExceededError`)나 원장 없음(`LedgerMissingError`)이면 **네트워크 요청을 보내지 않고** 예외를 그대로 올린다.
2. `AsyncOpenAI`(주입된 `http_client` 사용)로 `chat.completions.create` 호출.
3. 응답에서 메시지·`tool_calls`·사용량을 꺼내 `ChatResult` 생성. 사용량 필드가 없으면 추정액을 비용으로 기록하고 계속한다(과소 계상 금지).
4. `ledger.record(purpose="agent_primary", model="gpt-6-luna" 등 단가표 모델명, task_id, 토큰 수, cost_usd)`.
- 타임아웃은 임시 상수다(타임아웃·재시도·서킷·fallback 정책은 M3). **재시도하지 않는다.**
- SDK 예외(연결·타임아웃·401·429·5xx)는 `GatewayError`로 감싸고 `str`·`repr`·`args`에 키·요청 헤더·본문이 들어가지 않게 한다.
- 키·헤더·본문은 로그에 남기지 않는다(이 모듈은 로깅하지 않는다).

### 테스트 (`tests/test_gateway_client.py`)

모든 HTTP는 `httpx.MockTransport`를 `http_client`로 주입한다. 원장은 `tmp_path`.
- 요청 검사: 경로 `/chat/completions`, `Authorization: Bearer <에이전트 키>`, 본문의 `model == "agent-primary"`, `user == <사용자 ID>`, 메타데이터 `task_id`, `tools`가 그대로 전달, **메시지 내용에 사용자 ID가 없다**.
- **마스터 키 비사용**: 환경에 `LITELLM_MASTER_KEY=<센티널>`을 설정해도 어떤 요청 헤더·본문에도 센티널이 없다. `gateway/client.py` 소스에 `LITELLM_MASTER_KEY` 문자열이 없다.
- 응답 파싱: 텍스트 응답, `tool_calls` 응답(여러 개), 사용량·캐시 토큰. 원장에 `agent_primary` 행이 1줄 생기고 `cost_usd`가 단가표 계산과 같다.
- 사용량 필드 없는 응답은 추정액으로 기록한다.
- 예산: 원장을 상한 직전까지 채우면 `BudgetExceededError`이고 **transport가 받은 요청 수가 0**이다. 원장 파일이 없으면 `LedgerMissingError`이고 요청 0건.
- 오류: 401·429·500·연결 실패·타임아웃이 `GatewayError`이고, 그 `str`·`repr`·`args`·`caplog`(DEBUG, `httpx`·`httpcore`·`openai` 포함)에 에이전트 키 센티널이 없다. 요청이 실패한 경우 원장에 행이 생기지 않는다.
- 키 없음: `LITELLM_AGENT_KEY`가 비어 있으면 요청 전에 `GatewayError`.
- `ChatClient` Protocol을 만족하는 가짜 클라이언트를 테스트 코드에서 쉽게 만들 수 있다(후속 step이 재사용).
- 계층 가드 통과(`gateway`는 agent·tools·broker·api를 import하지 않는다. `govops.costs`와 `openai`는 허용).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 모델 호출이 이 클라이언트 하나를 통하고, 호출 직전에 누적액 검사가 일어나는가?
   - 마스터 키를 쓰지 않는가? 키·헤더가 예외·로그에 없는가?
   - 사용자 ID가 요청 메타 필드로만 가고 메시지 내용에는 없는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `LITELLM_MASTER_KEY`를 읽거나 참조하지 마라. 이유: 앱은 에이전트 전용 가상 키만 쓴다(마스터 키 발급 도구는 다음 step의 별도 CLI뿐).
- 실제 LiteLLM·OpenAI에 요청하지 마라. 이유: 하네스 step은 mock transport만 쓴다.
- 재시도·fallback·서킷·Guardrail을 구현하지 마라. 이유: 각각 M3·M4 범위다.
- 비용을 응답 헤더(`x-litellm-response-cost` 등)에서 읽지 마라. 이유: B 원장은 사용량 × 단가표로 계산한다.
- 키·Authorization 헤더·요청·응답 본문을 로그·예외·원장에 남기지 마라.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- 저장소의 `data/manifest/`에 원장을 만들지 마라(테스트는 `tmp_path`).
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

# Step 2: resilience

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-006), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이"의 실행 상태 그림, "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` ("5. 장애 주입 시나리오")
- `/src/govops/tools/rest.py`, `/src/govops/tools/jira.py`, `/src/govops/tools/slack.py`, `/src/govops/tools/a_mcp.py`, `/src/govops/tools/registry.py`, `/src/govops/obs/trace.py`(`SpanKind.RETRY`·`CIRCUIT_STATE`), `/src/govops/agent/graph.py`(도구 오류가 payload로 바뀌는 곳)
- 이전 step 산출물: `/src/govops/tools/rest.py`의 오류 분류(`kind`, `retry_after`), `/src/govops/tools/markers.py`

## 작업

도구 호출의 **타임아웃·재시도·서킷**을 `tools/resilience.py`에 만든다. 시계·sleep·난수는 주입 가능하게 한다(테스트가 실시간 대기 없이 돈다). TDD로 테스트를 먼저 쓴다.

### 값(모듈 상수 하나에 모은다. 초안값이며 사전 등록 문서에서 확정된다)

| 항목 | 값 | 적용 대상 |
|---|---|---|
| Soft Timeout | 3.0초 | **도구 호출(읽기·쓰기)에만**. 초과해도 중단하지 않고 span 속성 `soft_timeout_exceeded=true`로 기록 |
| Hard Timeout | 10.0초 | 도구 호출(읽기·쓰기)에만. 초과하면 중단 |
| 읽기 재시도 | 최대 3회 시도 | 읽기 도구 |
| 백오프 | base 0.5초, ×2, 상한 4.0초, full jitter | 재시도 대기 |
| `Retry-After` | 값 그대로 준수, 30초 상한 | 429 |
| 서킷 | 연속 5회 실패 시 열림, 30초 뒤 half-open(시험 호출 1회) | **대상 서비스별**: `jira`·`slack`·`a_mcp` |

**모델 호출 타임아웃은 이 값 표에 없다.** 모델 호출(게이트웨이)에는 적용하지 않는다(M3 뒤 phase에서 별도 정한다).

### 인터페이스

```python
class FailureKind(StrEnum): CONNECT; RATE_LIMITED; UNCERTAIN; DEFINITIVE; CIRCUIT_OPEN
def classify(exc: Exception) -> FailureKind
class CircuitBreaker: ...        # 대상 서비스별 인스턴스. 상태: closed/open/half_open. 시계 주입
class ToolPolicy:                # 생성자: soft=SOFT, hard=HARD(기본값은 값 표 상수), 시계·sleep·jitter 난수 주입
    async def call_read(self, service: str, fn: Callable[[], Awaitable[T]]) -> T
    async def call_once(self, service: str, fn: Callable[[], Awaitable[T]], *, hard_timeout: float = HARD) -> T   # 쓰기용: 재시도 없이 타임아웃·서킷만 적용
```

- `ToolPolicy`는 soft/hard 값을 생성자에서 받는다(기본값은 위 값 표 상수이고 상수는 한 곳에만 둔다). 테스트는 작은 값(예: hard 0.05초, 응답 지연 0.2초)을 넘겨 Hard Timeout을 검증해도 된다. 상수 값을 테스트에서 monkeypatch하지 않는다.
- 분류: 연결 수립 실패(`kind == "connect"`) → `CONNECT`, 429 → `RATE_LIMITED`, 읽기·쓰기 타임아웃·`transport` 오류·5xx → `UNCERTAIN`, 그 밖의 4xx(401·403·404 등) → `DEFINITIVE`, 서킷 열림 → `CIRCUIT_OPEN`(대상 서비스를 부르지 않고 즉시 실패).
- `call_read`: `CONNECT`·`RATE_LIMITED`·`UNCERTAIN`은 최대 3회 시도(백오프+jitter, `Retry-After` 준수), `DEFINITIVE`는 재시도하지 않고 그대로 올린다. 읽기는 표식 조회를 하지 않는다.
- `call_once`는 **재시도하지 않는다**. 쓰기 재시도는 다음 step의 실행기가 `classify`와 이 정책의 대기 계산(`backoff(n)`, `retry_after` 상한 적용)을 써서 직접 한다. 그러므로 `backoff`·`cap_retry_after`를 공개 메서드로 노출한다.
- 서킷은 연속 실패만 센다(성공하면 0으로). `DEFINITIVE`(4xx)는 서비스 장애가 아니므로 실패로 세지 않는다.
- 상태 변화·재시도는 `obs.trace`의 `SpanKind.RETRY`·`SpanKind.CIRCUIT_STATE` span으로 남긴다(span 속성에 토큰·본문을 넣지 않는다).
- 서킷 열림 후 읽기 호출의 사용자 노출용 오류는 새 예외 `ToolUnavailable`(메시지 없음 또는 고정 코드 `"circuit_open"`)로 올린다. `agent/graph.py`는 이미 예외를 `{"error": {"code": "tool_unavailable"}}`로 바꾸므로 **그래프 코드는 이 step에서 수정하지 않는다**.

### 적용

- `ToolRuntime`(`tools/registry.py`)의 읽기 호출(A MCP·mock-jira 읽기)을 `ToolPolicy.call_read`로 감싼다. A MCP 호출 한 번이 Hard Timeout을 넘으면 중단한다. 대상 서비스 이름은 `a_mcp`·`jira`·`slack`로 고정한다. `ToolRuntime` 생성자 시그니처는 기본값으로 호환을 유지한다(기본 정책 인스턴스).
- 기존 읽기 도구의 정상·오류 응답 payload 형식은 바꾸지 않는다.

### 테스트

- 분류표 파라미터화 테스트(위 5종), 서킷 닫힘→열림→half-open→닫힘/재열림, DEFINITIVE는 서킷에 안 센다, `Retry-After` 30초 상한, 백오프 상한·jitter 범위, Soft 초과는 중단하지 않고 속성만 남김, Hard 초과는 중단.
- 읽기: UNCERTAIN은 3회까지 재시도하고 표식 조회를 하지 않는다. 서킷 열림 뒤 호출은 대상 서비스에 요청을 보내지 않는다(요청 수 0).
- 기존 `test_tool_runtime`·`test_a_mcp_client` 등이 그대로 통과한다. 키·네트워크 없이 통과한다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계(`tools/`는 `agent`·`langgraph` 등을 import하지 않음)를 따르는가?
   - 값 표 외의 숫자를 코드 곳곳에 흩어 두지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 모델 호출(게이트웨이)에 Soft/Hard Timeout을 적용하지 마라. 이유: 도구 호출 타임아웃(M2 실측 최대 663ms)과 모델 호출(1.4~4.6초)은 값이 다르다. 모델 타임아웃은 별도 결정이다.
- 쓰기 호출에 5xx·타임아웃 자동 재시도를 넣지 마라. 이유: 결과가 불확실한 실패는 표식 조회 없이 재시도하면 중복 부작용이 된다(AGENTS.md CRITICAL).
- 서킷을 audience(자격 증명 대상)나 사용자별로 나누지 마라. 이유: 서킷은 대상 서비스의 장애 상태이며 V15의 독자 범위 개념과 무관하다.
- 실시간 `sleep`에 의존하는 테스트를 만들지 마라. 이유: 시계·sleep 주입으로 결정론이어야 한다.
- 오류 문자열·span에 토큰·응답 본문을 넣지 마라.
- `agent/graph.py`를 수정하지 마라. 이유: 이 step은 도구 계층만 다룬다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드를 약화하지 마라.

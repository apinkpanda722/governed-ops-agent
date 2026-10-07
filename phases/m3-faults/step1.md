# Step 1: a-client-status

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/tools/a_mcp.py`(`AMcpClient._request`·`_failure`·httpx 이벤트 훅 `authorize`·`observe_status`), `/src/govops/tools/resilience.py`(`classify`·`FailureKind`·`ToolPolicy`·재시도·서킷), `/tests/test_a_mcp_client.py`, `/tests/test_resilience.py`, `/tests/conftest.py`(`serve_asgi`·`temporary_a_tokens`)
- **외부 계약 실측**(2026-10-06, 로컬 루프백 서버·합성 토큰으로 `AMcpClient` 호출. 실측 스크립트는 저장소 밖 스크래치에 있었으므로 이 step에서 같은 시나리오를 회귀 테스트로 옮긴다):
  - 연결 거부(닫힌 포트) → `kind=connect` → `classify=CONNECT`(0.02초)
  - HTTP 500·503·429 → **모두 `kind=protocol`, `status=None`** → `classify=DEFINITIVE`(재시도·서킷 미작동, `Retry-After` 무시). MCP SDK(`mcp>=2.3.0`)가 비정상 HTTP 응답을 `MCPError(code=-32603, "Server returned an error response")`를 담은 `ExceptionGroup`으로 바꾸고 상태 코드를 버린다.
  - **(첫 시도에서 추가로 관측)** 같은 닫힌 포트·응답 지연 시나리오가 **타이밍에 따라 `connect`/`timeout` 또는 `protocol`로 갈린다**(느린 시작을 주입하면 `protocol`, 지연 시나리오도 `protocol`). MCP SDK는 연결을 백그라운드 태스크에서 열고, `ConnectError`·타임아웃을 그대로 올리기도 하고 연결 종료류 프로토콜 오류로 감싸 올리기도 한다. `protocol`이면 `classify`가 `DEFINITIVE`가 되어 재시도하지 않고 서킷 실패 횟수가 0으로 되돌아간다(A가 죽어도 서킷이 열리지 않을 수 있다). 근거: `phases/m3-faults/step1-output.json`(첫 시도 기록)·이전 시도의 `blocked_reason`.
  - 요청을 읽은 뒤 연결 리셋 → `kind=transport`(`UNCERTAIN`). 요청 전 닫힘 → `transport`. 응답 지연 > timeout → `kind=timeout`(`UNCERTAIN`, 1.5초 timeout 기준 약 1.5초)

## 작업

**배경**: 위 실측대로 A가 5xx·429로 응답해도 클라이언트가 이를 `DEFINITIVE`(재시도 없음)로 분류한다. 또 닫힌 포트·응답 지연도 SDK가 오류를 감싸는 방식이 타이밍에 따라 달라져 `connect`/`timeout`이 `protocol`로 분류될 수 있다(첫 시도에서 관측). F11("읽기 재시도 → 서킷 열림 → 일시적으로 조회할 수 없음")이 성립하려면 **전송 계층에서 직접 기록한 사실(전송 오류 종류·비정상 상태 코드·`Retry-After`)이 SDK 예외 해석보다 먼저** 분류에 쓰여야 한다. 이는 **운영 동작을 바꾸는 의도된 수정**이다(실제 A의 5xx·429와 연결 거부·타임아웃을 안정적으로 재시도·서킷 대상으로 본다). 401 처리는 바꾸지 않고, 기록된 사실이 없을 때의 기존 예외 해석(`isinstance` 분기)은 폴백으로 남긴다. TDD로 회귀 테스트를 먼저 쓴다.

### 1. 수정 (`tools/a_mcp.py`) — SDK 예외 형태가 아니라 **전송 계층에서 직접 기록한 사실**로 분류한다

SDK가 같은 전송 오류를 타이밍에 따라 다르게 감싸므로, 예외 해석에 의존하지 않는다. `AMcpClient`가 쓰는 httpx 클라이언트의 **전송 계층을 감싸** 요청 단위로 사실을 기록한다(이미 있는 401 응답 훅과 같은 호출 단위 범위, 요청마다 초기화).

- **전송 오류 기록**: 전송 계층 래퍼가 `httpx2.ConnectError`·`httpx2.ConnectTimeout` → `connect`, `httpx2.ReadTimeout`·`httpx2.WriteTimeout` → `timeout`, 그 밖의 `httpx2.TransportError`(읽기 오류·리셋·요청 전 닫힘 등) → `transport`로 **마지막 한 건**을 기록한다. 래퍼는 예외를 그대로 다시 올린다(동작은 바꾸지 않는다).
- **비정상 HTTP 상태 기록**: 응답 훅이 마지막 비정상(2xx가 아닌) 상태 코드와 `Retry-After` 값을 기록한다.
- **`_failure()` 우선순위**: ① 401(기존 경로, 그대로) → ② 기록된 전송 오류(`connect`·`timeout`·`transport`) → ③ 기록된 비정상 HTTP 상태 → `("http", status, retry_after)` → ④ 기존 예외 해석(`isinstance` 분기) → ⑤ `protocol`. 한 호출에서 전송 오류와 비정상 상태가 모두 기록되면 ②가 이긴다(실제로 서버에 닿지 못했거나 응답을 못 받은 사실이 더 강하다).
- 기록하는 것은 **종류·상태 코드·`Retry-After` 값뿐**이다. 요청·응답 본문, 요청 헤더(`Authorization`), 예외 메시지, 토큰은 기록·저장하지 않는다.
- `tools/resilience.classify`에서 `("http", 5xx)` → `UNCERTAIN`(읽기라 재시도 가능), `("http", 429)` → `RATE_LIMITED`(`Retry-After` 준수)가 되는지 확인한다. 필요한 경우에만 최소로 고친다.
- 이미 `tools/rest.py`가 쓰는 `_retry_after` 파서를 재사용하되, `tools/` 안에서만 import한다(계층 경계).

### 2. 회귀 테스트 (로컬 루프백·합성 토큰만, 외부 호출 금지)

원시 TCP 또는 ASGI 로컬 서버로 아래 7개 시나리오를 만든다. 서버는 요청 헤더를 어디에도 기록하지 않는다.

| 시나리오 | 기대 `kind` | 기대 `classify` |
|---|---|---|
| 연결 거부(닫힌 포트) | `connect` | `CONNECT` |
| HTTP 500 | `http`, status 500 | `UNCERTAIN` |
| HTTP 503 + `Retry-After` | `http`, status 503, retry_after 보존 | `UNCERTAIN` |
| HTTP 429 + `Retry-After` | `http`, status 429, retry_after 보존 | `RATE_LIMITED` |
| 요청을 읽은 뒤 리셋 | `transport` | `UNCERTAIN` |
| 요청 전 닫힘 | `transport` | `UNCERTAIN` |
| 응답 지연 > timeout | `timeout` | `UNCERTAIN` |

- **7개 시나리오 각각을 두 조건에서 실행해 같은 `kind`·`classify`가 나오는지 단언한다**: (a) 지연 없음, (b) **느린 시작**(`httpx2.AsyncClient.__aenter__` 앞에 `asyncio.sleep(0.05 * n)`를 넣어 SDK 백그라운드 태스크의 시작을 늦춤, n = 0~4 여러 값). 한 조건에서만 통과하면 실패다.
- 입력을 고정하거나 보조 단언(전송 계층 호출 여부 같은 우회 관측)으로 결과를 맞추지 않는다. 최종 `AMcpError.kind`·`status`·`retry_after`와 `classify` 결과를 직접 단언한다(FP-011). 단언을 통과시키려고 timeout 값·sleep을 조정하지 않는다(타임아웃 값은 실측 기준: 1.5초 timeout, 지연 서버는 그보다 길게).
- **서킷**: 연결 거부가 **연속 5회**면 서킷이 열리고(서비스 `a_mcp`, 5회 실패/30초), 열린 뒤 호출은 서버에 닿지 않는다(서버 쪽 요청·연결 수가 늘지 않는다). 5xx·429 서버를 상대로 **읽기 재시도 횟수**(읽기 3회)와 서킷 열림도 단언한다. 가상 시간 폴링이 필요하면 `real_tick`처럼 실제로 조금 기다린다(FP-032). 연결 거부가 `protocol`(`DEFINITIVE`)로 분류되면 서킷 실패 횟수가 0으로 되돌아가는 회귀도 느린 시작 조건에서 막는다.
- 401 경로 회귀: 기존 401 테스트가 그대로 통과하고 토큰 재발급 동작이 변하지 않는다.
- 기존 `tests/test_a_mcp_client.py`는 **수정 없이** 통과해야 한다.
- 요청 간 상태 격리: 앞 호출의 기록(전송 오류·비정상 상태)이 다음 호출에 새지 않는다.

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
   - 사용자 개입 필요 또는 지시가 서로 충돌해 판단할 수 없음 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 401 경로를 바꾸지 마라. 이유: 기존 계약 테스트와 토큰 재발급 동작이 의존한다. `connect`·`timeout`·`transport`·5xx·429 분류는 이 step의 수정 대상이다(전송 계층 기록 사실을 우선한다).
- SDK가 올려 준 예외 형태(`ExceptionGroup`·`MCPError` 구조)에만 기대어 분류하지 마라. 이유: SDK는 같은 전송 오류를 타이밍에 따라 다르게 감싼다(FP-020 실측 계약). 전송 계층이 직접 기록한 사실이 예외 해석보다 우선한다.
- 타이밍 의존 실패를 테스트의 sleep·timeout 값·관측 지점 조정으로 덮지 마라(FP-011). 이유: 같은 입력에서 결과가 갈리는 것이 실제 결함이다. 한 조건에서만 통과하는 테스트는 통과가 아니다.
- 응답 본문·요청 헤더·토큰을 훅·로그·예외 메시지에 남기지 마라. 이유: AGENTS.md CRITICAL(토큰 비노출). 상태 코드·`Retry-After` 값만 보관한다.
- A 계약 스냅샷(`data/contract/`)을 수정하지 마라(AGENTS.md CRITICAL).
- F11 경로(chaos A 서버·클라이언트·하네스)에서 `KODOCS_MCP_TOKENS_FILE`과 실제 A 사용자 토큰을 읽거나 보내지 마라. 합성 토큰만 쓴다. chaos 서버는 요청 헤더(특히 `Authorization`)를 기록·출력·저장하지 않는다. 이유: 장애를 주입하는 서버는 외부 프로세스 역할이고 실제 자격 증명이 닿으면 안 된다(AGENTS.md CRITICAL: 토큰을 로그·trace에 남기지 않는다). 테스트로 강제한다.
- 주입한 장애가 시험하려는 경로를 타지 않는 시나리오를 만들지 마라(FP-005). 이유: 장애가 방어 로직을 실행시키지 않으면 중복 0이 아무 일도 안 일어나서 저절로 나온다. 시나리오마다 그 경로가 실제로 실행됐다는 증거(조회 호출 수 > 0, 대기 발생, 재시도 횟수)를 단언한다. F10은 항상 F5와 결합한다.
- 외부 서비스(A MCP·LiteLLM·mock)의 응답 형태를 추측으로 만들어 그 추측만 검증하지 마라(FP-020). 이유: 틀린 추측은 틀린 테스트로 통과한다. 이 step이 의존하는 실측 결과(읽기 목록의 외부 계약 실측)를 픽스처로 쓰고, 실측 값이 없는 항목은 단정하지 말고 `blocked`로 보고한다.
- git 이력·시간·I/O 지연에 의존하는 테스트를 로컬 통과만으로 끝내지 마라(FP-032). 이유: CI(얕은 복제·느린 디스크)에서 달라진다. 가상 시간 폴링은 `real_tick`처럼 실제로 조금 기다리고, 느린 I/O를 주입해 재현한 뒤 고친다.
- 이 step이 의존하는 외부 지점을 실측 없이 가정하지 마라(FP-050). 이유: 승인 뒤 live에서 결함이 드러나 수정이 늘었다. 읽기 목록의 실측 근거를 따르고 다른 동작을 관측하면 가정하지 말고 보고한다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

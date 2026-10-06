# Step 1: a-client-status

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/tools/a_mcp.py`(`AMcpClient._request`·`_failure`·httpx 이벤트 훅 `authorize`·`observe_status`), `/src/govops/tools/resilience.py`(`classify`·`FailureKind`·`ToolPolicy`·재시도·서킷), `/tests/test_a_mcp_client.py`, `/tests/test_resilience.py`, `/tests/conftest.py`(`serve_asgi`·`temporary_a_tokens`)
- **외부 계약 실측**(2026-10-06, 로컬 루프백 서버·합성 토큰으로 `AMcpClient` 호출. 실측 스크립트는 저장소 밖 스크래치에 있었으므로 이 step에서 같은 시나리오를 회귀 테스트로 옮긴다):
  - 연결 거부(닫힌 포트) → `kind=connect` → `classify=CONNECT`(0.02초)
  - HTTP 500·503·429 → **모두 `kind=protocol`, `status=None`** → `classify=DEFINITIVE`(재시도·서킷 미작동, `Retry-After` 무시). MCP SDK(`mcp>=2.3.0`)가 비정상 HTTP 응답을 `MCPError(code=-32603, "Server returned an error response")`를 담은 `ExceptionGroup`으로 바꾸고 상태 코드를 버린다.
  - 요청을 읽은 뒤 연결 리셋 → `kind=transport`(`UNCERTAIN`). 요청 전 닫힘 → `transport`. 응답 지연 > timeout → `kind=timeout`(`UNCERTAIN`, 1.5초 timeout 기준 약 1.5초)

## 작업

**배경**: 위 실측대로 A가 5xx·429로 응답해도 클라이언트가 이를 `DEFINITIVE`(재시도 없음)로 분류한다. F11("읽기 재시도 → 서킷 열림 → 일시적으로 조회할 수 없음")이 성립하려면 상태 코드와 `Retry-After`가 분류까지 전달돼야 한다. 이는 **운영 동작을 바꾸는 의도된 수정**이다(실제 A의 5xx·429를 이제 재시도·서킷 대상으로 본다). 401 처리와 `kind=protocol`의 나머지 경로는 바꾸지 않는다. TDD로 회귀 테스트를 먼저 쓴다.

### 1. 수정 (`tools/a_mcp.py`)

- `AMcpClient`가 이미 401 감지에 쓰는 httpx 응답 이벤트 훅(`observe_status`)이 **마지막 비정상(2xx가 아닌) 상태 코드와 `Retry-After` 헤더 값**을 호출 단위(요청마다 초기화)로 기록하게 한다. 응답 본문·요청 헤더(`Authorization`)는 기록하지 않는다.
- `_failure()`가 `kind=protocol`로 분류할 상황에서, 관측된 비정상 상태 코드가 있으면 `("http", status, retry_after)`를 반환한다. 이 값이 `tools/resilience.classify`에서 `status`에 따라 5xx → `UNCERTAIN`(읽기라 재시도 가능), 429 → `RATE_LIMITED`(`Retry-After` 준수)로 분류되는지 확인한다. `classify`의 기존 분기를 쓰고, 필요한 경우에만 최소로 고친다.
- 401 경로, `connect`·`transport`·`timeout` 분류는 **그대로** 둔다.

### 2. 회귀 테스트 (로컬 루프백·합성 토큰만, 외부 호출 금지)

원시 TCP 또는 ASGI 로컬 서버로 아래 7개를 만들고 `AMcpClient`의 실제 결과를 단언한다. 서버가 요청 헤더를 어디에도 기록하지 않는다(F11 경로의 자격 증명 규칙과 같다).

| 시나리오 | 기대 `kind` | 기대 `classify` |
|---|---|---|
| 연결 거부(닫힌 포트) | `connect` | `CONNECT` |
| HTTP 500 | `http`, status 500 | `UNCERTAIN` |
| HTTP 503 + `Retry-After` | `http`, status 503, retry_after 보존 | `UNCERTAIN` |
| HTTP 429 + `Retry-After` | `http`, status 429, retry_after 보존 | `RATE_LIMITED` |
| 요청을 읽은 뒤 리셋 | `transport` | `UNCERTAIN` |
| 요청 전 닫힘 | `transport` | `UNCERTAIN` |
| 응답 지연 > timeout | `timeout` | `UNCERTAIN` |

- 읽기 재시도(`ToolPolicy`, 읽기 3회)와 서킷(서비스 `a_mcp`, 5회 실패/30초): 위 5xx·429 서버를 상대로 **재시도 횟수와 서킷 열림**을 단언한다(가상 시간 폴링이 필요하면 FP-032에 따라 실제로 조금 기다리거나 짧은 timeout을 쓴다). 서킷이 열린 뒤 호출이 서버에 닿지 않음(서버 쪽 요청 수가 늘지 않음)도 단언한다.
- 401 경로 회귀: 기존 401 테스트가 그대로 통과하고 토큰 재발급 동작이 변하지 않는다.
- 기존 `tests/test_a_mcp_client.py`는 **수정 없이** 통과해야 한다.

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

- 401 경로·`connect`·`transport`·`timeout` 분류를 바꾸지 마라. 이유: 이 step은 `kind=protocol`이던 5xx·429만 복원한다. 다른 경로를 바꾸면 기존 계약 테스트가 깨진다.
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

# Step 9: remove-vacuous-guard

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-022), `/docs/ARCHITECTURE.md`(장애 하네스 절), `/docs/results/hypotheses-m3-faults.md`, `/.claude/skills/failure-patterns/SKILL.md`(FP-005·021·023·050)
- `/src/govops/evalrun/faults.py`(`run_faults`의 시작 점검과 정리, `_invoke`, 안전 오류 코드 목록), `/src/govops/evalrun/mock_admin.py`(`MockAdmin.reset`·`faults_clear`·`faults_stats`), `/src/govops/mocks/faults.py`(**읽기 전용 참고**: `ENDPOINTS`, `FaultState.stats`·`reset`), `/src/govops/tools/a_mcp.py`·`/src/govops/tools/resilience.py`(`ToolPolicy.call_read`·서킷), `/src/govops/replay/chaos_a.py`, `/tests/test_evalrun_faults.py`(`test_stale_rules_fail_closed`, `test_each_f11_variant_has_transport_failure_evidence`, `Admin` 가짜), `/tests/test_docs_consistency.py`
- **외부 계약 실측**(원자료: `src/govops/mocks/faults.py`·`jira.py`·`slack.py`·`admin.py`). **성공 여부와 반환 내용을 따로 적는다**:

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| `faults clear` / `POST /faults` 삭제 | exit 0, `{"status":"ok"}` | `FaultState.reset()`이 규칙과 카운터를 모두 지운다 |
| `POST /reset`(mock `reset`) | exit 0 | `faults.reset()`을 호출해 규칙·카운터를 지우고 서비스 상태를 초기화한다(jira.py:72-74, slack.py:47-49) |
| `faults stats` | exit 0 | **요청 카운터만** `{endpoint: {"requests": n, "applied_rules": m}}`. 규칙 목록을 읽는 API는 없다. `clear` 직후에는 항상 `{}`이다 |
| 규칙이 없는 상태에서 읽기 엔드포인트 요청 1회 뒤 `faults stats` | exit 0 | `{endpoint: {"requests": 1, "applied_rules": 0}}` (`FaultState.select`가 요청 수를 세고 일치하는 규칙 수를 `applied_rules`에 더한다) |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/evalrun/faults.py`, `tests/test_evalrun_faults.py`, `docs/ARCHITECTURE.md`(장애 하네스 절에 문장 추가), `docs/ADR.md`(ADR-022에 항목 추가), `tests/test_docs_consistency.py`(문서 문구 확인 추가). 계층: `evalrun`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import할 수 없다.

**배경**: `run_faults`의 시작 점검은 `faults_clear` 직후 `faults_stats`가 비어 있는지 확인해 `stale_faults`로 중단한다. 위 실측대로 clear 직후 stats는 항상 `{}`이라 이 검사는 **절대 걸리지 않는다**(FP-005·FP-021 유형). 남은 규칙으로부터의 실제 보호는 **실행마다 하는 mock `reset`과 그 실패 처리**(`MockAdminError`)다. 이 step은 걸리지 않는 검사를 지우고, 보호가 행동으로 확인되는 점검 하나로 대체하고, 타이밍에 의존한 F11 테스트의 단언 대상을 결정적인 값으로 바꾼다. TDD.

### 1. `stale_faults` 가드 삭제 (`evalrun/faults.py`)

- 시작 시 `faults_clear` 직후의 `faults_stats` 비어 있음 검사(`raise ValueError("stale_faults")`)를 지운다. 그 검사만 지운다: 시작·종료 시 `faults_clear`와 실행마다의 `reset`은 그대로다.
- 정리 실패 경로(`finally`의 `faults_clear` 실패 → `manifest["cleanup_failed"] = True`)의 오류 코드는 `stale_faults`가 아니라 `cleanup_failed`로 바꾼다. 안전 오류 코드 목록에서 `stale_faults`를 빼고 `cleanup_failed`·아래 새 코드를 넣는다.
- 이 가드 전용 테스트 `test_stale_rules_fail_closed`를 삭제한다(검증 대상이 제거되므로 삭제 허용). 삭제한 테스트 이름과 이유를 최종 보고에 적는다.

### 2. 탐침 요청 행동 증거 점검 (하네스 시작 시 한 번, `evalrun/faults.py`)

- 규칙 목록을 읽을 수 없으므로 **행동으로 확인**한다: 시작 시 한 번, 양쪽 mock에 `reset`을 한 **직후**, mock당 부작용 없는 읽기 요청 하나(`mocks/faults.py`의 `ENDPOINTS` 중 읽기 엔드포인트, 예: `slack.channels`·`jira.ticket.get`을 mock REST 클라이언트로 호출)를 보낸 뒤 `faults_stats`에서 해당 엔드포인트의 `requests >= 1`이고 `applied_rules == 0`인지 확인한다. 하나라도 `applied_rules > 0`이면 `fault_rules_active`로 중단한다. `requests == 0`(탐침이 카운트되지 않음)이면 `probe_not_observed`로 중단한다(점검이 아무것도 보지 못한 채 통과하는 것을 막는다, FP-005).
- 탐침은 **읽기 엔드포인트만** 쓴다(쓰기 엔드포인트 금지). 점검 뒤 mock을 다시 `reset`한다(카운터 정리). 재개(`--resume`)에서도 시작 시 한 번 실행한다.
- 가짜 `Admin`은 실제 계약과 같이 카운터만 돌려준다(`reset`·`clear` 직후 `{}`, 요청 뒤 카운터 증가, 규칙 목록 조회 메서드 없음).

### 3. F11 delay 테스트의 단언 대상 교체 (`tests/test_evalrun_faults.py`)

`test_each_f11_variant_has_transport_failure_evidence`는 `server.requests == 3`(서버 도달 수)을 단언한다. 이 값은 SDK 시작 시간에 따라 달라지는 경쟁 조건이다(FP-023).

- **클라이언트 쪽 시도 횟수 == 3**을 단언한다. 시도 횟수는 클라이언트 쪽에서 결정적으로 셀 수 있는 값으로 센다(`policy.call_read`에 넘긴 호출 함수의 실행 횟수, 필요하면 전송 계층 기록). `circuits["a_mcp"].failures == 3`은 유지한다.
- **서버 수신 건수는 시도 횟수 이하로만** 확인한다(`server.requests <= attempts`). `refuse` 변형의 `server.requests == 0`은 결정적이므로 그대로다.
- **시간 값은 원래처럼 작게**(`ToolPolicy(hard=0.01)`, `delay=0.05`) 되돌려도 통과해야 한다. 이전 step이 바꾼 시간 값(`hard=0.5`, `delay=2.0`)은 되돌린다.
- **느린 시작 조건**에서도 실행한다: `httpx2.AsyncClient.__aenter__` 앞에 `asyncio.sleep(0.05 * n)`(n = 0~4)를 넣어 SDK 시작을 늦춰도 같은 단언이 통과한다(고치기 전 코드에서 서버 수신 단언이 실패함을 먼저 확인한다). 테스트에 sleep 주입을 넣는 것은 이 시작 조건 재현용으로 허용된다.

### 4. 문서

- `docs/ARCHITECTURE.md`의 장애 하네스 절에 "남은 규칙 보호는 실행마다 하는 `reset`(과 그 실패 처리)이 맡는다. mock은 설정된 규칙 목록을 읽는 API가 없고 `faults stats`는 요청 카운터만 돌려주므로, 하네스는 시작 시 `reset` 직후 탐침 요청 하나로 `applied_rules`가 0인지 행동으로 확인한다."를 쓴다. `docs/ADR.md`의 ADR-022에 같은 결정을 한 항목으로 추가한다(결정 내용만, 경위·누가 결정했는지는 쓰지 않는다). `tests/test_docs_consistency.py`가 두 문서에 이 문구가 있음을 확인한다.

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

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->

## 이 step의 금지사항

- `faults stats`가 설정된 규칙을 보여 준다고 가정하지 마라(FP-050). 이유: 위 외부 계약 실측대로 요청 카운터만 돌려주고 `clear` 직후에는 항상 `{}`이다. 규칙이 걸렸는지·없는지는 요청을 보내 `applied_rules`로 확인한다.
- 탐침 점검이 아무것도 보지 못한 채 통과하게 두지 마라(FP-005·FP-021). 이유: `requests >= 1`과 `applied_rules == 0`을 함께 확인하지 않으면 탐침이 카운트되지 않아도 통과한다. 탐침은 읽기 엔드포인트만 쓴다.
- F11 테스트의 단언을 느슨하게 하지 마라(`<=`로 전부 바꾸기·범위 비교·삭제, FP-056). 이유: 서버 도달 수가 아니라 단언 **대상**을 클라이언트 쪽 시도 횟수(결정적)로 바꾸는 것이다. `failures == 3`·`refuse`의 `== 0`은 유지하고 서버 수신 상한(`<= 시도 횟수`)도 단언한다(FP-023).
- 삭제는 `test_stale_rules_fail_closed` 하나뿐이다(검증 대상 가드를 제거했으므로). 다른 테스트를 삭제하거나 완화하지 마라(FP-056).
- 문서에 실행 결과 수치(성능·중복 건수·TCR)를 쓰지 마라(FP-043). 이유: live 실행 전이고 결과는 결과 문서가 쓴다.

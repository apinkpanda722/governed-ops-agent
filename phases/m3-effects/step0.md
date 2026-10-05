# Step 0: mock-faults

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-006, ADR-016), `/docs/THREAT_MODEL.md` ("5. 장애 주입 시나리오"), `/docs/ARCHITECTURE.md` ("배치도"의 mock 관리 접근, "도구와 부작용·멱등성")
- `/src/govops/mocks/` 전체(`jira.py`, `slack.py`, `service.py`, `admin.py`, `__main__.py`, `auth.py`), `/tests/test_mock_jira.py`, `/tests/test_mock_slack.py`, `/tests/test_mocks.py`, `/tests/test_compose.py`

## 작업

mock-jira·mock-slack에 **장애 주입**과 **본문 포함 상태 조회**를 추가한다. 이 step은 `mocks/`만 수정한다. TDD로 테스트를 먼저 쓴다.

### 장애 주입 (`src/govops/mocks/faults.py`)

- 엔드포인트 식별자: `slack.send`, `slack.history`, `jira.comment.create`, `jira.comment.list`, `jira.child.create`, `jira.child.list`(읽기 조회 `jira.ticket.get`·`jira.ticket.search`·`slack.channels`는 지연·5xx만 대상이어도 된다).
- 규칙은 엔드포인트별 **요청 순번**(1부터, 마지막 `set`/`reset` 이후 카운트)으로 건다. 한 규칙 = `{endpoint, nth: [순번…] | "all", action}`.
- action 종류(모두 결정론, 난수 없음):
  - `delay`: `ms` 만큼 응답을 늦춘다(처리는 정상). 처리 전 지연인지 처리 후 지연인지 `when: before|after_apply` 필드로 구분한다.
  - `status`: 지정한 5xx를 돌려준다. `when: before`(효과 미반영)와 `when: after_apply`(효과를 반영한 뒤 5xx)를 모두 지원한다.
  - `rate_limit`: 429와 `Retry-After: <초>` 헤더를 돌려준다(효과 미반영).
  - `drop_after_apply`: 효과를 반영한 뒤 **HTTP 응답을 보내지 않고 연결을 끊는다**(클라이언트는 읽기 오류를 본다).
  - `hide_for`: 이 요청이 만든 효과가 **조회 결과(`*.history`·`*.comment.list`·`*.child.list`)에는 `ms`가 지나야 보이게** 한다(`visible_at`). 상태 조회(`/state`)의 실제 건수에는 즉시 센다. 시계는 기존 `JiraState`·`SlackState`의 주입 가능한 `clock`을 쓴다.
- 서로 다른 규칙은 같은 요청에 겹칠 수 있다(예: `drop_after_apply` + `hide_for`). 겹침 규칙의 적용 순서를 문서화하고 테스트한다.
- `FaultState`는 jira·slack 상태 객체와 같은 프로세스 안에서 REST 앱과 관리 앱이 공유한다(`__main__.py`의 조립을 따라간다). 스레드 안전하게 만든다.
- 관리 앱(`create_admin_app`)에만 `POST /faults`(규칙 전체 교체), `DELETE /faults`, `GET /faults/stats`(엔드포인트별 받은 요청 수·적용된 규칙 수)를 추가한다. **REST 앱에는 장애 설정 경로를 만들지 않는다.** `POST /reset`은 규칙과 카운터도 초기화한다.
- CLI(`mocks/admin.py`): `faults set --file <json>`, `faults clear`, `faults stats`, `state --bodies`를 추가한다. 기존 `health`·`state`·`reset` 동작과 출력은 그대로 둔다.
- `state --bodies`(관리 앱 `GET /state?bodies=true`): 기본 `/state`는 본문을 해시만 보여 주고, `bodies=true`일 때만 메시지 `text`·코멘트 `body`·후속 티켓 `title`·`description`을 포함한다. 각 효과의 `metadata`는 항상 포함한다. 합성 데이터라 평문 본문을 허용하지만 이 옵션은 관리 앱에서만 열린다.

### 테스트

- 규칙별 단위 테스트(각 action, nth 선택, 카운터 리셋, 겹침 규칙).
- `drop_after_apply`는 실제 loopback 서버(`127.0.0.1` 임시 포트, uvicorn 스레드)로 한 번 검증한다: 클라이언트는 `httpx.ReadError`/`RemoteProtocolError`를 보고 `/state` 건수는 1이다.
- `hide_for`: 주입 시계를 움직여 조회에는 늦게 나타나고 `/state` 건수는 즉시 1임을 확인한다.
- 관리 포트 비공개 유지(`tests/test_compose.py`), REST 앱에 `/faults`가 없다는 테스트, 규칙 없을 때 기존 mock 테스트가 모두 그대로 통과.
- 키·네트워크(loopback 제외) 없이 통과한다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계(`mocks/`는 `govops`의 다른 계층을 import하지 않음)를 따르는가?
   - 관리 엔드포인트가 compose 내부 네트워크에만 열려 있는가?
   - AGENTS.md CRITICAL 규칙(토큰 비노출, 실제 API 호출 금지)을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- REST 포트(8000)에 장애 설정·상태 조회 경로를 만들지 마라. 이유: 관리 엔드포인트는 compose 내부 전용이다(AGENTS.md CRITICAL).
- 난수·실시간 `sleep`에 의존하는 규칙을 만들지 마라. 이유: 장애 재현은 요청 순번 스크립트로 결정론이어야 한다. 시계와 대기는 주입 가능하게 둔다.
- `hide_for`로 `/state`의 실제 건수를 숨기지 마라. 이유: 중복 건수는 mock 실제 건수로 센다(EVAL 안정성).
- mock이 서버 측 중복 제거나 멱등 키를 처리하게 만들지 마라. 이유: 멱등성은 B가 보장한다(ADR-006).
- `src/govops/mocks/`가 `govops`의 다른 계층을 import하게 하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

# Step 1: write-clients

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-006, ADR-017), `/docs/ARCHITECTURE.md` ("도구와 부작용·멱등성", "자격 증명 중개 흐름")
- `/src/govops/tools/rest.py`, `/src/govops/tools/jira.py`, `/src/govops/tools/slack.py`, `/src/govops/tools/registry.py`, `/src/govops/tools/a_mcp.py`, `/src/govops/broker/credentials.py`
- `/src/govops/mocks/jira.py`, `/src/govops/mocks/slack.py`(요청·응답 형식), `/tests/test_tool_runtime.py`, `/tests/test_tool_registry.py`, `/tests/test_smoke_tools.py`
- 이전 step 산출물: `/src/govops/mocks/faults.py`

## 작업

도구 계층에 부작용 **쓰기 클라이언트**와 **표식 조회 클라이언트**, 멱등 키·표식 유틸을 만든다. 에이전트 실행 경로에는 아직 연결하지 않는다. TDD로 테스트를 먼저 쓴다.

### 오류 분류 가능한 REST 오류 (`tools/rest.py` 수정)

`ToolHttpError`에 필드를 더한다(기존 필드·기존 테스트 동작 유지):
- `kind`: `"connect"`(연결 수립 실패: `httpx.ConnectError`·`ConnectTimeout` 등 요청이 서버에 닿지 않았음이 확실한 경우), `"timeout"`(`ReadTimeout`·`WriteTimeout`·`PoolTimeout` 아님 — 읽기/쓰기 타임아웃), `"transport"`(그 밖의 `httpx.RequestError`: 읽기 오류·프로토콜 오류 등 결과 불확실), `"http"`(응답을 받음).
- `retry_after: float | None`: 429 응답의 `Retry-After`(초 정수 또는 소수)를 파싱한다. 잘못된 값은 `None`.
- 기존처럼 응답 본문·헤더·토큰을 예외 메시지에 넣지 않는다. 요청별 `timeout` 값을 호출자가 넘길 수 있게 한다.
- 쓰기용 `_post(user_id, path, json=...)`를 추가한다. **자체 재시도를 하지 않는다**(재시도 정책은 호출하는 실행기가 정한다). 자격 증명은 요청 직전에 `credentials.credential_for(user_id, self.audience)`로 받아 헤더에만 붙인다.

### 쓰기·조회 클라이언트

- `SlackClient.send_message(user_id, channel, text, *, metadata) -> dict`(`POST /channels/{id}/messages`), `SlackClient.channel_history(user_id, channel, *, limit=50) -> list[dict]`.
- `JiraClient.add_comment(user_id, ticket_id, body, *, metadata)`, `JiraClient.ticket_comments(user_id, ticket_id)`, `JiraClient.create_followup(user_id, parent_ticket_id, *, title, description, priority, metadata)`, `JiraClient.ticket_children(user_id, ticket_id)`.
- 조회 메서드(`channel_history`·`ticket_comments`·`ticket_children`)는 **도구 계층 전용**이다. `agent_tool_definitions()`에 나타나면 안 되고 `tool_specs()`에도 추가하지 않는다.
- 응답에서 효과의 mock ID(`message_id`·코멘트 ID·후속 티켓 `ticket_id`)를 뽑는 헬퍼를 둔다.

### 멱등 키·표식 (`src/govops/tools/markers.py`)

```python
def idempotency_key(task_id: str, tool: str, args: Mapping[str, Any]) -> str   # sha256 hex
def make_marker(task_id: str, idem_key: str, approval_id: str) -> dict[str, str]
def has_marker(items: Iterable[Mapping], idem_key: str) -> bool
```

- 멱등 키 = sha256(canonical JSON{task_id, tool, args}). args는 **정규화**한다: 문자열은 NFC, 키 정렬, 공백 없는 JSON, UTF-8. 키 순서가 달라도 같은 키가 나와야 한다. 사용자·팀·직급 필드가 args에 있으면 `ValueError`.
- 표식은 **메타데이터 필드**에만 둔다: `govops_idem`(멱등 키), `govops_task`(task ID), `govops_approval`(승인 ID). 본문(`text`·`body`·`description`)에는 절대 넣지 않는다.
- `has_marker`는 항목의 `metadata["govops_idem"]`로만 판정한다(본문 검색 금지).

### 테스트

- 오류 분류: `ConnectError`→`connect`, `ReadTimeout`→`timeout`, `ReadError`→`transport`, 429+`Retry-After`→`http`+`retry_after`, 5xx→`http`. 예외 문자열에 토큰·본문이 없다.
- 쓰기 클라이언트가 in-process mock(ASGI)에 실제로 효과를 만들고 표식이 `metadata`에만 있다. 조회 클라이언트로 표식이 보인다. 토큰은 요청 헤더에만 있고 응답·예외·로그에 없다.
- 멱등 키 안정성(키 순서·NFC 차이 무관), 인자가 다르면 키가 다르다.
- 도구 계약 테스트: `agent_tool_definitions()`와 `tool_specs()`는 이전과 똑같다(부작용 도구 3개는 spec만 있고 에이전트 정의에는 없음, 신원 필드 없음). `ToolRuntime.call`은 여전히 부작용 도구에 `ToolNotExecutable`을 던진다.
- 키·네트워크 없이 통과한다. 계층 가드 통과(`tools/`는 `agent`·`api`·`eval`·`mocks`·`langgraph`·LLM SDK를 import하지 않는다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - 자격 증명이 `credential_for`로 요청 직전에만 받아져 헤더에만 붙는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 표식을 본문에 넣지 마라. 이유: 본문 표식은 사용자에게 보이고 사람이 수정하면 사라진다(ADR-006).
- 표식 조회에 검색 API를 쓰지 마라. 이유: 검색은 반영이 늦을 수 있다. 채널 최근 이력·티켓 코멘트 목록·원 티켓 children 목록만 쓴다.
- `_post`에 재시도·백오프를 넣지 마라. 이유: 부작용 재시도는 표식 조회를 먼저 해야 하므로 정책은 실행기(다음 step들)에 있다.
- 조회 메서드를 에이전트 도구 정의·`tool_specs()`에 추가하거나 `ToolRuntime`에서 부작용 도구가 실행되게 하지 마라. 이유: 부작용은 승인과 outbox를 거쳐서만 실행한다(AGENTS.md CRITICAL).
- 도구 입력에 사용자·팀·직급 필드를 추가하지 마라.
- 토큰·`Authorization` 값을 예외·로그·trace에 남기지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드를 약화하지 마라.

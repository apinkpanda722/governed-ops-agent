# Step 7: rest-clients

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표, 아키텍처 규칙의 자격 증명·부작용 도구·읽기 권한 거부 항목)
- `/docs/ARCHITECTURE.md` ("도구와 부작용·멱등성" 표와 설명, "자격 증명 중개 흐름")
- `/docs/ADR.md` (ADR-002 도구 목록, ADR-005 불변식, ADR-006)
- step 0 산출물: `/src/govops/tools/contract.py`(`load_snapshot`, `find_identity_fields`)
- step 1·2 산출물: `/src/govops/mocks/jira.py`, `/src/govops/mocks/slack.py`, `/src/govops/mocks/auth.py`, `/src/govops/mocks/tokens.py`, `/tests/test_mock_jira.py`, `/tests/test_mock_slack.py` (REST 응답 형식 확인용. `tools`는 `mocks`를 import할 수 없다. **테스트 코드만** mocks를 import해 in-process로 쓴다)
- step 4·6 산출물: `/src/govops/broker/credentials.py`, `/src/govops/tools/a_mcp.py`, `/tests/test_a_mcp_client.py`, `/tests/conftest.py`
- 도구 이름 결정(확정): 읽기 `search_docs`, `lookup_id`, `run_sql`, `get_document`(A 계약) + `jira_get_ticket`, `jira_search_tickets`; 부작용 `jira_add_comment`, `jira_create_followup_ticket`, `slack_send_message`.

## 작업

mock-jira·mock-slack의 **읽기 REST 클라이언트**와, 도구 9개의 **spec 레지스트리·읽기 도구 실행기**를 `tools` 계층에 만든다. 부작용 도구는 spec(스키마)만 정의하고 **실행기를 연결하지 않는다**(승인과 outbox 없이는 부작용을 실행할 수 없다. 연결은 M3). TDD로 테스트를 먼저 쓴다.

### 만들 파일

1. **`src/govops/tools/rest.py`** — 공통 REST 기반
   - `ToolHttpError(status: int, code: str)`: 메시지에 헤더·토큰이 없다.
   - 호출마다 `credential_for(user_id, audience)`를 **요청 직전에** 호출하고 `Authorization: Bearer ...`를 그 요청 헤더에만 붙인다. 토큰을 속성·전역에 두지 않는다. 사용자·팀 정보를 URL·본문·쿼리에 넣지 않는다.
   - `httpx.AsyncClient`를 외부에서 주입받을 수 있어야 한다(테스트가 `ASGITransport`·`MockTransport`를 쓴다). 타임아웃은 임시 상수(M3에서 정책화).
2. **`src/govops/tools/jira.py`**: `JiraClient(base_url, *, http_client=None)` (audience `mock-jira`)
   ```python
   async def get_ticket(self, user_id: str, ticket_id: str) -> dict | None   # 404 → None (없음과 권한 없음을 구분하지 않는다)
   async def search_tickets(self, user_id: str, *, query: str | None = None, project: str | None = None, status: str | None = None, limit: int = 20) -> list[dict]
   async def list_comments(self, user_id: str, ticket_id: str) -> list[dict] | None   # 표식 조회용(M3), 에이전트 도구 아님
   async def list_children(self, user_id: str, ticket_id: str) -> list[dict] | None   # 표식 조회용(M3), 에이전트 도구 아님
   ```
   쓰기 메서드(코멘트·후속 티켓 생성)는 **만들지 않는다**.
3. **`src/govops/tools/slack.py`**: `SlackClient(base_url, *, http_client=None)` (audience `mock-slack`)
   ```python
   async def list_channels(self, user_id: str) -> list[dict]
   async def channel_history(self, user_id: str, channel_id: str, *, limit: int = 50) -> list[dict]   # 표식 조회용(M3), 에이전트 도구 아님. 403은 ToolHttpError
   ```
   발송 메서드는 **만들지 않는다**.
4. **`src/govops/tools/registry.py`**
   ```python
   @dataclass(frozen=True)
   class ToolSpec:
       name: str; description: str; input_schema: dict
       side_effect: bool; requires_approval: bool
       audience: Audience; version: int

   def tool_specs() -> dict[str, ToolSpec]          # 9개 (A 4개는 load_snapshot()의 스키마·설명·버전을 그대로 사용, B 5개는 아래 정의)
   def agent_tool_definitions() -> list[dict]       # 모델에 제공할 읽기 도구만(6개), OpenAI function-tool 형식 {"type":"function","function":{"name","description","parameters"}}

   @dataclass(frozen=True)
   class ToolOutcome:
       payload: dict; is_error: bool; observed_ids: tuple[str, ...]
   class ToolNotExecutable(Exception): ...         # 부작용 도구·알 수 없는 도구

   class ToolRuntime:
       def __init__(self, a_client: AMcpClient, jira: JiraClient, slack: SlackClient)
       async def call(self, name: str, arguments: Mapping[str, Any], *, user_id: str) -> ToolOutcome
   ```
   B 도구 입력 스키마(pydantic 모델 `extra="forbid"`로 정의하고 `model_json_schema()`로 스키마를 얻는다. 모든 스키마는 `additionalProperties: false`):
   - `jira_get_ticket`: `ticket_id`(패턴 `^[A-Z]{3}-\d+$`)
   - `jira_search_tickets`: `query`(선택), `project`(선택, 키 3글자), `status`(선택), `limit`(선택, 1~20)
   - `jira_add_comment`: `ticket_id`, `body`
   - `jira_create_followup_ticket`: `parent_ticket_id`(필수), `title`, `description`(선택), `priority`(선택)
   - `slack_send_message`: `channel`, `text`
   **어떤 스키마에도 사용자·팀·직급·멱등 키·표식·메타데이터 필드를 두지 않는다.** (표식·멱등 키는 M3에서 도구 계층이 붙인다.)
   - `observed_ids`: 결과 payload에서 모은 식별자 — A 결과의 모든 깊이 `doc_id` 값, jira 결과의 `ticket_id`·`parent_id` 값. 중복 제거, 정렬.
   - 실행기 동작: A 도구 4개는 `AMcpClient.call_tool`로, `jira_get_ticket`·`jira_search_tickets`는 `JiraClient`로 호출한다. `jira_get_ticket`이 없음/권한 없음이면 `ToolOutcome({"error": {"code": "not_found", "message": ...}}, is_error=True, ())` — 존재하지 않는 티켓과 권한 없는 티켓이 **구별되지 않는다**. 인자가 스키마에 맞지 않으면 `is_error=True`, 코드 `invalid_argument`(네트워크 호출 전). `side_effect`인 도구나 알 수 없는 이름은 **어떤 네트워크 호출도 하지 않고** `ToolNotExecutable`.
   - 채널 이력(`channel_history`)·코멘트·후속 티켓 목록(`list_comments`, `list_children`)은 레지스트리에 도구로 올리지 않는다.

### 테스트

- `tests/test_tool_registry.py`
  - `tool_specs()`가 정확히 9개이고 이름이 위 확정 목록과 같다. 읽기 도구는 6개(A 4개 + `jira_get_ticket` + `jira_search_tickets`)로 `side_effect=False`, 부작용 도구는 `jira_add_comment`·`jira_create_followup_ticket`·`slack_send_message` 셋으로 `side_effect=True`·`requires_approval=True`. 모델에 제공되는 `agent_tool_definitions()`는 읽기 6개뿐이다.
   - **전체 spec 9개의 `input_schema`에 `find_identity_fields`(step 0) 위반이 0건**이다. 스캐너가 위반을 잡는 음성 사례(신원 필드가 있는 가짜 spec)도 테스트에 둔다. `agent_tool_definitions()`의 `parameters`에도 같은 검사를 한다.
   - 모든 B 스키마가 `additionalProperties: false`이고 `jira_create_followup_ticket`의 필수 필드에 `parent_ticket_id`가 있다. 어떤 스키마에도 `idempotency`·`marker`·`metadata` 계열 필드가 없다.
   - `agent_tool_definitions()`에 채널 이력·코멘트 목록 도구가 없다.
- `tests/test_tool_runtime.py` (in-process 실제 계층 통과; 테스트 코드가 mocks를 import해 `httpx.ASGITransport`로 연결하고, 가짜 A는 step 5의 `serve_asgi`로 실제 streamable HTTP를 쓴다. 토큰은 `tmp_path`에서 step 1·5의 생성기로 만들고 환경 변수로 가리킨다):
  - **같은 task를 두 사용자가 요청하면 결과가 다른 계층 통과 테스트**: `demo-platform-g3`와 `demo-finance-g3`가 (a) `lookup_id`(incident `INC-2026-012`) — platform은 `syn-incident-022`를 `observed_ids`에 포함, finance는 포함하지 않음 (b) 시드에 둔 team 수준 platform 티켓에 `jira_get_ticket` — platform은 티켓, finance는 `not_found` 오류이며 **존재하지 않는 티켓 ID의 결과와 payload가 같다**. 사용자별 결과가 다른 이유가 신원(토큰)뿐임을 확인한다(인자는 동일).
  - **Authorization 가로채기**: A·jira·slack 각각의 서버 앞에 테스트가 만든 ASGI 미들웨어(또는 `httpx.MockTransport`)를 두어 요청 헤더를 기록한다. 각 요청에 `Authorization`이 정확히 하나이고, audience별로 토큰이 다르며(`kodocs-mcp` ≠ `mock-jira` ≠ `mock-slack`), 요청 URL·쿼리·본문에 사용자 label·team·grade 문자열이 없다. 토큰 문자열이 `ToolOutcome`·`ToolHttpError`의 `str/repr`·`caplog`(DEBUG)·`capsys`·클라이언트 `vars()`에 없다.
  - 부작용 도구(`slack_send_message` 등)를 `call`하면 `ToolNotExecutable`이고 **서버가 받은 요청 수가 0**이다.
  - 잘못된 인자(스키마 위반, 알 수 없는 필드 `user_id` 포함)는 네트워크 호출 전에 `invalid_argument`다.
  - `JiraClient.list_comments`·`list_children`·`SlackClient.channel_history`가 mock에 대해 동작한다(타 팀 채널 이력은 `ToolHttpError` 403). 쓰기 메서드가 클래스에 **존재하지 않는다**(`hasattr` 검사).
- `tests/test_architecture.py` 통과(`tools`는 mocks·agent·api 등을 import하지 않는다. 테스트 코드는 예외).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 도구 spec 9개 전체에 사용자·팀·직급 필드가 없는가? 모델에 제공되는 도구가 읽기 6개뿐인가?
   - 부작용 도구가 실행기에 연결되지 않았고 호출 시 네트워크 접촉이 0인가?
   - 토큰이 요청 헤더에만 있고 audience별로 분리되는가? 같은 요청을 다른 사용자가 하면 결과가 다른가?
   - `tools`가 `mocks`·`langgraph`·LLM SDK를 import하지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 부작용 도구의 실행(쓰기 클라이언트 메서드 포함)을 구현하지 마라. 이유: AGENTS.md CRITICAL, 부작용은 승인과 outbox를 거쳐서만 실행한다. outbox는 M3, 승인 노드 핵심도 M3 범위다.
- 도구 입력 스키마에 사용자·팀·직급·멱등 키·표식 필드를 추가하지 마라. 이유: 신원은 세션에서만 정하고 멱등 키·표식은 도구 계층이 M3에서 붙인다.
- 채널 이력·코멘트 목록·후속 티켓 목록을 모델이 쓰는 도구로 노출하지 마라. 이유: 표식 조회 전용(M3)이며 권한 판정 정보를 모델에 드러내지 않는다.
- `tools` 코드에서 `govops.mocks`를 import하지 마라. 이유: 계층 경계 위반이다. 테스트 코드만 in-process로 쓴다.
- 재시도·서킷·타임아웃 정책을 구현하지 마라. 이유: M3 범위다.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `Makefile`, `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

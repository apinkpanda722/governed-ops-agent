# Step 6: api-tasks-cli

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `api` 행, 아키텍처 규칙의 신원·토큰·읽기 거부 항목)
- `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "BFF 인증 흐름"의 API 부분, "상태 관리")
- `/docs/ADR.md` (ADR-005, ADR-011의 API 부분, ADR-017)
- step 0~4 산출물: `/src/govops/costs.py`, `/src/govops/gateway/client.py`, `/src/govops/agent/graph.py`(`build_graph`, `run_task`, `get_task`, `AgentState`), `/src/govops/agent/checkpointer.py`
- step 5 산출물: `/src/govops/api/app.py`, `/src/govops/api/sessions.py`, `/src/govops/api/auth.py`, `/src/govops/api/startup.py`, `/src/govops/api/__main__.py`, `/src/govops/api/users.py`, `/tests/test_api_sessions.py`
- m1-tools 산출물: `/src/govops/tools/registry.py`(`ToolRuntime`), `/src/govops/tools/a_mcp.py`, `/src/govops/tools/jira.py`, `/src/govops/tools/slack.py`, `/tests/conftest.py`
- `/.env.example`, `/Makefile`, `/tests/test_makefile.py`, `/tests/test_architecture.py`

## 작업

`POST /tasks`·`GET /tasks/{id}`와 CLI, 그리고 운영용 조립(wiring)을 만든다. 승인 API·SSE는 만들지 않는다(M3·M5). TDD로 테스트를 먼저 쓴다.

### 설계 (확정)

- task 실행은 요청을 받은 즉시 반환하고 **백그라운드에서** 그래프를 돌린다(`asyncio.create_task`, 강한 참조 보관). `thread_id` = `task_id` = `uuid4().hex`.
- **소유자만 조회**한다. 존재하지 않는 task, 형식이 틀린 ID, **다른 사용자의 task**는 모두 **같은 404**(상태 코드와 응답 본문 바이트가 같다). task의 소유자는 에이전트 state의 `user_id`(그래프가 시작되기 전에는 메모리의 소유자 표)로 판정한다.
- 응답에 토큰·세션 토큰이 없다.

### 만들 것

1. **`src/govops/api/tasks.py`**
   ```python
   class TaskService:
       def __init__(self, graph, *, max_request_chars: int = 2000)
       async def start(self, user_id: str, request: str) -> str            # task_id, 백그라운드 실행 시작
       async def get(self, user_id: str, task_id: str) -> dict | None      # 소유자가 아니거나 없으면 None
       async def aclose(self) -> None                                      # 진행 중 task 정리
   ```
   `get` 응답 필드: `task_id`, `status`(`running`|`completed`|`failed`), `answer`, `cited_doc_ids`, `observed_doc_ids`(state의 `observed_ids`), `steps`, `error`(오류 코드 또는 null). 백그라운드 실행이 예기치 않게 예외로 끝나면 `status="failed"`, `error="internal_error"`로 보인다(예외 메시지는 노출·로그하지 않는다).
2. **`src/govops/api/app.py` 확장**: `create_app(..., service: TaskService | None = None)`. `service`가 있으면 라우트 추가 — `POST /tasks` 본문 `{"request": str}`(길이 1~2000, 공백만은 422) → 202 `{"task_id": ...}`, `GET /tasks/{task_id}` → 200 위 필드 / 404. 두 라우트는 `current_user` 인증(401)을 요구한다. 사용자·팀·직급을 본문·쿼리로 받지 않는다.
3. **`src/govops/api/wiring.py`**: 운영 조립. 환경 변수로 `AMcpClient(KODOCS_MCP_URL)`, `JiraClient(MOCK_JIRA_URL, 기본 http://127.0.0.1:8081)`, `SlackClient(MOCK_SLACK_URL, 기본 http://127.0.0.1:8082)`, `LiteLLMChatClient()`, `open_checkpointer(GOVOPS_DATABASE_URL)`을 만들어 `ToolRuntime`·`build_graph`·`TaskService`를 구성하는 비동기 컨텍스트 매니저를 제공한다. `KODOCS_MCP_URL`·`LITELLM_AGENT_KEY`가 없으면 **변수 이름만** 말하는 설정 오류. 시작 검사(`verify_contract`)를 포함해 계약이 다르면 중단한다. `GOVOPS_DATABASE_URL`이 비어 있으면 in-memory checkpointer(개발용, 재시작 시 task 소실)를 쓴다.
4. **`src/govops/api/__main__.py`**: step 5의 기동 구성에 wiring과 `TaskService`를 연결한다(lifespan에서 조립·정리). 호스트 `127.0.0.1` 고정은 유지한다.
5. **`src/govops/api/cli.py`** (`python -m govops.api.cli ask --as <label> "<요청>" [--base-url http://127.0.0.1:8000] [--timeout 120] [--interval 1]`)
   ```python
   def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep) -> int
   ```
   `POST /sessions`로 세션 발급 → `POST /tasks` → `GET /tasks/{id}`를 종료 상태(`completed`/`failed`)까지 폴링 → 상태, 답변, `cited_doc_ids`, `observed_doc_ids`, `steps`, `error`를 출력한다. **세션 토큰은 메모리에만 두고 출력하지 않는다.** 성공(`completed`)이면 종료 코드 0, `failed`·타임아웃·HTTP 오류는 1.
6. **`Makefile`**: `serve`(`uv run --env-file .env python -m govops.api`)와 `ask`(`uv run --env-file .env python -m govops.api.cli ask --as $(AS) "$(Q)"`) 타깃 추가. 각각 위에 한 줄 주석(`serve`: "compose·live A 기동 후, DEMO_LOGIN=true 필요", `ask`: 예시 사용법). `verify`는 호출하지 않는다. `tests/test_makefile.py` 확장.

### 테스트 (`tests/test_api_tasks.py`, `tests/test_api_cli.py`, `tests/test_api_wiring.py`)

가짜 `ChatClient`·`ToolExecutor`로 실제 `build_graph`와 `InMemorySaver`를 만들어 쓴다. API 호출은 `httpx.AsyncClient(transport=httpx.ASGITransport(app=...))` 또는 `TestClient`.
- 정상 흐름: 로그인 → `POST /tasks` 202 → 폴링 → `completed`, `answer`·`cited_doc_ids`·`observed_doc_ids`·`steps` 필드가 맞다. `running` 상태 응답도 확인한다(모델 응답을 이벤트로 붙잡아 두는 가짜 클라이언트).
- **소유자 격리**: 사용자 B의 세션으로 사용자 A의 task를 조회하면 404이고, 응답이 존재하지 않는 UUID를 조회했을 때와 **상태 코드·본문·헤더(날짜 등 가변 제외)가 같다**. 형식이 틀린 ID도 같은 404.
- 인증: 헤더 없음·만료·미등록 세션 → 401. 요청 길이 0·공백·2001자 → 422. 본문에 `user`·`team`·`grade`를 넣어도 무시되고 결과 사용자는 세션 사용자다(가짜 실행기가 받은 `user_id`로 확인).
- **토큰 비노출**: 세션 토큰 센티널이 `POST /tasks`·`GET /tasks` 응답 본문·헤더, `caplog`(DEBUG), 그래프 state(`InMemorySaver` 저장분), 모델에 전달된 메시지에 나타나지 않는다.
- 백그라운드 예외(가짜 모델이 예기치 않은 예외): `GET`이 `failed`/`internal_error`이고 응답·로그에 예외 문자열이 없다. 동시에 두 task를 시작해도 서로 섞이지 않는다. `aclose`가 진행 중 task를 정리한다.
- CLI: `MockTransport`(또는 ASGI transport)와 주입된 `sleep`으로 로그인·생성·폴링·출력을 확인한다. 출력(`capsys`)에 세션 토큰이 없고 답변과 ID 목록이 있다. `failed`·타임아웃·401·연결 오류에서 종료 코드 1.
- wiring: 환경 변수를 `monkeypatch`로 설정하고 `serve_asgi`의 가짜 A·임시 토큰 파일로 조립이 성공하며 `verify_contract`가 통과한다. 스냅샷과 다른 가짜 A면 시작이 중단된다. `LITELLM_AGENT_KEY` 또는 `KODOCS_MCP_URL`이 없으면 설정 오류에 변수 **이름**만 있고 값은 없다. `GOVOPS_DATABASE_URL`이 비어 있으면 in-memory saver. 이 테스트에서 실제 LiteLLM·Postgres에 접속하지 않는다(채팅 클라이언트는 생성만 하고 호출하지 않는다).
- 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 소유자가 아닌 task 조회가 없는 task와 구별되지 않는가?
   - 신원이 세션에서만 정해지는가(요청 본문 신원 필드 무시)? 세션·자격 증명 토큰이 응답·state·로그에 없는가?
   - 승인 API·SSE·부작용 실행이 없는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `POST /tasks/{id}/approval`·SSE·부작용 도구 연결을 만들지 마라. 이유: 승인 노드 핵심은 M3, SSE·화면은 M5 범위다.
- 요청 본문·쿼리·헤더로 사용자·팀·직급을 받지 마라. 이유: 신원은 인증된 세션에서만 정한다.
- 다른 사용자의 task와 없는 task를 구별되는 응답으로 만들지 마라. 이유: 존재 자체를 알리지 않는다.
- CLI·로그·응답에 세션 토큰이나 자격 증명을 출력하지 마라.
- `serve`·`ask`를 이 step에서 실행하지 마라. 이유: 실제 서비스 기동·실제 API 호출은 사용자가 한다. 테스트는 가짜 클라이언트와 ASGI transport만 쓴다.
- `api` 코드에서 `broker`·`mocks`·LLM SDK를 직접 import하지 마라.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

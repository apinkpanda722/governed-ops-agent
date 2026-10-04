# Step 7: e2e-isolation

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (아키텍처 규칙 전체 — 신원·토큰·읽기 거부·부작용·기록 재생)
- `/docs/ARCHITECTURE.md` ("자격 증명 중개 흐름" 시퀀스 전체, "A 연동 모드")
- `/docs/ADR.md` (ADR-004, ADR-005 불변식 목록, ADR-006, ADR-017)
- 이 phase의 산출물: `/src/govops/api/app.py`, `/src/govops/api/tasks.py`, `/src/govops/api/sessions.py`, `/src/govops/api/wiring.py`, `/src/govops/agent/graph.py`, `/src/govops/agent/prompts.py`, `/src/govops/gateway/client.py`, `/tests/test_agent_graph.py`, `/tests/test_api_tasks.py`
- m1-tools 산출물: `/src/govops/tools/registry.py`, `/src/govops/tools/a_mcp.py`, `/src/govops/tools/jira.py`, `/src/govops/tools/slack.py`, `/src/govops/broker/credentials.py`, `/src/govops/replay/fake_a.py`, `/src/govops/replay/recordings.py`, `/src/govops/replay/tokens.py`, `/src/govops/mocks/tokens.py`, `/src/govops/mocks/jira.py`, `/tests/conftest.py`, `/tests/test_tool_runtime.py`(in-process mock + 가짜 A 구성 방식), `/tests/test_a_mcp_client.py`(헤더 가로채기 `Capture` 미들웨어)
- 커밋된 실제 기록: `/data/replay/a/recordings.jsonl`(live A에서 기록한 `demo-platform-g3`·`demo-finance-g3`의 `lookup_id`(incident `INC-2026-012`)·`get_document`(`syn-incident-022`) 4건), `/data/replay/requests.json`
- `/data/seed/tickets.json`, `/data/seed/users.json`(커밋된 시드)

## 작업

계층 전체를 **실제로 통과**하는 격리 테스트를 만든다. 이 step은 production 코드를 바꾸지 않는 테스트 step이다(버그가 발견되면 최소한으로 고치고 어떤 버그였는지 최종 summary에 적는다). TDD 원칙대로 먼저 테스트를 쓰고 통과시킨다.

### 구성 (`tests/test_e2e_isolation.py`)

- **replay 모드 구성**: 가짜 A(`create_app`)를 **커밋된 실제 기록 `data/replay/a`**(`RecordingSet.load`)와 `generate_a`(replay 토큰 생성기)로 만든 임시 A 형식 토큰 파일로 `serve_asgi`로 띄운다(**실제 streamable HTTP**). mock-jira는 임시 시드·임시 토큰(`mocks.tokens.generate_tokens`)으로 in-process(`httpx.ASGITransport`)로 연결한다. `broker`는 환경 변수(`KODOCS_MCP_TOKENS_FILE`, `MOCK_TOKENS_FILE`)로 임시 파일을 가리킨다.
- 실제 `ToolRuntime`(`AMcpClient`·`JiraClient`·`SlackClient`) + 실제 `build_graph`(`InMemorySaver`) + 실제 API 앱(`create_app`, `TaskService`, `SessionStore`, 사용자 목록에 두 사용자)을 `httpx.ASGITransport`로 호출한다.
- 모델만 **스크립트된 가짜 `ChatClient`**다: 첫 호출은 도구 호출 두 개(`lookup_id`(`kind="incident"`, `value="INC-2026-012"`), 시드에서 고른 *platform 팀 team 수준 티켓*에 대한 `jira_get_ticket`)를 돌려주고, 둘째 호출은 **직전 도구 메시지에서 실제로 보인 ID만** `[ID]` 형식으로 인용하는 답변을 만든다(보인 문서가 없으면 "찾을 수 없습니다"). 받은 `messages`·`tools`를 모두 기록한다.
- 같은 요청 문장으로 `demo-platform-g3`와 `demo-finance-g3`가 각자 세션을 발급(`DEMO_LOGIN` 켠 앱)해 `POST /tasks`를 하고 폴링한다.
- 이 step의 테스트는 모두 `127.0.0.1` 임의 포트(`serve_asgi`)와 in-process만 쓴다.

### 검증 항목 (모두 테스트로)

1. **같은 task를 두 사용자가 요청하면 결과가 다르다**: platform 결과의 `observed_doc_ids`·`cited_doc_ids`에 `syn-incident-022`가 있고 finance 결과에는 없다. finance의 답변은 "찾을 수 없습니다" 계열이다. 티켓은 platform이 조회되고 finance는 `not_found`이며 finance의 `not_found` 도구 결과 payload가 **존재하지 않는 티켓 ID를 조회한 결과 payload와 같다**.
2. **Authorization 헤더 가로채기**: 가짜 A와 mock-jira 앞에 테스트 ASGI 미들웨어(`test_a_mcp_client.py`의 `Capture` 방식)를 둔다. 각 서버가 받은 모든 요청에 `Authorization`이 정확히 하나이고, 사용자별로 다르며, audience별로 다르다(A 토큰 ≠ mock-jira 토큰). 요청 URL·쿼리·본문에 사용자 label·team·grade가 없다.
3. **토큰 누출 스캔**: 이 테스트에서 쓰는 모든 비밀 센티널(A 토큰들, mock-jira·mock-slack 토큰들, 두 세션 토큰, `LITELLM_AGENT_KEY`·`LITELLM_MASTER_KEY`로 설정한 센티널)을 모아 아래 모든 곳에서 검색해 **하나도 나타나지 않음**을 확인한다(로그인 응답 본문의 세션 토큰 자체는 제외): ① 로그인 외 모든 API 응답 본문·헤더 ② 가짜 모델이 받은 모든 `messages`·`tools` ③ `InMemorySaver`의 저장된 체크포인트 전체(직렬화해 바이트 검색) ④ `caplog`(모든 로거 DEBUG, `httpx`·`httpcore`·`mcp`·`uvicorn` 포함)와 `capsys` ⑤ 가짜 A·mock-jira가 돌려준 모든 응답 본문·헤더.
4. **세션 신원 격리**: finance 세션으로 platform의 task를 조회하면 404이고 존재하지 않는 ID의 404와 응답이 같다. 본문에 `user`·`team`·`grade` 필드를 넣은 `POST /tasks`도 결과가 세션 사용자 기준이다.
5. **모델이 신원을 보지 못한다**: 가짜 모델이 받은 모든 메시지 내용에 사용자 label·팀 이름 문자열(예: `demo-platform-g3`, `platform`)이 없다(도구 결과에 팀 관련 값이 있을 수 있으므로 *사용자 label*과 *시스템·사용자 메시지*에 대해서만 검사한다). 모델에 제공된 도구 정의 6개가 `find_identity_fields` 위반 0건이다.
6. **부작용 차단**: 가짜 모델이 `slack_send_message`·`jira_add_comment`를 호출하면 모델에는 `tool_not_available` 오류가 돌아가고, 서버(가짜 A·mock-jira·mock-slack)가 받은 쓰기 요청이 0건이다(mock의 관리 `state`로 코멘트·후속 티켓·메시지 생성 0건 확인).
7. **중복 실행·재시작 없음**: 같은 task의 `GET`을 반복해도 도구 호출·모델 호출 수가 늘지 않는다.
8. **기록에 없는 호출**: 가짜 모델이 기록에 없는 `search_docs` 질의를 하면 가짜 A의 `replay_miss` 결과가 도구 결과로 모델에 전달되고 task는 정상 종료한다(오류 코드가 `replay_miss`로 구별되어 live 결과로 오인되지 않는다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 테스트가 실제 계층(API·그래프·ToolRuntime·AMcpClient(HTTP)·broker·가짜 A·mock)을 통과하고 모델만 가짜인가?
   - 같은 요청의 사용자별 결과 차이가 신원(토큰)에서만 오는가? 토큰 누출 스캔 범위가 위 5곳을 모두 포함하는가?
   - 부작용이 서버에 0건인가? 읽기 거부가 없는 번호와 구별되지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실제 LLM·LiteLLM·OpenAI·live A·docker·외부 네트워크를 쓰지 마라. 이유: 이 테스트는 기록 재생(replay)과 가짜 모델만 쓴다. 소켓은 `127.0.0.1` 임의 포트에만 바인드한다.
- 테스트를 통과시키려고 가로채기·누출 스캔의 범위를 줄이거나 센티널을 약화하지 마라. 이유: 이 테스트의 목적이 토큰·신원 격리 검증이다.
- 가짜 A에 인증 우회나 사용자 인자 경로를 추가하지 마라.
- `data/replay/a/recordings.jsonl`을 수정하거나 재생성하지 마라. 이유: live A에서 기록한 원본이다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- production 코드를 바꿔야 하면 버그 수정에 필요한 최소 범위만 고치고 이유를 최종 summary에 적어라. 계층 경계를 깨는 수정은 금지한다.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

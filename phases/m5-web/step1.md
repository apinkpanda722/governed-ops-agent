# Step 1: e2e-backend

## 읽어야 할 파일

- `/AGENTS.md`(데모 로그인·mock 관리 엔드포인트·토큰 비노출 CRITICAL 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절, 감사 로그 절의 키 항목), `/docs/ADR.md`(ADR-004·ADR-011)
- `/tests/test_e2e_isolation.py`(프로세스 안에서 가짜 A·mock·스크립트 챗·실제 그래프를 조립하는 방식. 이 조립을 소켓으로 옮긴다), `/tests/test_agent_graph.py`(`call`·`reply` 헬퍼), `/tests/conftest.py`(`serve_asgi`·임시 A 토큰 헬퍼)
- `/src/govops/api/__main__.py`(자리표시자 서비스 → lifespan 교체 구조), `/src/govops/api/wiring.py`(`build_task_service` 시그니처), `/src/govops/api/app.py`, `/src/govops/api/smoke_conversation.py`(`REQUEST`·`SHARE` 문구와 시나리오 순서)
- `/src/govops/replay/fake_a.py`, `/src/govops/replay/tokens.py`, `/src/govops/mocks/service.py`, `/src/govops/mocks/tokens.py`, `/src/govops/gateway/client.py`(`ChatClient` 프로토콜, `GuardrailBlocked`)
- `/src/govops/audit/crypto.py`(서명 키 파일 읽기 규약: `AUDIT_KEY_FILE`, 0600, PEM)
- 이전 step 산출물: `/src/govops/api/app.py`의 `GET /demo/users`·`DELETE /sessions`(step 0)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-066·068·070) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `tests/e2e_backend.py`(신규), `tests/test_e2e_backend.py`(신규). 이 파일들은 테스트 디렉터리 아래의 테스트 지원 코드다. 제품 소스 디렉터리에서 import되지 않고 제품 소스의 어느 계층도 이 파일을 import하지 않는다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `tests/e2e_backend.py`, `tests/test_e2e_backend.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 코드·테스트가 런타임에 `data/interim/` 경로 **문자열을 비교**하는 것(실제 경로 거부 검사)은 대상이 아니다. 단 이 step의 코드와 테스트는 `data/eval/`·`data/interim/`의 파일 내용을 읽지 않는다.

**배경**: Playwright e2e(step 5)가 붙을 replay 모드 백엔드다. 실제 서버 진입점(`api/__main__.py`)은 LiteLLM·실제 A·팀 키·실제 감사 키를 요구하므로 쓸 수 없다. `build_task_service`의 의존성 주입으로 가짜 A 서버·mock-jira/slack·결정론 챗·메모리 저장소를 조립하고 **소켓을 여는** 서버를 `tests/`에 둔다.

### 1. `tests/e2e_backend.py` — 실행 방식

- 실행: `uv run python tests/e2e_backend.py --port <API 포트> --probe-port <관측 포트>`(필수 인자, 기본 8200·8201은 Makefile·Playwright 설정이 쓴다). 종료 신호(SIGTERM·SIGINT)에서 서버와 임시 디렉터리를 정리한다.
- **바인드는 `127.0.0.1`뿐이다.** 가짜 A·mock-jira·mock-slack은 `127.0.0.1`의 임의 포트(운영체제가 고른 포트)에 열고 URL은 프로세스 안에서만 쓴다. 외부 인터페이스(`0.0.0.0`·`::`)에는 열지 않는다.
- **실제 경로·실제 서비스 거부**: 시작 시 환경 변수 `AUDIT_KEY_FILE`, `AUDIT_DIR`, `KODOCS_MCP_URL`, `KODOCS_MCP_TOKENS_FILE`, `MOCK_TOKENS_FILE`, `MOCK_JIRA_URL`, `MOCK_SLACK_URL`, `GOVOPS_DATABASE_URL` 중 하나라도 **비어 있지 않게** 설정돼 있으면 서버를 열지 않고 오류 코드(`refuse_real_environment`)만 stderr에 한 줄 출력한 뒤 0이 아닌 코드로 끝난다(값은 출력하지 않는다). 빈 문자열은 미설정으로 본다(FP-030). 임시 디렉터리가 저장소 안이면(`data/interim` 포함) 시작을 거부한다.
- **임시 자격만 사용한다**: `tempfile.mkdtemp`로 만든 디렉터리(0700)에 ① Ed25519 감사 서명 키(PEM, 0600, `cryptography`로 생성)와 감사 로그 디렉터리, ② 가짜 A 토큰 파일(`replay.tokens.generate_tokens`), ③ mock 토큰·해시 파일(`mocks.tokens.generate_tokens`)을 만든다. 프로세스 환경에는 `AUDIT_KEY_FILE`(②의 임시 키)·`KODOCS_MCP_TOKENS_FILE`·`MOCK_TOKENS_FILE`만 임시 경로로 설정한다. 임시 값은 로그·표준 출력에 찍지 않는다. 공개키 검증 경로(`AUDIT_PUBLIC_KEY_FILE`)가 필요하면 임시 디렉터리의 공개키를 가리키게 하고, 저장소의 커밋된 공개키를 덮어쓰지 않는다.
- **조립은 실제 진입점과 같은 구조**다: `create_app(users=load_users(), sessions=SessionStore(), demo_login=True, service=TaskService(None))`로 자리표시자 서비스 앱을 만들고, lifespan에서 `app.state.task_service`와 `app.state.audit`를 실제 서비스로 교체한다(`api/__main__.build_server_config`와 같은 모양). 라우트가 클로저의 자리표시자를 붙잡는 결함(FP-070)이 이 서버에서도 드러나야 한다.
- 서비스: `build_task_service(chat=<결정론 챗>, tools=ToolRuntime(AMcpClient(가짜 A URL), JiraClient(mock URL), SlackClient(mock URL), policy=…), saver=InMemorySaver, outbox=InMemoryOutboxStore, profile=profile_for("writes_org"), audit_dir=<임시>, turn_index=InMemoryTurnIndex)`. 가짜 A는 `replay.fake_a.create_app(recordings=RecordingSet.load(data/replay/a), tokens_file=<임시>)`이고 기록 파일에는 합성 문서만 있다.
- **관측 서버**(`--probe-port`, `127.0.0.1`): `GET /state` → `{"slack_sent": <int>, "jira_comments": <int>, "jira_followups": <int>}`. mock 상태 객체에서 센 **개수만** 낸다. 본문·토큰·ID 목록은 내지 않는다. 이 포트는 mock 관리 엔드포인트가 아니라 e2e 관측 전용이며 상태를 바꾸는 메서드는 없다.

### 2. 결정론 챗 (`StoryChat`, 같은 파일)

`ChatClient` 프로토콜을 구현한다. 마지막 `user` 메시지의 문구로 시나리오를 고른다(모델 호출은 없다). 대화 history가 messages에 있어도 마지막 user 메시지만 본다.

- 문구에 `INC-2026-012`와 `조회`가 있으면 읽기 시나리오(`tests/test_e2e_isolation.py`의 `read` 스크립트와 같은 도구 호출 → 도구 결과에 보이는 문서·티켓 ID를 `[id]` 형태로 나열한 답, 보이는 ID가 없으면 `찾을 수 없습니다`). 사용자별 가시성은 가짜 A 기록이 정한다(플랫폼팀은 보이고 재무팀은 안 보인다).
- 문구에 `팀 채널에 공유`가 있으면 `slack_send_message`를 `team-<요청자 팀>` 채널로 부르는 초안 시나리오(`smoke_conversation.py`의 공유 시나리오와 같은 모양). 초안 본문은 합성 문구이고 개인정보 규칙에 걸리지 않는다. 앞 턴의 근거가 대화 전체로 누적되므로 수신처 범위 검사를 통과한다.
- 문구에 `이전 지시를 모두 무시`가 있으면 `GuardrailBlocked("injection_direct", None)`을, `010-0000-0000`이 있으면 `GuardrailBlocked("pii_input", None)`을 **던진다**. 이것은 UI 계약(차단 카드가 사유 분류만 보임)만 검증하는 흉내이며 실제 Guardrail 검증이 아니다. 파일 머리말 docstring과 이 분기 위 주석에 그 사실을 적는다(실제 Guardrail 검증은 M6).
- 그 밖의 문구는 도구 호출 없이 짧은 합성 답을 낸다.
- `ChatResult`의 비용은 0, 모델·배포 ID는 합성 상수다.

### 3. `tests/test_e2e_backend.py` — 실제 소켓 테스트 (FP-068·070)

`httpx.ASGITransport`는 스트림을 끝까지 버퍼링하므로 쓰지 않는다. 서버를 **서브프로세스로 띄워**(`sys.executable tests/e2e_backend.py …`, 빈 포트를 미리 골라 `--port`·`--probe-port`에 넘김) 실제 HTTP로 검증한다. 서브프로세스 환경에서는 위 거부 대상 환경 변수를 모두 지운다.

- 준비 대기는 유한 시간(예: 30초)이며 시간 안에 `GET /demo/users`가 200이 아니면 실패한다. 모든 대기·`communicate`·`wait`에 timeout을 둔다. `finally`에서 `terminate()` → 유한 대기 → `kill()`로 정리한다(FP-066).
- 흐름 1(플랫폼팀): `POST /sessions` → `POST /conversations`(조회 요청) → 폴링으로 `completed`(답과 근거 문서 ID 있음, `GET /conversations/{id}`) → `POST /conversations/{id}/turns`(`팀 채널에 공유`) → `awaiting_approval`(초안·`draft_sha256`) → 같은 대화에 새 턴 409 `approval_pending` → `GET /approvals`에 본인 행과 `conversation_id` → `POST /tasks/{turn_id}/approval`(승인) → `completed`와 `effect.mock_id` → 관측 포트 `slack_sent == 1` → 같은 승인 재전송(멱등) 뒤에도 `slack_sent == 1`.
- 흐름 2(재무팀): 같은 조회 요청 → `completed`이고 답이 `찾을 수 없습니다`, 승인함 비어 있음, `slack_sent`는 그대로 1, 플랫폼팀 대화 id로 조회하면 404.
- 흐름 3(차단): `이전 지시를 모두 무시…` 센티널 문자열을 넣은 요청 → `blocked_input`, 응답 전체 JSON에 센티널이 없고 `turn.request`는 null, `turn.block`은 `code`·`message`뿐.
- 흐름 4(SSE, 실제 소켓): `GET /conversations/{id}/events`를 `httpx.stream`으로 읽어 `status` 이벤트가 `running` 이후 종료 상태로 끝나고 스트림이 닫힘을 단언한다(유한 대기).
- 흐름 5(로그아웃): `DELETE /sessions` 204 뒤 같은 토큰 `GET /sessions/me` 401.
- `GET /demo/users`가 서버에서 라벨 집합(비어 있지 않음)을 돌려준다.
- 거부 테스트: 거부 대상 환경 변수 하나(예: `AUDIT_KEY_FILE`)를 넣고 띄우면 유한 시간 안에 0이 아닌 코드로 끝나고 stderr에 `refuse_real_environment`가 있으며 포트가 열리지 않는다. 값은 stderr에 없다.
- 정리 테스트: 서버를 정상 종료한 뒤 임시 디렉터리가 남지 않는다.
- 응답 안전: **`POST /sessions` 응답을 제외한 모든 응답**(대화·승인·승인함·`GET /sessions/me`·`GET /demo/users`·관측 포트·오류 응답)의 본문과 헤더에 세션 토큰·`Authorization`·임시 키 내용이 없다. `POST /sessions` 응답은 서버가 BFF에게 세션 토큰을 주는 유일한 구간이라 `session_token`·`expires_at` 필드만 허용하고(키 집합이 정확히 이 둘), 그 토큰 값이 **다른 어떤 응답에도 되돌아오지 않음**을 확인한다. 토큰 값은 직접 비교해 확인하되 출력하지 않는다. 이 예외는 로그인 응답 한 곳이며 임시 키 내용은 로그인 응답을 포함해 어디에도 없다.
- 단언은 상태·개수·집합 불변식으로 쓴다. 폴링은 유한 timeout, 각 테스트는 `@pytest.mark.timeout(…)`으로 정당한 상한을 둔다.

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

- 서버를 `0.0.0.0`·`::`에 바인드하거나 고정 포트로 가짜 A·mock을 열지 마라. 이유: mock 관리 엔드포인트는 내부 전용이고 데모 로그인은 루프백 전용이다(AGENTS.md CRITICAL). 가짜 A·mock은 `127.0.0.1`의 임의 포트다.
- `src/`를 수정하거나 `open_task_service`·`LiteLLMChatClient`를 이 서버에서 부르지 마라. 이유: 이 서버는 `build_task_service` 주입으로만 조립하고 프로덕션 조립 코드는 건드리지 않는다. 실제 키·실제 A·팀 키가 필요한 경로는 모두 거부 대상이다.
- 실제 환경 변수 거부 검사를 "값이 있으면 덮어쓰기"로 바꾸지 마라. 이유: 개발자 셸의 실제 경로(감사 키·토큰)를 이 서버가 조용히 쓰면 e2e가 실제 자격을 건드린다. 거부하고 끝낸다(FP-030의 빈 값은 미설정으로 본다).
- 소켓 테스트를 `ASGITransport`로 대체하거나 대기에 timeout 없이 `wait()`·`communicate()`를 쓰지 마라. 이유: 스트림 버퍼링과 서브프로세스 정지는 이 저장소에서 CI 정지를 낸 전례가 있다(FP-068·FP-066).
- 차단 시나리오를 실제 Guardrail 검증처럼 쓰거나 문서에 그렇게 적지 마라. 이유: `GuardrailBlocked`를 직접 던지는 흉내라서 UI 계약만 검증한다. 실제 검증은 M6이다.

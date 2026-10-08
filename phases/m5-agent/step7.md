# Step 7: events-session-ttl

## 읽어야 할 파일

- `/AGENTS.md`(세션·토큰 규칙), `/docs/ADR.md`(ADR-013·ADR-017의 2026-10-08 변경 기록), `/docs/ARCHITECTURE.md`(웹 계층과 B 세션 절)
- 이전 step 산출물: `/src/govops/api/tasks.py`(step 5·6: 잠금 순서, `_resolve`, `get`의 만료 처리), `/src/govops/api/app.py`, `/src/govops/api/conversations.py`, `/tests/test_api_conversation_views.py`
- `/src/govops/api/sessions.py`, `/src/govops/api/auth.py`(`current_user`), `/src/govops/api/__main__.py`(`build_server_config`)
- `/tests/conftest.py`(`serve_asgi` 픽스처: 실제 uvicorn 서버), `/tests/test_api_sessions.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-012·032·066) — 읽기만 한다
- **외부 계약 실측**(2026-10-08):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| 프로젝트 의존성 `sse-starlette` | **선언 없음** | `pyproject.toml`의 의존성에 없다(다른 패키지의 간접 설치물). 의존성 설치는 사용자 몫이므로 쓰지 않는다. FastAPI의 `StreamingResponse`로 SSE 프레임(`event:`·`data:`·빈 줄)을 직접 쓴다 |
| Next 16 route handler의 SSE 중계(스크래치 프로브, dev와 `next start`) | 성공 | 업스트림 이벤트가 버퍼링 없이 1초 간격으로 도착했다. `Accept-Encoding: gzip`에도 `content-encoding`이 붙지 않았다. 이 step에서는 FastAPI 쪽만 만든다 |
| `httpx.ASGITransport` | 제약 | 응답이 끝나기 전에는 본문을 돌려주지 않는다. 스트림 테스트는 `serve_asgi`(실제 서버)로 한다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/tasks.py`, `src/govops/api/app.py`, `src/govops/api/sessions.py`, `tests/test_api_events.py`(신규), `tests/test_api_sessions.py`(TTL 사례 추가에 한함, 기존 단언 유지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/api/tasks.py`, `src/govops/api/app.py`, `src/govops/api/sessions.py`, `tests/test_api_events.py`, `tests/test_api_sessions.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 새 테스트는 두 경로를 읽지 않는다(임시 디렉터리와 합성 데이터만 쓴다).

**배경**: 웹은 이벤트를 받으면 `GET /conversations/{id}`로 화면을 다시 읽는다. 이벤트는 신호일 뿐 내용을 싣지 않는다. SSE가 실패하면 클라이언트가 폴링으로 대체한다.

### 1. `GET /conversations/{conversation_id}/events`

- 소유자만: 대화가 없거나 소유자가 아니면 스트림을 열기 전에 일반 JSON 404 `conversation_not_found`(없는 id와 같은 응답). 인증 없으면 401.
- `StreamingResponse`, `media_type="text/event-stream"`, 헤더 `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`.
- `TaskService.progress(user_id, conversation_id) -> tuple[str, str] | None`: 대화 잠금 → task 잠금 순서로 현재 `(turn_id, status)`를 읽고 `get`과 같이 만료를 먼저 처리한다. 대화가 사라졌으면 `None`.
- 프레임: `event: status\ndata: {"conversation_id": …, "turn_id": …, "status": …, "seq": n}\n\n`. **data는 이 네 키뿐이다**(텍스트·사유·문서 ID·초안 없음). 연결 직후 현재 상태 스냅샷을 `seq=0`으로 보낸다. 이후 `(turn_id, status)`가 바뀔 때만 `seq`를 1씩 올려 보낸다. 기본 폴링 간격 0.5초.
- 상태가 `running`·`awaiting_approval`이 아니면(종료 상태) 그 이벤트를 보낸 뒤 스트림을 닫는다. 15초 동안 이벤트가 없으면 `: ping\n\n` heartbeat 주석을 보낸다. 최대 1800초 뒤 닫는다. 클라이언트가 끊으면(`request.is_disconnected()`) 다음 폴링에서 멈추고 잠금을 쥔 채 대기하지 않는다. `progress`가 `None`이면 닫는다.
- `create_app(..., sse_poll_seconds=0.5, sse_heartbeat_seconds=15.0, sse_max_seconds=1800.0)` 키워드 인자로 간격을 주입할 수 있다(테스트용). 기본값은 위 값이고 기존 호출은 그대로 동작한다.

### 2. 세션 TTL (`sessions.py`)

`SESSION_TTL_SECONDS = 8 * 3600`을 최종값으로 확정한다(주석에서 "임시값, M5에서 확정"을 지운다). `SessionStore`의 만료와 `expires_at` 반환은 기존 방식 그대로다.

### 3. 테스트 (`tests/test_api_events.py`, 실제 서버 `serve_asgi` + `httpx` 스트림)

모든 읽기에 유한한 `timeout`을 둔다. 가짜 모델의 게이트(`HeldChat`)로 상태 전이를 결정론적으로 만들고 `sse_poll_seconds`를 작게(0.02) 준다. 처리 시각이 아니라 **이벤트 열**을 단언한다(FP-012·032).

- 첫 프레임은 스냅샷 `seq=0`이고 data의 키 집합이 정확히 `{conversation_id, turn_id, status, seq}`다. 모든 프레임에서 요청·답변 텍스트·사유·문서 ID·초안 문자열이 없다(센티널 문자열로 확인).
- `running → completed` 전이 열과 `seq`가 0부터 연속이고 종료 상태 이벤트 뒤 스트림이 닫힌다.
- 쓰기 턴: `running → awaiting_approval`에서 스트림이 열려 있고(heartbeat 간격을 작게 주어 `: ping`을 관찰), 승인 뒤 `running → completed` 이벤트를 받고 닫힌다.
- 새 턴이 시작되면 `turn_id`가 바뀐 이벤트가 나온다(새 연결로 확인).
- 소유: 다른 사용자·없는 id는 같은 404 JSON, 인증 없으면 401, 스트림이 열리지 않는다.
- 클라이언트가 중간에 끊으면 서버가 스트림 작업을 끝낸다(진행 중인 스트림 수 카운터가 유한한 시간 안에 0이 된다).
- 최대 시간: `sse_max_seconds`를 작게 주면 그때 닫힌다.
- 세션 TTL: `SESSION_TTL_SECONDS == 28800`이고 기본 `SessionStore`의 만료가 발급 시각 + 28800초다(시계 주입).

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

- `sse-starlette`를 import하거나 `pyproject.toml`·`uv.lock`을 고치지 마라. 이유: 의존성 설치는 사용자 몫이고 이 패키지는 선언되어 있지 않다. 표준 `StreamingResponse`로 충분하다.
- 이벤트 data에 네 키 외의 내용을 넣지 마라. 이유: SSE는 신호다. 텍스트·사유·문서 ID를 실으면 입력 차단 카드의 은닉 규칙과 승인자 화면의 범위가 깨진다.
- 스트림 안에서 잠금을 쥔 채 `sleep`·`await`로 대기하지 마라. 이유: 열린 연결 하나가 대화 잠금을 붙들면 승인과 새 턴이 막힌다. 폴링 한 번마다 잠금을 잡고 놓는다(FP-066).
- 스트림 테스트에서 시간 값에 기대어 도착 시각을 단언하거나 `sleep`으로 순서를 맞추지 마라. 이유: CI에서 흔들린다(FP-012·032). 게이트와 이벤트 열로 단언한다.
- 테스트 안에서 무한 스트림을 시간 제한 없이 읽지 마라. 이유: 멈춘 테스트가 CI를 막는다. 모든 읽기에 `timeout`, 끝에 `finally` 정리를 둔다(FP-066).

# Step 2: bff

## 읽어야 할 파일

- `/AGENTS.md`(웹 계층 경계, 토큰 비노출·세션 CRITICAL 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절), `/docs/ADR.md`(ADR-011), `/docs/THREAT_MODEL.md`(TH4·V4 행)
- `/src/govops/api/app.py`(라우트·상태 코드·SSE 형식·`DELETE /sessions`·`GET /demo/users`), `/src/govops/api/tasks.py`(`_view`·`approvals`·`ApprovalError` 상태 코드), `/src/govops/agent/draft.py`(`_FIELDS`: 초안 도구와 필드 이름), `/src/govops/api/smoke_conversation.py`(`FORBIDDEN` 키)
- `/web/src/guards.test.ts`, `/web/package.json`, `/web/tsconfig.json`, `/web/vitest.config.mts`, `/web/eslint.config.mjs`
- Next 16 문서(`/web/node_modules/next/dist/docs/01-app/` 아래): `01-getting-started/15-route-handlers.md`, `03-api-reference/03-file-conventions/route.md`(`params`가 Promise), `03-api-reference/04-functions/headers.md`, `02-guides/authentication.md`, `02-guides/data-security.md`, `02-guides/streaming.md`, `02-guides/self-hosting.md`, `02-guides/testing/vitest.md`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-030·066·068·069·070) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/package.json`(`scripts`의 `dev`·`start`만), `web/src/lib/server/env.ts`, `web/src/lib/server/session.ts`, `web/src/lib/server/origin.ts`, `web/src/lib/server/upstream.ts`, `web/src/lib/server/project.ts`, `web/src/lib/server/proxy.ts`, `web/src/lib/server/env.test.ts`, `web/src/lib/server/session.test.ts`, `web/src/lib/server/origin.test.ts`, `web/src/lib/server/project.test.ts`, `web/src/app/api/demo-users/route.ts`, `web/src/app/api/session/route.ts`, `web/src/app/api/conversations/route.ts`, `web/src/app/api/conversations/[id]/route.ts`, `web/src/app/api/conversations/[id]/turns/route.ts`, `web/src/app/api/conversations/[id]/events/route.ts`, `web/src/app/api/approvals/route.ts`, `web/src/app/api/tasks/[id]/approval/route.ts`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`. 모두 web 디렉터리 안의 서버 코드(route handler·서버 전용 라이브러리)다. 브라우저 코드(`"use client"`)는 이 step에서 만들지 않는다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/package.json`, `web/src/lib/server/env.ts`, `web/src/lib/server/session.ts`, `web/src/lib/server/origin.ts`, `web/src/lib/server/upstream.ts`, `web/src/lib/server/project.ts`, `web/src/lib/server/proxy.ts`, `web/src/lib/server/env.test.ts`, `web/src/lib/server/session.test.ts`, `web/src/lib/server/origin.test.ts`, `web/src/lib/server/project.test.ts`, `web/src/app/api/demo-users/route.ts`, `web/src/app/api/session/route.ts`, `web/src/app/api/conversations/route.ts`, `web/src/app/api/conversations/[id]/route.ts`, `web/src/app/api/conversations/[id]/turns/route.ts`, `web/src/app/api/conversations/[id]/events/route.ts`, `web/src/app/api/approvals/route.ts`, `web/src/app/api/tasks/[id]/approval/route.ts`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 브라우저는 FastAPI와 직접 통신하지 않는다. BFF(Next route handler)가 httpOnly 세션 쿠키에서 B 세션 토큰을 꺼내 `Authorization: Bearer`로 FastAPI(루프백)에 중계한다. 브라우저에는 토큰이 어떤 형태로도(응답 본문·JS가 읽는 쿠키·로컬 저장소) 전달되지 않는다. 아래 쿠키·CSRF 결정은 Next 16 `next start`에서 실측한 값이다: `NextResponse.cookies.set`은 `Max-Age`·`HttpOnly`·`SameSite=strict`를 그대로 내고 `delete`는 `Expires=1970`을 내며, `NextRequest.cookies.get`으로 읽는다. `req.url`의 origin은 `Host`와 상관없이 항상 `localhost`이므로 Origin 검사는 **`Origin`의 host와 `Host` 헤더**를 비교한다.

### 1. 서버 라이브러리 (`web/src/lib/server/`)

- `env.ts`: `envOr(name, fallback)`는 미설정·빈 문자열·공백뿐이면 기본값(FP-030의 TS판). `apiBase()`는 `GOVOPS_API_URL`(서버 전용, `NEXT_PUBLIC_` 금지)을 읽고 기본값은 `http://127.0.0.1:8000`이다. **호스트가 `127.0.0.1`인 http URL만** 허용하고 아니면 예외를 던진다(루프백 전용 FastAPI, `localhost`는 `::1`로 풀려 연결 실패할 수 있다).
- `session.ts`: 쿠키 이름 `govops_session`. `setSessionCookie(response, token, {expiresAt, origin})`은 `httpOnly: true`, `sameSite: "strict"`, `path: "/"`, `secure`는 **요청 `Origin`의 스킴이 `https:`일 때만** true, `maxAge = Math.max(0, Math.min(28800, Math.floor(expiresAt - Date.now()/1000)))`(`expiresAt`은 FastAPI가 주는 epoch 초). `maxAge`가 0이면 쿠키를 설정하지 않고 로그인 실패로 다룬다. `clearSessionCookie(response)`. `readSessionToken(request)`는 `NextRequest.cookies`에서 읽는다. 토큰 형식(`^[A-Za-z0-9_-]+$`)이 아니면 없는 것으로 본다.
- `origin.ts`: `requireSameOrigin(request)`는 **상태를 바꾸는 모든 메서드**(`GET`·`HEAD`·`OPTIONS`가 아닌 것: POST·PUT·PATCH·DELETE)에서 `Origin`이 없거나, URL로 파싱되지 않거나, `new URL(origin).host !== request.headers.get("host")`이면 403 응답(`{"code":"origin_mismatch"}`)을 돌려주고 통과하면 `null`을 돌려준다. `Host` 헤더가 없으면 403이다. `GET`은 검사하지 않는다.
- `upstream.ts`: `upstreamFetch(path, {method, token, body, signal})`. 요청 헤더에 `Authorization: Bearer <token>`(토큰이 있을 때만)과 JSON `content-type`을 붙인다. 응답·오류·로그에 토큰이나 업스트림 본문을 그대로 남기지 않는다. 연결 실패·타임아웃은 예외 클래스 이름만 가진 오류로 바꾼다. 비-스트림 호출에는 유한 timeout(예: 15초)을 둔다.
- `project.ts`: 업스트림 JSON을 화면 계약으로 **허용 목록 투영**한다(아래 3절). 투영 코드는 버리는 키 이름(`masked`·`output_flagged`·`guardrail_block`·`stop_reason`·`evidence_ids`·`guardrail_signals`·`guardrail_post` 등)을 소스에 쓰지 않는다. 허용한 키만 이름으로 꺼낸다.
- `proxy.ts`: 라우트 공통 도우미 `proxyJson(request, spec)`. 흐름: 변경 메서드면 `requireSameOrigin` → 쿠키에서 토큰 읽기(없으면 업스트림을 부르지 않고 401 `{"code":"unauthenticated"}`) → 입력 검증 → 업스트림 호출 → 응답 처리. 업스트림 401은 쿠키를 지우고 401로 돌려준다. 업스트림 4xx는 같은 상태 코드와 `{"code": <허용 목록 코드>}`로, 5xx·연결 실패는 502 `{"code":"upstream_unavailable"}`로 돌려준다. 성공은 `project` 함수로 투영한 JSON이다. 모든 JSON 응답에 `Cache-Control: no-store`를 붙인다.
- **업스트림 오류 코드 허용 목록**: `approval_pending`·`turn_in_progress`·`turn_limit`·`conversation_not_found`·`invalid_request`는 업스트림 `detail`을 그대로 쓴다. 승인 API의 4xx는 상태로 정한다: 403 → `approval_forbidden`, 404 → `task_not_found`, 409 → `approval_conflict`, 422 → `invalid_edit`. `/sessions` 로그인: 403 → `loopback_required`, 404 + `detail == "User not found"` → `user_not_found`, 404 + 그 밖 → `demo_login_disabled`. 그 밖은 `request_failed`.

### 2. 라우트 (`web/src/app/api/**/route.ts`, 모두 `export const dynamic = "force-dynamic"`)

route handler는 `next/headers`의 `cookies()`를 쓰지 않는다. 읽기는 `NextRequest.cookies`, 쓰기·삭제는 `NextResponse.cookies`로 한다. 동적 세그먼트는 `{ params }: { params: Promise<{ id: string }> }`를 `await`한다. 경로의 `id`는 `^[0-9a-f]{32}$` 형식이 아니면 업스트림을 부르지 않고 404 `{"code":"not_found"}`다(형식 검증, PII 정규식 금지 — FP-010).

| 파일 | 메서드 | 업스트림 | 비고 |
|---|---|---|---|
| `demo-users/route.ts` | GET | `GET /demo/users` | 인증 없음. 응답 `{"users":[라벨…]}` |
| `session/route.ts` | GET | `GET /sessions/me` | 응답 `{"user_id"}`. 401이면 쿠키 삭제 |
| `session/route.ts` | POST | `POST /sessions` | 본문 `{"user": "demo-<팀>-g<숫자>"}`만(형식 `^demo-[a-z-]+-g[0-9]+$`, 아니면 422 `invalid_request`). 성공 시 쿠키 설정, 응답 `{"ok":true,"user_id"}` — **세션 토큰과 세션 만료(`expires_at`)는 응답에 없다** |
| `session/route.ts` | DELETE | `DELETE /sessions` | 쿠키가 있으면 업스트림 세션을 폐기하고(업스트림 401은 이미 무효이므로 성공으로 본다) **쿠키는 항상 지운다**. 업스트림이 5xx·연결 실패면 502 `upstream_unavailable`(쿠키는 지운 채) |
| `conversations/route.ts` | POST | `POST /conversations` | 본문 `{"request": 1~2000자 문자열}`만 |
| `conversations/[id]/route.ts` | GET | `GET /conversations/{id}` | 투영 |
| `conversations/[id]/turns/route.ts` | POST | `POST /conversations/{id}/turns` | 본문 `{"request"}`만 |
| `conversations/[id]/events/route.ts` | GET | `GET /conversations/{id}/events` | SSE 중계(아래) |
| `approvals/route.ts` | GET | `GET /approvals` | 투영 |
| `tasks/[id]/approval/route.ts` | POST | `POST /tasks/{id}/approval` | 본문 `{"decision": "approve"|"reject"|"edit_approve", "draft_sha256": 64 hex, "edited"?: {문자열: 문자열}}`만(`edited`는 `edit_approve`일 때만). 업스트림 202·200 상태를 그대로 전달 |

- 모든 본문은 **알려진 키만** 통과시키고 알 수 없는 키가 있으면 422 `invalid_request`다. 사용자·팀·직급·task 소유자 같은 필드를 업스트림에 만들어 보내지 않는다(신원은 세션에서만 정해진다). 로그인 본문의 `user`가 유일한 예외다.
- **SSE 중계**: 인증·id 검사 뒤 `upstreamFetch(…, {signal: request.signal})`로 연결하고, 업스트림 응답이 2xx이며 `text/event-stream`일 때만 본문 스트림을 그대로 `Response`로 넘긴다. 헤더는 `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`다. 클라이언트가 끊으면 `request.signal`로 업스트림 요청이 취소돼야 한다(실측: signal을 넘기지 않으면 업스트림이 계속 흐른다). 업스트림이 401이면 쿠키를 지우고 401, 404면 404, 그 밖의 오류는 502다. 이 스트림은 업스트림 SSE 계약(`data`는 `conversation_id`·`turn_id`·`status`·`seq`뿐)을 그대로 중계하는 순수 중계다.
- `web/package.json`의 `scripts`: `dev`는 `next dev -H 127.0.0.1 -p 3100`, `start`는 `next start -H 127.0.0.1 -p 3100`으로 바꾼다. 다른 키(의존성·`engines`·`packageManager`)는 바꾸지 않는다.

### 3. 투영 (`project.ts`)

모든 투영은 없는 키를 `null`·빈 값으로 채우지 않고, 형식이 맞지 않는 값은 버린다(문자열 길이 상한 2000자, ID 형식 `^[A-Za-z0-9._-]{1,80}$`).

- **대화 view** (`GET /conversations/{id}`): `{conversation_id, turn_index, history: [{role: "user"|"assistant", content}], turn}`. `turn`: `turn_id`, `status`(알려진 값만: `running`·`awaiting_approval`·`completed`·`failed`·`rejected`·`expired`·`blocked_input`·`interrupted`·`blocked`), `request`(차단 상태이거나 null이면 키 없음), `answer`, `cited_doc_ids`, `error`(`^[a-z_]{1,40}$`일 때만), `guardrail: {quarantined(정수), quarantined_at_least(불리언)}`, `block: {code("injection_direct"|"pii_input"|"other"), message}`(차단일 때만), `draft`, `draft_sha256`(64 hex), `check: {status, message}`, `expires_at`, `effect: {state, mock_id}`, `rejected_edit: {status, message}`, `retry_hint`.
- **초안** `draft`: `{tool, args}`. `tool`은 `slack_send_message`·`jira_add_comment`·`jira_create_followup_ticket`뿐이고, `args`는 `channel`·`text`·`ticket_id`·`body`·`parent_ticket_id`·`title`·`description`·`priority` 중 문자열 값만 통과한다.
- **승인함 행** (`GET /approvals`): `{approvals: [{turn_id(업스트림 task_id), status, requester_label, draft, draft_sha256, check, expires_at, conversation_id?}]}`. `conversation_id`는 있을 때만(32 hex) 넘긴다. `history`·`request`·`answer`·`messages`는 업스트림이 잘못 보내도 넘기지 않는다.
- 버리는 것: 입력 원문(차단 턴), 판정 근거, 출력 사후 탐지 결과, `guardrail.masked`, `observed_doc_ids`, `steps`, `effect_result`, 내부 상태 키 전부.

### 4. 테스트

**합성 값의 취급**(공통 금지사항의 "비밀 값"은 실제 토큰·키를 가리킨다): 테스트의 가짜 세션 토큰은 실행할 때 `node:crypto`의 무작위 값(`randomBytes(...).toString("base64url")`)으로 만들고 소스에 토큰 리터럴을 쓰지 않는다. 스텁 업스트림은 수신한 `Authorization` 헤더를 변수로 보관해 기대 값과 **비교만** 하고 출력·로그·스냅샷에 남기지 않는다. 금지 키 센티널(`SENTINEL-…`)은 비밀이 아닌 합성 문자열이다. 이 합성 값은 공통 금지사항의 대상이 아니다.

- `env.test.ts`: 미설정·빈 문자열·공백에서 기본값, 루프백이 아닌 호스트·`localhost`·`https` 거부.
- `origin.test.ts`: 변경 메서드(POST·PUT·PATCH·DELETE)에서 Origin 없음·형식 오류·host 불일치·Host 헤더 없음은 403, `Origin: http://127.0.0.1:3100`과 `Host: 127.0.0.1:3100`은 통과, `GET`은 검사하지 않음. 요청 객체가 `host` 헤더를 허용하지 않아 라우트 수준 Host 테스트가 불가능하면 Origin 판정 순수 함수(`origin`, `host` 문자열 입력) 테스트로 대신하되, 위 케이스(변경 메서드 4종 × 없음·형식 오류·불일치·Host 없음·통과)를 **모두 유지**하고 route 표 가드의 "변경 메서드마다 `requireSameOrigin` 호출 존재" 검사도 그대로 둔다. 대체한 사실은 테스트 주석에 적는다. 단언 강도는 낮추지 않는다.
- `session.test.ts`: 쿠키 속성(`HttpOnly`, `SameSite=strict`, `Path=/`, `Max-Age`), `Secure`는 `https` Origin에서만, `maxAge = min(28800, …)`와 0 이하 처리, 삭제 쿠키, 토큰 형식 검사.
- `project.test.ts`: 업스트림 응답에 센티널 문자열을 담은 금지 키(`request`(차단 턴), `messages`, `history`(승인함), 임의 `rules`, `guardrail.masked`, 판정 근거 필드)와 토큰 모양 문자열을 섞어 넣고 **투영 결과의 JSON 문자열에 센티널·토큰이 없음**을 단언한다. 같은 테스트에서 허용 필드(답, 근거 문서, 초안, 검사 메시지, 만료 시각)가 **실제로 들어 있음**도 함께 단언한다(FP-069: 빈 결과로 공허하게 통과 금지). 투영 소스에 금지 키 이름이 없다는 소스 스캔 테스트도 둔다.
- `routes.test.ts` (**route 표 가드**, 실제 파일 트리를 읽는다): `web/src/app/api/**/route.ts` 파일 경로 **이름 집합**이 위 표의 8개 파일과 정확히 같다(개수가 아니라 이름 집합, FP-061). 각 파일에 `export const dynamic = "force-dynamic"`, 어느 파일도 `next/headers`를 import하지 않음, `POST`·`PUT`·`PATCH`·`DELETE`를 export하는 파일은 `requireSameOrigin`을 호출한다(소스 스캔). `"use client"` 파일이 `@/lib/server`를 import하지 않음.
- `bff.integration.test.ts` (`// @vitest-environment node`, **실제 HTTP 스텁 업스트림까지 소켓 왕복**): `node:http` 서버를 포트 0으로 열고 `GOVOPS_API_URL`을 그 주소(`127.0.0.1`)로 설정한 뒤 각 route 모듈의 export 함수(`GET`·`POST`·`DELETE`)를 **실제 `NextRequest`(쿠키·`Origin`·`Host` 헤더 포함)로 호출**한다. 스텁은 FastAPI의 실제 계약에서 BFF에 중요한 부분을 재현한다: 모르는 Bearer는 401, `POST /sessions`는 토큰과 `expires_at`를 주고, `DELETE /sessions`는 그 토큰을 폐기하며, SSE는 청크로 나눠 흘리고 연결이 끊긴 것을 기록한다. 검증: ① 로그인 → `Set-Cookie` 속성, 응답 본문에 토큰 없음 ② 쿠키로 `GET /api/session` 200, 쿠키 없음·잘못된 쿠키 401과 삭제 쿠키 ③ 변경 메서드의 Origin 없음·악성 Origin 403(업스트림 호출 0건) ④ 로그아웃이 업스트림 세션을 폐기하고 쿠키를 지움, 이후 같은 토큰 업스트림 401 ⑤ 대화 시작·턴·조회·승인 요청 본문 검증(알 수 없는 키 422)과 업스트림 오류 코드 매핑(409 세 종류, 404, 403) ⑥ 투영: 스텁이 금지 키를 섞어 보내도 응답에 없음 ⑦ SSE 중계: 청크가 도착하는 대로 읽히고, 클라이언트가 `AbortController`로 끊으면 **스텁이 연결 종료를 기록**한다(유한 대기). 모든 소켓·타이머·서버는 `afterEach`/`finally`에서 닫고, 대기에는 timeout을 둔다(FP-066).
- 기존 `web/src/guards.test.ts`를 약화하지 않는다. 새 파일은 그 스캔(공개 변수 비밀 이름, 로컬 저장소 토큰, 브라우저 파일의 FastAPI 직접 호출)을 위반하지 않는다.

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

- route handler에서 `next/headers`의 `cookies()`를 쓰지 마라. 이유: 쿠키 읽기는 `NextRequest.cookies`, 쓰기·삭제는 `NextResponse.cookies`로 통일하고 `next start`에서 속성(`HttpOnly`·`SameSite`·`Max-Age`)이 그대로 나감을 실측했다. route 표 가드가 `next/headers` import를 막는다.
- Origin을 `new URL(request.url).origin`과 비교하지 마라. 이유: 실측에서 `req.url`의 origin은 `Host`와 무관하게 항상 `http://localhost:포트`라서 `127.0.0.1`로 접속한 정상 요청이 모두 403이 된다. `Origin`의 host와 `Host` 헤더를 비교한다.
- SSE 업스트림 `fetch`에서 `signal: request.signal`을 빼지 마라. 이유: 실측에서 signal이 없으면 클라이언트가 끊어도 업스트림 스트림이 끝까지 흘렀다. 중단 전파는 통합 테스트로 고정한다(FP-068).
- 응답 본문·로그·오류에 세션 토큰, **세션 만료 필드**(`POST /sessions` 업스트림 응답의 `expires_at`, 쿠키 `Max-Age` 계산에만 쓴다), 업스트림 응답 원문을 넣지 마라. 승인 만료를 뜻하는 투영 필드 `expires_at`(대화 view의 `turn`·승인함 행)은 이 금지의 대상이 아니며 화면 계약에 포함된다. 투영 코드에 버리는 키 이름을 쓰지도 마라. 이유: 비노출은 "허용한 키만 꺼내는" 허용 목록으로 지키고, 거부 목록은 새 키가 생기면 새로 샌다. 테스트는 허용 필드가 실제로 존재함도 함께 확인한다(FP-069).
- `GOVOPS_API_URL`을 `NEXT_PUBLIC_` 변수나 클라이언트 코드에 노출하지 말고, 빈 값이 기본값을 덮게 두지 마라. 이유: 브라우저는 FastAPI 주소를 몰라야 하고 `.env.example` 방식의 빈 값이 기본 주소를 지우면 안 된다(FP-030).

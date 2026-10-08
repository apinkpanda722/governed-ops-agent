# Step 3: bff

## 읽어야 할 파일

- `/AGENTS.md`(웹 계층 경계, 토큰·화면 비노출 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절), `/docs/THREAT_MODEL.md`(TH4·V4 행)
- 이전 step 산출물: `/src/govops/api/app.py`(`GET /conversations`), `/src/govops/api/tasks.py`(`_view`의 `tool_steps`·`approval_method`·`effect_at`, 목록 항목 키)
- `/web/src/lib/server/project.ts`·`project.test.ts`, `/web/src/lib/server/proxy.ts`, `/web/src/lib/server/upstream.ts`, `/web/src/app/api/conversations/route.ts`, `/web/src/app/api/routes.test.ts`, `/web/src/app/api/bff.integration.test.ts`, `/web/src/guards.test.ts`
- Next 16 문서(`/web/node_modules/next/dist/docs/01-app/` 아래): `01-getting-started/15-route-handlers.md`, `03-api-reference/03-file-conventions/route.md`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-060·061·066·068·069·070) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/app/api/conversations/route.ts`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`. 모두 web 디렉터리 안의 서버 코드(route handler·서버 전용 라이브러리)다. 브라우저 코드(`"use client"`)는 이 step에서 수정하지 않는다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/app/api/conversations/route.ts`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 브라우저는 BFF만 부른다. 새 API 필드와 대화 목록을 **허용 목록 투영**으로 화면에 넘긴다. 투영 코드는 버리는 키 이름(`masked`·`output_flagged`·`guardrail_block`·`stop_reason`·`evidence_ids`·`guardrail_signals`·`guardrail_post` 등)을 소스에 쓰지 않고 허용한 키만 이름으로 꺼낸다. 세션 토큰은 응답 어디에도 없다. 쿠키 읽기·쓰기는 `NextRequest.cookies`·`NextResponse.cookies`로 하고 `next/headers`는 쓰지 않는다(기존 규칙).

### 1. 투영 (`project.ts`)

- `projectTurn`에 다음을 더한다. 기존 필드와 규칙은 그대로다.
  - `tool_steps`: 배열이면 각 항목에서 `name`(`^[a-z_]{1,40}$`인 문자열)과 `ok`(불리언)가 **둘 다 맞는 것만** `{name, ok}`로 통과시키고 그 밖의 키는 버린다. 최대 20개.
  - `approval_method`: 문자열이 `approve` 또는 `edit_approve`일 때만.
  - `effect_at`: 문자열이고 `Date.parse`가 유한한 값이며 길이 64 이하일 때만.
- `projectApprovals`(승인함 행)는 위 세 키를 **업스트림이 보내도 통과시키지 않는다**(요청자 본인 뷰 전용 필드).
- `projectConversationList(value)`: `{conversations: [...]}`. 항목마다 `conversation_id`(`^[0-9a-f]{32}$`), `title`(문자열, 길이 80 이하, 빈 문자열이면 `"새 대화"`), `status`(알려진 상태 값만), `awaiting_approval`(불리언), `turn_count`(0 이상 정수), `updated_at`(문자열, 길이 64 이하)만 통과한다. 형식이 맞지 않는 항목은 버린다. 최대 50개. 배열이 아니면 빈 객체.

### 2. 라우트 (`conversations/route.ts`)

- 기존 `POST`는 그대로 두고 `GET`을 더한다: 쿠키가 없으면 업스트림을 부르지 않고 401(기존 프록시 규칙), 있으면 업스트림 `GET /conversations?limit=50`을 호출해 `projectConversationList`로 투영한다. **쿼리는 BFF가 고정한다**(클라이언트의 `?limit=` 등 쿼리 문자열은 업스트림에 전달하지 않는다). 업스트림 401은 쿠키를 지우고 401, 5xx·연결 실패는 502 `upstream_unavailable`, 모든 JSON 응답에 `Cache-Control: no-store`(기존 프록시 규칙 그대로).
- 라우트 파일 이름 집합은 바뀌지 않는다(8개). `GET`은 Origin 검사 대상이 아니고 `POST`는 기존대로 `requireSameOrigin`을 호출한다.

### 3. 테스트

- `project.test.ts`: ① `tool_steps`에 센티널을 담은 `args`·`result`·`arguments`·문서 본문 키를 섞고 이름이 잘못된 항목(`Bad-Name`·길이 초과·`ok`가 문자열)을 넣어도 **투영 결과의 JSON 문자열에 센티널이 없고** 올바른 항목만 `{name, ok}`로 남는다(올바른 항목이 **실제로 존재함**도 함께 단언, FP-069). 21개를 넣으면 20개로 잘린다. ② `approval_method`·`effect_at`의 허용·거부 값. ③ `projectApprovals`는 세 키를 업스트림이 보내도 버린다. ④ 목록 투영: 정상 항목은 키 여섯 개만, 센티널 `request`·`history`·`answer` 키는 버림, 잘못된 항목 제외, 50개 상한. ⑤ 투영 소스에 금지 키 이름이 없다는 기존 소스 스캔 테스트가 새 코드에도 통과한다.
- `bff.integration.test.ts`(소켓 스텁 업스트림, `// @vitest-environment node`): 스텁에 `GET /conversations`를 더해 ① 쿠키로 `GET /api/conversations` 200과 투영된 본문 ② 쿠키 없음 401(업스트림 호출 0건) ③ 업스트림 401 → 삭제 쿠키 ④ 클라이언트 쿼리(`?limit=1000`)가 업스트림에 전달되지 않고 업스트림이 받은 쿼리는 `limit=50` ⑤ 5xx → 502 ⑥ 스텁이 응답에 센티널 키를 섞어도 본문에 없음. 스텁의 토큰은 실행 때 무작위로 만든 합성 값이며 소스에 리터럴로 쓰지 않는다. 모든 소켓·서버는 `afterEach`/`finally`에서 닫고 대기에는 timeout을 둔다(FP-066).
- `routes.test.ts`: 라우트 파일 이름 집합이 여전히 같고(개수가 아니라 이름 집합, FP-061), `conversations/route.ts`가 `GET`·`POST`를 export하며 `POST`가 Origin 검사를 호출한다는 기존 가드가 통과한다. 새 메서드를 표에 반영한다.

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

- 투영에서 업스트림 객체를 통째로 복사하거나 `{...value}`로 펼치지 마라. 이유: 허용한 키만 이름으로 꺼내야 새 필드가 생겨도 새어 나가지 않는다. 테스트는 허용 필드가 실제로 존재함도 함께 확인한다(FP-069).
- 클라이언트가 보낸 쿼리 문자열이나 헤더를 업스트림에 그대로 전달하지 마라. 이유: 목록 상한과 경로는 BFF가 고정한다. 임의 쿼리 전달은 업스트림 검증에 기대는 우회 경로가 된다.
- `tool_steps`·`approval_method`·`effect_at`을 승인함 행에 통과시키지 마라. 이유: 요청자 본인 턴 뷰 전용 필드이고 다른 승인자의 화면에 나가면 안 된다.
- 브라우저 코드·`web/src/lib/types.ts`·`web/src/lib/testids.ts`를 수정하지 마라. 이유: 화면 계약의 클라이언트 쪽 타입은 다음 web step의 범위이고 이 step은 서버 투영만 맡는다(한 step에 한 계층).
- 소스에 세션 토큰·`Authorization` 값 리터럴을 쓰지 말고 테스트의 합성 토큰은 실행 때 무작위로 만들어라. 이유: 공통 금지사항의 "비밀 값"은 실제 토큰·키를 가리키며, 합성 값은 소스에 리터럴로 남기지 않는 것으로 구분한다.

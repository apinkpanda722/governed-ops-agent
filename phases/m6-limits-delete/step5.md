# Step 5: web-delete-ui

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/web/AGENTS.md`, `/web/CLAUDE.md`(웹 규칙), `/AGENTS.md`(웹 계층 경계)
- Next 16 문서(`/web/node_modules/next/dist/docs/01-app/` 아래): `01-getting-started/15-route-handlers.md`, `03-api-reference/03-file-conventions/route.md`
- `/web/src/app/api/conversations/[id]/route.ts`, `/web/src/app/api/conversations/[id]/turns/route.ts`(`requireSameOrigin` 호출 패턴), `/web/src/lib/server/proxy.ts`, `/web/src/lib/server/project.ts`, `/web/src/lib/server/origin.ts`
- `/web/src/components/conversation-list.tsx`, `/web/src/components/conversation-list-context.tsx`, `/web/src/hooks/use-conversation-list.ts`, `/web/src/lib/bff.ts`, `/web/src/lib/messages.ts`, `/web/src/lib/testids.ts`
- `/web/src/app/api/routes.test.ts`, `/web/src/app/api/bff.integration.test.ts`, `/web/src/lib/server/project.test.ts`, `/web/src/guards.test.ts`, `/web/src/ui-boundary.test.ts`, `/web/src/components/sidebar.test.tsx`, `/web/src/components/app-shell.test.tsx`, `/web/e2e/support.ts`, `/web/e2e/approval-flow.spec.ts`, `/web/e2e/blocked-and-isolation.spec.ts`, `/web/src/e2e-config.test.ts`
- 이전 step 산출물: `/src/govops/api/app.py`의 `DELETE /conversations/{conversation_id}`(200, `{conversation_id, deleted}`)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/app/api/conversations/[id]/route.ts`, `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`, `web/src/components/conversation-list.tsx`, `web/src/components/conversation-list.test.tsx`, `web/src/components/sidebar.test.tsx`, `web/src/components/app-shell.test.tsx`, `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/e2e/delete-conversation.spec.ts`, `web/src/e2e-config.test.ts`, `tests/test_web_sync.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `web/src/app/api/conversations/[id]/route.ts`, `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`, `web/src/components/conversation-list.tsx`, `web/src/components/conversation-list.test.tsx`, `web/src/components/sidebar.test.tsx`, `web/src/components/app-shell.test.tsx`, `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/e2e/delete-conversation.spec.ts`, `web/src/e2e-config.test.ts`, `tests/test_web_sync.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`을 읽지 않는다(웹 코드와 vitest만 다룬다).

**배경**: 사용자가 자기 대화를 삭제할 수 있는 BFF 라우트와 목록 UI를 만든다. 브라우저는 BFF만 부르고(FastAPI 직접 호출 금지) 세션 토큰은 httpOnly 쿠키에만 있다. 의존성 변경 없음: `web/package.json`·`web/pnpm-lock.yaml`·`pyproject.toml`·`uv.lock`을 수정하지 않는다(새 대화상자 패키지를 추가하지 않는다).

이 이름들(`DELETE` 핸들러, `projectDeleted`, `conversationDelete`)의 기존 사용처는 없다. `grep`으로 확인했다.

### 1. BFF (`conversations/[id]/route.ts`, `project.ts`)

- `GET`은 그대로 두고 `DELETE`를 더한다: `requireSameOrigin(request)`를 **직접 호출**해 막히면 반환(`turns/route.ts`와 같은 형태, `routes.test.ts`가 소스에서 이 호출을 찾는다) → `const { id } = await params` → `proxyJson(request, { path: `/conversations/${id}`, project: projectDeleted, id })`. `export const dynamic = "force-dynamic"`은 유지한다.
- `projectDeleted(value)`(`project.ts`)는 업스트림 본문에서 `deleted === true`만 꺼내 `{ deleted: true }`를 돌려주고 그 밖은 `{ deleted: false }`다. 다른 키를 통과시키지 않는다.
- 오류 코드 매핑은 이미 `proxy.ts`의 `upstreamError`에 있다(`conversation_not_found`·`turn_in_progress`·`approval_pending`). `proxy.ts`는 수정하지 않는다.

### 2. 목록 UI (`conversation-list.tsx`)

- 각 항목에 삭제 버튼을 둔다(`testids.conversationDelete(id)`). 누르면 항목 아래에 **인라인 확인 영역**이 열리고 다음 문구를 그대로 보인다(상수 `DELETE_CONFIRM_NOTICE`는 `messages.ts`에 둔다): "대화를 삭제할까요? 원문은 다음 정리 때 제거되고, 감사 기록과 이미 발송된 효과는 남는다." 확인 버튼(`testids.conversationDeleteConfirm(id)`)과 취소 버튼(`testids.conversationDeleteCancel(id)`)이 있다. 브라우저 모달(`window.confirm`·`alert`)을 쓰지 않는다.
- 확인하면 `bff("/api/conversations/<id>", { method: "DELETE" })`를 부르고, **서버가 성공을 돌려준 뒤에만** 목록을 `refresh()`한다. 현재 경로가 `/c/<id>`이면 `/c`로 이동한다. 실패하면 `errorMessage(code)`를 `role="alert"`로 항목 아래에 보인다(승인 대기 409는 기존 `approval_pending` 문구).
- 키보드로 삭제 버튼·확인·취소에 접근할 수 있다. 낙관적 제거는 하지 않는다.
- `useRouter`가 필요해 `sidebar.test.tsx`·`app-shell.test.tsx`의 렌더가 깨지면 그 테스트의 라우터 모의만 고친다(~에 한함, 단언 약화 금지).

### 3. 테스트

- `conversation-list.test.tsx`(신규, vitest): 확인 전·취소 시 DELETE 호출 0 / 확인 시 DELETE 정확히 1회(메서드·경로) / 성공 뒤 `refresh` 호출과 현재 경로 이동 / 실패 코드별 문구(`approval_pending`·`turn_in_progress`·`conversation_not_found`) / 확인 문구가 위 문장과 정확히 같음 / 버튼이 실제로 존재함(FP-069: 존재 확인).
- `bff.integration.test.ts`(소켓 스텁 업스트림, `// @vitest-environment node`): 쿠키 없음 401(업스트림 호출 0) / Origin 불일치 403 / 업스트림 200 → `{deleted: true}` / 404 → `conversation_not_found` / 409 → `approval_pending`·`turn_in_progress` / 5xx → 502 / 형식이 틀린 id → 404(업스트림 호출 0). 스텁의 토큰은 실행 때 무작위로 만든 합성 값이고 소스에 리터럴로 쓰지 않는다. 소켓·서버는 `afterEach`/`finally`에서 닫는다(FP-066).
- `routes.test.ts`: 라우트 파일 이름 집합은 그대로(8개)이고 `[id]/route.ts`가 `GET`·`DELETE`를 export하며 `DELETE`가 `requireSameOrigin(request)`를 호출한다는 기존 가드가 통과한다.
- `project.test.ts`: `projectDeleted`가 `deleted: true`만 통과시키고 센티널 키를 버린다.
- e2e 스펙 `web/e2e/delete-conversation.spec.ts`: replay 백엔드에서 대화를 만들고 삭제해 목록에서 사라지고 `/c/<id>`가 "대화를 찾을 수 없습니다"를 보이는지, 삭제 확인 문구가 보이는지 확인한다. 이 step에서는 `playwright test --list`(verify에 포함)로 수집만 된다.
- `web/src/e2e-config.test.ts`(**~에 한함, 단언 약화 금지**): 12–13행의 `expect(specs).toHaveLength(2)`를 **정렬한 스펙 파일 이름의 정확 일치** 단언으로 바꾼다. 기대값은 `["approval-flow.spec.ts", "blocked-and-isolation.spec.ts", "delete-conversation.spec.ts"]`(정렬순) 세 개이고 개수만 늘리는 방식(`toHaveLength(3)`)은 쓰지 않는다. 14–21행의 소스 제약(`console.`·`storageState`·`waitForTimeout`·`data-testid`·`trace|screenshot|video:` 금지, `document.cookie` 금지(`document.cookie.length`만 허용), `cookies(` 사용 시 `.value` 금지, `from "../src/lib/testids"` import 필수, `switchUser` 금지, `getByTestId("…")` 문자열 리터럴 금지)과 `import.meta` 금지 검사는 **그대로 둔다**. 새 스펙 `delete-conversation.spec.ts`가 이 제약을 모두 통과해야 한다(`testids`의 함수로만 요소를 찾고 모달·`waitForTimeout`·`console`을 쓰지 않는다). 이 파일의 다른 테스트는 수정하지 않는다.

**사용자 실행(이 step에서 실행하지 않는다)**: `make e2e-web`(포트 3200·8200·8201이 비어 있고 3100의 `next start` 서버가 꺼져 있어야 한다).

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

- 브라우저 코드에서 FastAPI를 직접 호출하지 마라. 이유: 웹 계층 경계(AGENTS.md)다. 세션 토큰은 BFF만 다룬다.
- 새 패키지(대화상자 라이브러리 등)를 추가하거나 lockfile을 바꾸지 마라. 이유: 의존성 변경 없음이 이 phase의 조건이다. 인라인 확인 영역으로 구현한다.
- `window.confirm`·`window.alert`를 쓰지 마라. 이유: 브라우저 모달은 자동화(e2e)를 멈추고 접근성 테스트에 잡히지 않는다.
- 서버 응답 전에 목록에서 항목을 제거하지 마라. 이유: 409(승인 대기·실행 중)에서 화면과 서버 상태가 어긋난다.
- `proxy.ts`·`origin.ts`·`bff.ts`를 수정하지 마라. 이유: 오류 코드 매핑과 Origin 검사가 이미 있고 이 step은 라우트와 목록 UI만 맡는다. `make e2e-web`을 실행하지 마라. 이유: 사용자 실행 항목이다.

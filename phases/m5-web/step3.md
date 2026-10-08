# Step 3: ui-conversation

## 읽어야 할 파일

- `/AGENTS.md`(웹 계층 경계, 토큰 비노출 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절), `/docs/ADR.md`(ADR-011, ADR-014), `/docs/THREAT_MODEL.md`(V4·TH4 행)
- 이전 step 산출물: `/web/src/lib/server/project.ts`(화면이 받는 투영 계약), `/web/src/app/api/session/route.ts`, `/web/src/app/api/conversations/route.ts`, `/web/src/app/api/conversations/[id]/route.ts`, `/web/src/app/api/conversations/[id]/turns/route.ts`, `/web/src/app/api/conversations/[id]/events/route.ts`, `/web/src/app/api/demo-users/route.ts`, `/web/src/lib/server/proxy.ts`(오류 코드 목록)
- `/web/src/guards.test.ts`, `/web/src/components/ui/button.tsx`·`card.tsx`·`alert.tsx`·`badge.tsx`·`textarea.tsx`, `/web/src/app/layout.tsx`, `/web/src/app/page.tsx`, `/web/src/app/globals.css`, `/web/vitest.config.mts`, `/web/eslint.config.mjs`
- Next 16 문서(`/web/node_modules/next/dist/docs/01-app/` 아래): `01-getting-started/15-route-handlers.md`, `02-guides/forms.md`, `02-guides/streaming.md`, `02-guides/testing/vitest.md`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-066·068·069) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/lib/types.ts`, `web/src/lib/bff.ts`, `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/src/hooks/use-session.ts`, `web/src/hooks/use-conversation.ts`, `web/src/components/app-shell.tsx`, `web/src/components/thread.tsx`, `web/src/components/turn-view.tsx`, `web/src/components/blocked-card.tsx`, `web/src/components/quarantine-note.tsx`, `web/src/components/composer.tsx`, `web/src/components/conflict-notice.tsx`, `web/src/components/approval-slot.tsx`, `web/src/app/layout.tsx`, `web/src/app/page.tsx`, `web/src/app/login/page.tsx`, `web/src/app/(app)/layout.tsx`, `web/src/app/(app)/c/page.tsx`, `web/src/app/(app)/c/[id]/page.tsx`, `web/src/ui-boundary.test.ts`, `web/src/lib/bff.test.ts`, `web/src/hooks/use-conversation.test.tsx`, `web/src/components/thread.test.tsx`, `web/src/components/composer.test.tsx`, `web/src/app/login/page.test.tsx`. 모두 브라우저(클라이언트) 코드와 화면 테스트다. 서버 코드(`web/src/lib/server/`·`web/src/app/api/`)는 읽기만 하고 수정하지 않는다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/src/lib/types.ts`, `web/src/lib/bff.ts`, `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/src/hooks/use-session.ts`, `web/src/hooks/use-conversation.ts`, `web/src/components/app-shell.tsx`, `web/src/components/thread.tsx`, `web/src/components/turn-view.tsx`, `web/src/components/blocked-card.tsx`, `web/src/components/quarantine-note.tsx`, `web/src/components/composer.tsx`, `web/src/components/conflict-notice.tsx`, `web/src/components/approval-slot.tsx`, `web/src/app/layout.tsx`, `web/src/app/page.tsx`, `web/src/app/login/page.tsx`, `web/src/app/(app)/layout.tsx`, `web/src/app/(app)/c/page.tsx`, `web/src/app/(app)/c/[id]/page.tsx`, `web/src/ui-boundary.test.ts`, `web/src/lib/bff.test.ts`, `web/src/hooks/use-conversation.test.tsx`, `web/src/components/thread.test.tsx`, `web/src/components/composer.test.tsx`, `web/src/app/login/page.test.tsx`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 브라우저 화면이다. 화면은 BFF(`/api/*`)만 부른다. 화면 계약은 step 2의 투영(`project.ts`)이다. 화면은 **입력 원문(차단된 요청)·판정 근거·출력 사후 탐지 결과·문서 원문**을 표시하지 않는다. 스타일은 Tailwind + 이미 있는 shadcn 컴포넌트(`card`·`alert`·`badge`·`textarea`·`button`)를 쓰고 한국어 문구를 쓴다. 승인 카드의 구현은 다음 step이고 이 step은 `approval-slot.tsx`를 **빈 자리 컴포넌트**(`null` 렌더, 다음 step이 교체)로 둔다.

### 1. 공용 (`web/src/lib/`)

- `types.ts`: step 2 투영과 같은 모양의 화면 타입(`ConversationView`, `TurnView`, `Draft`, `CheckView`, `BlockView`, `ApprovalRow`, `ApiError`). 서버 코드를 import하지 않고 타입만 따로 정의한다.
- `bff.ts`: 클라이언트 `fetch` 래퍼. 모든 호출은 `/api/…` 상대 경로, `credentials: "same-origin"`, 변경 메서드는 JSON 본문. 응답이 401이면 `onUnauthenticated()`(기본 `window.location.assign("/login")`을 부르는 교체 가능한 훅)를 호출하고 오류를 던진다. 오류는 `{status, code}`만 담는다(응답 본문 원문·토큰을 담지 않는다). 토큰·세션 값을 읽거나 저장하는 코드를 두지 않는다.
- `messages.ts`: 오류 코드 → 한국어 문구 표. 정확한 문구: `approval_pending` → "이 대화에 처리 중인 승인 요청이 있습니다. 먼저 승인하거나 거부해 주세요.", `turn_in_progress` → "이전 요청을 처리하는 중입니다. 잠시 후 다시 시도해 주세요.", `turn_limit` → "이 대화의 턴 한도에 도달했습니다. 새 대화를 시작해 주세요.", `conversation_not_found` → "대화를 찾을 수 없습니다.", `invalid_request` → "요청을 확인해 주세요. 1자 이상 2000자 이하로 입력해야 합니다.", `upstream_unavailable` → "서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.", `demo_login_disabled` → "데모 로그인이 꺼져 있습니다.", `loopback_required` → "로컬 접속에서만 로그인할 수 있습니다.", `user_not_found` → "사용자를 찾을 수 없습니다.", 그 밖 → "요청을 처리하지 못했습니다." 차단 사유 분류 라벨: `injection_direct` → "지시 변경 시도", `pii_input` → "개인정보 포함", 그 밖 → "정책 차단". 상태 문구(`running` → "답변을 만드는 중입니다…", `failed` → "처리 중 오류가 발생했습니다. 다시 요청해 주세요.", `interrupted` → 서버가 준 `retry_hint`, `rejected` → "승인이 거부되었습니다.", `expired` → "승인 시간이 지나 종료되었습니다.").
- `testids.ts`: 화면 `data-testid` 상수 한 곳(e2e와 화면 테스트가 같은 이름을 쓴다). 이름 계약(정확히 이 이름): `loginUser(label)` → `login-user-<label>`, `loginError`=`login-error`, `sessionUser`=`session-user`, `switchUser`=`switch-user`, `navNewConversation`=`nav-new-conversation`, `navApprovals`=`nav-approvals`, `navApprovalsCount`=`nav-approvals-count`, `composerInput`=`composer-input`, `composerSubmit`=`composer-submit`, `composerCount`=`composer-count`, `thread`=`thread`, `historyUser`=`history-user`, `historyAssistant`=`history-assistant`, `turnRequest`=`turn-request`, `turnStatus`=`turn-status`, `turnAnswer`=`turn-answer`, `turnCitedDoc`=`turn-cited-doc`, `turnError`=`turn-error`, `blockedCard`=`blocked-card`, `blockedCardCategory`=`blocked-card-category`, `blockedCardMessage`=`blocked-card-message`, `quarantineNote`=`quarantine-note`, `conflictNotice`=`conflict-notice`, 그리고 다음 step이 쓰는 승인 이름 `approvalCard`=`approval-card`, `approvalTarget`=`approval-target`, `approvalBody`=`approval-body`, `approvalCheck`=`approval-check`, `approvalExpires`=`approval-expires`, `approvalApprove`=`approval-approve`, `approvalEditToggle`=`approval-edit-toggle`, `approvalEditInput(field)`=`approval-edit-input-<field>`, `approvalEditSubmit`=`approval-edit-submit`, `approvalReject`=`approval-reject`, `approvalResult`=`approval-result`, `approvalExpired`=`approval-expired`, `approvalRejectedEdit`=`approval-rejected-edit`, `approvalRequester`=`approval-requester`, `approvalOpenConversation`=`approval-open-conversation`, `inboxEmpty`=`inbox-empty`, `inboxRow`=`inbox-row`.

### 2. 훅

- `use-session.ts`: `GET /api/session`로 현재 사용자(`user_id`)를 얻는다. 401이면 `/login`으로 `router.replace`. 로딩·오류 상태를 돌려준다.
- `use-conversation.ts`: `useConversation(id, deps?)` — `deps`로 `EventSource` 팩토리와 타이머를 주입할 수 있다(테스트용, 기본은 브라우저 `EventSource`).
  - 마운트 시 `GET /api/conversations/{id}`를 한 번 읽고, 상태가 `running`·`awaiting_approval`이면 `EventSource("/api/conversations/{id}/events")`를 연다. `status` 이벤트가 오면 대화를 다시 읽는다. **종료 상태(`running`·`awaiting_approval`이 아닌 값)를 보면 `close()`한다**(서버가 스트림을 닫으면 브라우저가 자동 재연결하므로 닫지 않으면 무한 재연결한다).
  - `onerror`가 나면 `close()`하고 2초 간격 **폴링**으로 대신하며 종료 상태에서 멈춘다. 폴링은 `running` 또는 `awaiting_approval`일 때만 돈다.
  - 언마운트·`id` 변경·React StrictMode 이중 마운트에서 열린 `EventSource`와 타이머가 모두 닫힌다(열림 수 − 닫힘 수가 0).
  - **차단 카드는 클라이언트 상태에 새로고침 전까지 유지한다**: 읽은 view의 `turn.status`가 `blocked_input`이면 `{turn_id, block: {code, message}, afterMessages: history.length}`를 `blockedCards`에 넣는다(`turn_id`로 중복 제거, `block`에는 `code`·`message`만 저장하고 요청 원문은 어디에도 저장하지 않는다). 서버 history에는 차단 턴이 들어가지 않으므로 다음 턴 뒤에도 카드가 남아야 한다.
  - `submitTurn(text)`: 첫 대화는 `POST /api/conversations`, 이후는 `POST /api/conversations/{id}/turns`. 성공(202)이면 입력창을 비우고 다시 읽는다. 409·422 등 실패는 `{code}`로 돌려주고 입력 문자열은 지우지 않는다. 요청 문자열을 상태·로그·URL에 저장하지 않는다(제출 중 지역 변수뿐).

### 3. 컴포넌트·페이지

- `app-shell.tsx`(`"use client"`): 상단에 `sessionUser`(현재 사용자 라벨), 대화(`navNewConversation`)·승인함(`navApprovals`, 링크 `/approvals`, 개수 배지 `navApprovalsCount`는 다음 step이 채우므로 이 step은 자리만) 링크, `switchUser` 버튼(`DELETE /api/session` 후 `/login`).
- `login/page.tsx`: `GET /api/demo-users`로 라벨 목록을 받아 라벨을 `^demo-(.+)-g(\d+)$`로 파싱해 팀별로 묶고(`<팀> <숫자>급`) 버튼(`loginUser(label)`)을 낸다. 클릭하면 `POST /api/session` 후 `/c`로 이동. `demo_login_disabled`·`loopback_required` 등은 `loginError`에 `messages.ts` 문구로 낸다. 파싱에 실패한 라벨은 팀 `기타`로 묶어 표시한다(버리지 않는다). 사용자 목록이 비어 있으면 오류 문구를 낸다(빈 목록을 정상으로 두지 않는다).
- `(app)/layout.tsx`: `useSession`으로 보호하고 `AppShell`로 감싼다. `page.tsx`(루트)는 `/c`로 리다이렉트한다. `layout.tsx`는 `lang="ko"`와 제목 "해온클라우드 운영 에이전트"만 바꾼다(기존 폰트 설정은 유지).
- `(app)/c/page.tsx`: 새 대화 입력(`Composer`). 제출하면 `POST /api/conversations` 후 `/c/{conversation_id}`로 이동.
- `(app)/c/[id]/page.tsx`: `useConversation`으로 `Thread`와 `Composer`를 그린다(`useParams`로 id).
- `thread.tsx`: `thread` 컨테이너. 서버 `history`를 `historyUser`·`historyAssistant` 말풍선으로, **차단 카드(`blockedCards`)를 `afterMessages` 위치에** 끼워 넣고, 마지막에 현재 턴(`TurnView`)을 그린다. 현재 턴이 `blocked_input`이면 `blockedCards`와 같은 카드가 두 번 나오지 않는다(`turn_id` 기준).
- `turn-view.tsx`: `turnRequest`(요청 문자열이 있을 때만), `turnStatus`(상태 문구), `running`이면 진행 표시, `completed`면 `turnAnswer`와 근거 문서 칩(`turnCitedDoc`, 문서 **ID만**, 본문·원문 없음), `guardrail.quarantined > 0`이면 `QuarantineNote`, `failed`·`rejected`·`expired`·`interrupted`는 `messages.ts` 문구(`turnError`에는 서버 오류 코드를 보여 주지 않고 문구만), `blocked_input`이면 `BlockedCard`, `awaiting_approval`이면 `ApprovalSlot`(다음 step).
- `blocked-card.tsx`: `blockedCard`. **사유 분류 라벨**(`blockedCardCategory`)과 서버가 준 안내 문구(`blockedCardMessage`)만 보인다. 입력 원문·판정 근거는 props로도 받지 않는다(`{code, message}`만).
- `quarantine-note.tsx`: `quarantineNote`. 문구 "참고 문서 일부 격리" 배지와 설명("보안 검사에서 일부 참고 문서를 제외하고 답변했습니다."). `quarantined_at_least`가 true면 "N건 이상"으로 쓴다. 격리된 문서의 ID·본문·사유는 표시하지 않는다.
- `composer.tsx`: `composerInput` 텍스트 영역(`maxLength` 2000), `composerCount`, `composerSubmit`. 공백뿐이면 제출 비활성. 현재 턴이 `running`·`awaiting_approval`이면 입력과 제출을 비활성으로 하고 이유를 문구로 안내한다. 서버 409도 항상 처리한다(다른 탭·경쟁): 실패하면 `conflict-notice.tsx`(`conflictNotice`)에 `messages.ts` 문구를 낸다.
- 클라이언트 파일에는 FastAPI 주소·`localhost:8000`·`127.0.0.1`·`FASTAPI`·`API_BASE` 문자열을 주석 포함해 쓰지 않는다(`guards.test.ts`가 "use client" 파일에서 잡는다).

### 4. 테스트 (`@testing-library/react`, jsdom, 가짜 `fetch`·가짜 `EventSource`)

- `ui-boundary.test.ts`(소스 스캔): 화면 소스(`web/src/components/`·`web/src/hooks/`·`web/src/app/(app)/`·`web/src/app/login/`·`web/src/lib/bff.ts`·`messages.ts`·`types.ts`)에 `masked`·`output_flagged`·`guardrail_block`·`stop_reason`·`evidence_ids`·`guardrail_signals`·`guardrail_post`·`dangerouslySetInnerHTML`이 없고, 이 파일들이 `@/lib/server`·`next/headers`를 import하지 않으며, `localStorage`·`sessionStorage`·`document.cookie`를 쓰지 않는다. 스캔 대상 파일 수가 1 이상임을 먼저 단언한다(FP-069).
- `thread.test.tsx`:
  - **차단 카드 유지(보완 B)**: 입력창에 센티널 문자열을 넣어 제출 → (가짜 서버) 202 → 대화 view가 `blocked_input`(`request: null`, `block`) → `blockedCard`가 있고(존재 확인 먼저) 카드 DOM에 센티널이 없다 → 이어서 다음 턴(정상 요청)을 제출해 view가 `completed`(history에 차단 턴 없음)가 된 뒤에도 `blockedCard`가 **남아 있고**, `document.body` 전체 텍스트·속성에 센티널이 없다. 입력창은 제출 뒤 비어 있다.
  - 격리: `quarantined: 2`이면 `quarantineNote`가 있고 "참고 문서 일부 격리"를 포함, 0이면 없다. `quarantined_at_least: true`이면 "2건 이상"이다.
  - 화면이 모르는 키를 렌더하지 않음: view 픽스처에 `masked`·`stop_reason`·`rules`·`observed_doc_ids`·`request`(차단 턴)에 센티널 문자열을 담아 넣어도 DOM에 센티널이 없다.
  - 상태별 문구(`failed`·`rejected`·`expired`·`interrupted`)와 근거 문서 칩이 ID만 보인다.
- `composer.test.tsx`: 409 세 종류(`approval_pending`·`turn_in_progress`·`turn_limit`) 각각 `conflictNotice`에 정확한 문구, 실패해도 입력 문자열이 남음, 422에서 문구, 2000자 상한, 진행 중 상태에서 입력 비활성.
- `use-conversation.test.tsx`: 종료 상태 이벤트에서 `close()`가 불리고, `onerror`면 `close()` 후 폴링으로 전환해 종료 상태에서 폴링이 멈춘다(가짜 타이머), 언마운트와 StrictMode 이중 마운트에서 열린 수 − 닫힌 수가 0이다. 401 응답이면 `onUnauthenticated`가 호출된다. 각 테스트의 대기는 유한 timeout이다(FP-066).
- `bff.test.ts`: 401에서 훅 호출, 오류에 본문 원문이 없음, 상대 경로만 호출.
- `login/page.test.tsx`: 라벨이 팀별로 묶여 버튼이 나오고, 클릭하면 `POST /api/session` 후 이동, 빈 목록·`demo_login_disabled`가 오류 문구, 파싱 실패 라벨이 `기타`로 표시된다.

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

- 차단된 요청의 원문을 클라이언트 상태·DOM·URL·콘솔·테스트 출력에 남기지 마라. 이유: 차단 카드는 `block.code`·`message`만 보인다. 서버가 `request`를 비워 주는 것에 기대지 말고 화면 코드도 저장하지 않는다. 테스트는 카드가 **실제로 있음**을 먼저 확인한 뒤 센티널이 없음을 단언한다(FP-069).
- `EventSource`를 종료 상태에서 닫지 않거나 언마운트 정리를 빼지 마라. 이유: 서버는 종료 상태에서 스트림을 닫고 브라우저는 자동으로 다시 연결하므로 닫지 않으면 무한 재연결한다. 가짜 `EventSource` 테스트가 닫힘 수를 센다(FP-068, 실제 브라우저 동작은 e2e에서 확인).
- `approval-slot.tsx`를 이 step에서 구현하지 마라. 이유: 승인 카드는 다음 step의 범위다. 이 step은 `null`을 렌더하는 자리 컴포넌트와 테스트 이름 상수만 둔다.
- 화면에서 `masked`·`stop_reason`·`output_flagged`·판정 근거를 읽거나 표시하지 마라. 이유: 출력 사후 탐지·내부 예산 정보는 화면에 내지 않는다. 소스 스캔 테스트가 이름을 막는다.
- 서버 코드(`web/src/lib/server/`·`web/src/app/api/`)를 수정하거나 클라이언트에서 import하지 마라. 이유: 토큰 접근은 서버 코드에만 있다. 화면 계약이 모자라면 `blocked`로 보고한다.

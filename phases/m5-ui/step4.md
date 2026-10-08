# Step 4: web-shell

## 읽어야 할 파일

- `/AGENTS.md`(웹 계층 경계, 토큰·화면 비노출 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절), `/docs/ADR.md`(ADR-011)
- 이전 step 산출물: `/web/src/lib/server/project.ts`(화면이 받는 투영 계약: `tool_steps`·`approval_method`·`effect_at`·대화 목록 항목), `/web/src/app/api/conversations/route.ts`, `/src/govops/agent/org_info.py`(팀 한글 이름 `TEAMS`), `/src/govops/tools/registry.py`(읽기·부작용 도구 이름)
- 현재 화면 코드: `/web/src/components/app-shell.tsx`, `/web/src/components/conflict-notice.tsx`, `/web/src/hooks/use-session.ts`, `/web/src/hooks/use-approvals.ts`, `/web/src/app/(app)/layout.tsx`, `/web/src/app/layout.tsx`, `/web/src/app/globals.css`, `/web/src/app/login/page.tsx`, `/web/src/app/login/page.test.tsx`, `/web/src/lib/types.ts`, `/web/src/lib/messages.ts`, `/web/src/lib/testids.ts`, `/web/src/lib/bff.ts`, `/web/src/ui-boundary.test.ts`, `/web/src/guards.test.ts`
- shadcn 컴포넌트(이미 설치됨): `/web/src/components/ui/sheet.tsx`, `/web/src/components/ui/scroll-area.tsx`, `/web/src/components/ui/tooltip.tsx`, `/web/src/components/ui/separator.tsx`, `/web/src/components/ui/button.tsx`, `/web/src/components/ui/badge.tsx`, `/web/src/components/ui/card.tsx`, `/web/components.json`
- e2e 소비자(읽기 전용): `/web/e2e/support.ts`, `/web/e2e/approval-flow.spec.ts`, `/web/e2e/blocked-and-isolation.spec.ts`
- Next 16 문서(`/web/node_modules/next/dist/docs/01-app/` 아래): `03-api-reference/04-functions/use-router.md`, `02-guides/testing/vitest.md`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-032·060·061·066·069) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/app/layout.tsx`, `web/src/app/globals.css`, `web/src/app/login/page.tsx`, `web/src/app/login/page.test.tsx`, `web/src/app/(app)/layout.tsx`, `web/src/lib/types.ts`, `web/src/lib/types.test.ts`(신규), `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/src/lib/theme.ts`(신규), `web/src/lib/theme.test.ts`(신규), `web/src/lib/teams.ts`(신규), `web/src/lib/teams.test.ts`(신규), `web/src/lib/conversation-groups.ts`(신규), `web/src/lib/conversation-groups.test.ts`(신규), `web/src/hooks/use-theme.ts`(신규), `web/src/hooks/use-conversation-list.ts`(신규), `web/src/hooks/use-conversation-list.test.tsx`(신규), `web/src/components/app-shell.tsx`, `web/src/components/app-shell.test.tsx`(신규), `web/src/components/sidebar.tsx`(신규), `web/src/components/sidebar.test.tsx`(신규), `web/src/components/conversation-list.tsx`(신규), `web/src/components/conversation-list-context.tsx`(신규), `web/src/components/user-card.tsx`(신규), `web/src/components/theme-toggle.tsx`(신규), `web/src/components/topbar.tsx`(신규), `web/src/ui-boundary.test.ts`, `web/src/hooks/use-approvals.test.tsx`(`AppShell`을 렌더하는 기존 소비자 테스트: 새 셸이 요구하는 `next/navigation`의 `usePathname` 모킹, `ConversationListProvider` 래퍼, 필요한 브라우저 API 모킹을 **더하는 것에 한함**. 기존 단언은 지우거나 약화하지 않는다), `tests/test_web_sync.py`(신규). 모두 브라우저(클라이언트) 코드와 화면 테스트, 그리고 웹 상수와 Python 상수의 일치 검사다. 서버 코드(`web/src/lib/server/`·`web/src/app/api/`)와 Python 소스는 읽기만 한다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/src/app/layout.tsx`, `web/src/app/globals.css`, `web/src/app/login/page.tsx`, `web/src/app/login/page.test.tsx`, `web/src/app/(app)/layout.tsx`, `web/src/lib/types.ts`, `web/src/lib/types.test.ts`, `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/src/lib/theme.ts`, `web/src/lib/theme.test.ts`, `web/src/lib/teams.ts`, `web/src/lib/teams.test.ts`, `web/src/lib/conversation-groups.ts`, `web/src/lib/conversation-groups.test.ts`, `web/src/hooks/use-theme.ts`, `web/src/hooks/use-conversation-list.ts`, `web/src/hooks/use-conversation-list.test.tsx`, `web/src/components/app-shell.tsx`, `web/src/components/app-shell.test.tsx`, `web/src/components/sidebar.tsx`, `web/src/components/sidebar.test.tsx`, `web/src/components/conversation-list.tsx`, `web/src/components/conversation-list-context.tsx`, `web/src/components/user-card.tsx`, `web/src/components/theme-toggle.tsx`, `web/src/components/topbar.tsx`, `web/src/ui-boundary.test.ts`, `web/src/hooks/use-approvals.test.tsx`, `tests/test_web_sync.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 실무형 UI의 **셸**(레이아웃·사이드바·상단 바·사용자 카드·테마·로그인)과 뒤 step이 함께 쓰는 **공용 라이브러리**(타입·문구·testid)를 만든다. 참고 구성은 LibreChat 계열 화면이다: 접을 수 있는 왼쪽 사이드바 + 상단 바 + 본문 + 아래 고정 입력 카드, 좁은 화면에서는 사이드바가 서랍. 스타일은 shadcn 토큰(CSS 변수) 기반 라이트·다크이고 새 의존성을 추가하지 않는다. 아이콘은 기존 `lucide-react`다. 간격·모서리·그림자는 카드 기준으로 통일하고 포커스 링(`focus-visible`)과 `aria-*`를 둔다. 본문 영역의 메시지·입력 카드는 다음 step이 만든다.

### 1. 공용 라이브러리

- `types.ts`(추가만, 기존 타입 이름과 필드는 유지): `ToolStep = {name: string; ok: boolean}`, `TurnView`에 `tool_steps?: ToolStep[]`·`approval_method?: "approve" | "edit_approve"`·`effect_at?: string`, `ConversationListItem = {conversation_id, title, status, awaiting_approval, turn_count, updated_at}`. 기존 `conversationView()` 복사 함수가 새 세 필드를 **형식을 검사해** 복사한다(`tool_steps`는 이름과 `ok`가 맞는 항목만, 최대 20개, 그 밖의 키는 버림). `conversationListView(value)`는 허용한 여섯 키만 복사하고 형식이 틀린 항목은 버린다.
- `messages.ts`(추가만): `AGENT_DESCRIPTION = "내 권한으로 문서·티켓을 조회하고, 승인을 받은 뒤에만 발송합니다."`, `COMPOSER_NOTICE = "AI 답변은 틀릴 수 있습니다. 발송은 승인 후에만 실행됩니다."`, `GENERIC_CHECK_BLOCK = "정책 검사로 보낼 수 없습니다. 내용을 확인하고 다시 요청해 주세요."`, `toolLabel(name)`(읽기 도구 6개 `get_document`→"문서 읽기", `lookup_id`→"ID 조회", `run_sql`→"데이터 조회", `search_docs`→"문서 검색", `jira_get_ticket`→"티켓 조회", `jira_search_tickets`→"티켓 검색", 모르는 이름은 이름 그대로), `sentResultLabel(tool)`(`slack_send_message`→"Slack 메시지 ID", `jira_add_comment`→"Jira 코멘트 ID", `jira_create_followup_ticket`→"생성된 티켓 ID"), `approvalMethodLabel(method)`(`approve`→"승인", `edit_approve`→"수정 후 승인").
- `testids.ts`: 기존 이름은 **하나도 지우거나 바꾸지 않는다**(`switchUser`·`approvalResult` 포함. e2e와 기존 테스트가 쓴다. 정리는 마지막 step에서 한다). 새 이름을 추가한다: `logout`=`logout`, `sidebar`=`sidebar`, `sidebarToggle`=`sidebar-toggle`, `sidebarOpen`=`sidebar-open`, `sidebarNewChat`=`sidebar-new-chat`, `conversationItem(id)`=`conversation-item-<id>`, `conversationPending(id)`=`conversation-pending-<id>`, `conversationGroup(name)`=`conversation-group-<name>`, `conversationMore`=`conversation-more`, `topbarTitle`=`topbar-title`, `agentDescription`=`agent-description`, `themeToggle(mode)`=`theme-toggle-<mode>`, `userCard`=`user-card`, `userTeamBadge`=`user-team-badge`, `toolSteps`=`tool-steps`, `toolStepRow`=`tool-step-row`, `turnSkeleton`=`turn-skeleton`, `checkBlockedCard`=`check-blocked-card`, `composerHint`=`composer-hint`, `sentCard`=`sent-card`, `sentTarget`=`sent-target`, `sentBody`=`sent-body`, `sentMethod`=`sent-method`, `sentResultId`=`sent-result-id`, `sentAt`=`sent-at`, `inboxTable`=`inbox-table`, `inboxRequester`=`inbox-requester`, `inboxTarget`=`inbox-target`, `inboxRemaining`=`inbox-remaining`, `inboxStatus`=`inbox-status`.
- `teams.ts`: 팀 코드 → 한글 이름 표 8개(`platform`→플랫폼개발팀, `payments`→결제서비스팀, `data-ai`→데이터AI팀, `sre`→SRE인프라팀, `security`→정보보안팀, `hr`→인사팀, `finance`→재무팀, `legal`→법무컴플라이언스팀)와 표시 순서, `parseUserLabel(label)`(`^demo-(.+)-g(\d+)$` → `{team, grade}` 또는 `null`), `userDisplay(label)`(`"재무팀 · 2급"`; 파싱 실패나 모르는 팀은 라벨 원문 그대로).
- `conversation-groups.ts`: `groupConversations(items, now)` → `[{key: "오늘"|"어제"|"이전", items}]`(비어 있는 그룹은 뺀다, 사용자 로컬 날짜 기준), `formatListTime(updatedAt, now)`(오늘·어제는 `HH:mm`, 이전은 `M/D`, 파싱 실패는 빈 문자열).
- `theme.ts`: `ThemeMode = "light" | "dark" | "system"`, `readTheme()`, `writeTheme(mode)`, `applyTheme(mode)`. **웹에서 저장소를 쓰는 유일한 파일이다**: `localStorage`만, 키는 정확히 `govops-theme`, 값은 세 가지뿐(그 밖의 저장값은 `system`으로 취급). 읽기·쓰기를 각각 `try/catch`로 감싸고 실패하면 예외 없이 `system`/`false`다. 파일 어디에도 `token`·`session` 문자열을 쓰지 않는다. `applyTheme`은 `<html>`의 `dark` 클래스를 켜고 끈다(`system`은 `matchMedia("(prefers-color-scheme: dark)")` 결과를 따른다). 첫 페인트 전 인라인 스크립트와 `dangerouslySetInnerHTML`은 쓰지 않는다(명시 선택이 시스템과 다르면 첫 화면에 깜빡임이 생길 수 있고 그것이 한계다).

### 2. 훅과 컨텍스트

- `use-theme.ts`: `{mode, setMode}`. 마운트 때 `readTheme`로 읽어 적용하고, `system`이면 미디어 쿼리 변경을 구독하며 언마운트에서 구독을 해제한다.
- `use-conversation-list.ts`: `GET /api/conversations`를 마운트 때와 10초 간격으로 읽는다(`use-approvals.ts`와 같은 구조: 탭이 숨겨지면 멈추고 보이면 즉시 읽기, 중복 읽기 합치기, 언마운트 정리, 401은 기존 `bff` 훅). `{items, loading, error, refresh}`를 돌려준다.
- `conversation-list-context.tsx`: 위 훅을 한 번만 호출해 공급하는 `ConversationListProvider`와 `useConversationList()`. 뒤 step의 페이지가 제출 뒤 `refresh()`를 부른다.

### 3. 컴포넌트와 페이지

- `app-shell.tsx`(`"use client"`, 기존 `AppShell({user, children})` 시그니처 유지): 데스크톱(`md` 이상)은 왼쪽 사이드바 + 오른쪽 열(상단 바, 스크롤되는 본문 `main`). 사이드바는 접을 수 있고(`sidebarToggle`, 접으면 아이콘만 남는 좁은 레일) 접힘 상태는 **저장하지 않고 메모리로만** 둔다. 좁은 화면에서는 상단 바의 `sidebarOpen` 버튼이 `Sheet`(왼쪽 서랍)로 같은 사이드바 내용을 연다. **서랍 내용은 열렸을 때만 마운트해 testid가 한 화면에 두 번 나오지 않게 한다.** `ConversationListProvider`는 `(app)/layout.tsx`가 `AppShell`을 감싸 공급한다.
- `sidebar.tsx`: 위에서부터 제품 이름(링크 `/c`)과 접기 버튼, "새 대화" 버튼(`sidebarNewChat`, 링크 `/c`), 대화 목록(`ScrollArea`), 메뉴(대화 `/c`, 승인함 `/approvals` + 대기 건수 배지 `navApprovalsCount`, 0이면 숨김, 건수는 기존 `useApprovals(10000)`), 맨 아래 사용자 카드.
- `conversation-list.tsx`: `groupConversations`로 묶어 `conversationGroup(키)` 머리글 아래에 항목을 낸다. 항목은 링크 `/c/{id}`(`conversationItem(id)`), 제목 한 줄(말줄임)과 시각, `awaiting_approval`이면 "승인 대기" 배지(`conversationPending(id)`), 현재 대화는 `aria-current="page"`로 강조한다. 처음에는 **20개만** 보이고 20개를 넘으면 "더 보기"(`conversationMore`)가 나타나 눌렀을 때 전체(최대 50)를 보인다. 비어 있으면 안내 문구. 항목 제목 외의 본문 필드는 받지 않는다.
- `user-card.tsx`: 아바타(팀 한글 이름의 첫 글자), `sessionUser`에 `userDisplay(user)`(예: "재무팀 · 2급")를 보이고 `title` 속성과 툴팁에 원래 라벨, 팀 배지(`userTeamBadge`, 한글 팀 이름), 로그아웃 아이콘 버튼(`logout`, `aria-label="로그아웃"`): `DELETE /api/session`(서버 세션 폐기) 후 `router.replace("/login")`, 실패하면 `ConflictNotice`. 그 위에 `theme-toggle.tsx`(라이트·다크·시스템 세 버튼 `themeToggle(mode)`, `aria-pressed`).
- `topbar.tsx`: 좁은 화면의 `sidebarOpen` 버튼, 제목 `topbarTitle`(경로가 `/c/{id}`면 목록에서 찾은 그 대화의 제목, 목록에 없으면 "새 대화", `/c`는 "새 대화", `/approvals`는 "승인함"), 한 줄 설명 `agentDescription`(`AGENT_DESCRIPTION`, 좁은 화면에서는 숨김), 오른쪽 "새 대화" 버튼(`navNewConversation`, 링크 `/c`).
- `login/page.tsx`: 가운데 정렬 카드, 제품 이름, "데모 로그인 — 이 PC에서만 사용할 수 있습니다." 안내, 팀 한글 이름 머리글(`h2`, `teams.ts` 순서, 파싱 실패 라벨은 마지막 "기타" 묶음) 아래 "N급" 버튼(`loginUser(label)`, 기존 testid·동작 유지: 클릭 시 `POST /api/session` 후 `/c`), 오류는 아이콘이 붙은 경고(`loginError`, 기존 문구). 빈 사용자 목록은 오류로 취급한다(기존 동작).
- `layout.tsx`: `next/font/google`(Geist) import와 폰트 변수를 **제거**한다. `<html lang="ko">`, 메타 제목 "해온클라우드 운영 에이전트", 설명 `AGENT_DESCRIPTION`. `globals.css`: 글꼴 스택 `Pretendard, "Apple SD Gothic Neo", "Noto Sans KR", system-ui, sans-serif`(외부 글꼴 다운로드 없음), 등폭 글꼴은 시스템 스택, 사이드바·성공·경고 의미 토큰을 라이트·다크 양쪽에 추가한다(기존 토큰 값은 바꾸지 않는다).

### 4. 테스트 (`@testing-library/react`, 가짜 `fetch`·`next/navigation` 모킹)

- jsdom에 없는 `ResizeObserver`·`matchMedia` 등은 **테스트 파일 안에서** 스텁한다(설정 파일은 수정하지 않는다). 타임존에 의존하지 않도록 `now`와 날짜는 로컬 날짜 생성자(`new Date(2026, 9, 8, 14, 0)`)로 만든다(FP-032).
- `ui-boundary.test.ts`: 기존 검사(화면 소스의 금지 이름·import)를 **유지**하고 스캔 대상에 `lib/theme.ts`·`lib/teams.ts`·`lib/conversation-groups.ts`를 더한다. 저장소 금지는 **`lib/theme.ts` 한 파일만** 예외로 한다: 그 파일이 존재하고(먼저 단언), 키 문자열 `govops-theme`을 포함하며, `sessionStorage`·`document.cookie`·`token`·`session`이 없고, `localStorage` 접근이 모두 `try` 안에 있다. 그 밖의 모든 스캔 대상 파일에서는 `localStorage`·`sessionStorage`·`document.cookie`가 계속 실패해야 한다(예외 파일 밖에 `localStorage`를 심으면 실패함을 합성 사례로 확인).
- `theme.test.ts`: 저장값 검증(잘못된 값 → `system`), 읽기·쓰기가 예외를 던져도 앱이 동작, `dark` 클래스 적용, 시스템 모드의 미디어 쿼리 변경 추종과 구독 해제.
- `teams.test.ts`·`conversation-groups.test.ts`: 8개 팀 이름, `userDisplay`(정상·모르는 팀·파싱 실패), 오늘/어제/이전 묶음과 경계(자정 전후), 시각 형식, 빈 그룹 제외.
- `types.test.ts`: `conversationView`가 새 세 필드를 복사하되 형식이 틀린 `tool_steps` 항목·센티널 키(`args`·`result`)는 버리고, `conversationListView`가 여섯 키만 남긴다(통과 항목이 실제로 있음을 먼저 단언, FP-069).
- `use-conversation-list.test.tsx`: 최초 읽기와 10초 폴링(가짜 타이머), 탭 숨김 시 중지·복귀 시 읽기, 언마운트 정리(타이머 수 0), 401 훅.
- `sidebar.test.tsx`·`app-shell.test.tsx`: 목록이 오늘/어제/이전으로 묶여 보이고 20개 초과 시 "더 보기"가 전체를 보임, 승인 대기 배지, 항목 링크 `href`, 현재 항목 `aria-current`, 승인함 대기 건수 배지, 사용자 카드의 "재무팀 · 2급"과 `title`에 원래 라벨, 로그아웃이 `DELETE /api/session` 후 `/login`으로 이동(실패 시 안내), 테마 버튼 `aria-pressed`와 저장, 상단 바 제목(목록에서 찾음/없으면 새 대화/승인함), 사이드바 접기(저장 안 함), 서랍 열기(열렸을 때만 testid가 존재하고 한 번만 나타남).
- `login/page.test.tsx`: 팀 머리글이 한글 이름(`플랫폼개발팀` 등)으로 바뀐 것에 맞춰 갱신하되 기존 단언의 강도(그룹핑, 기타 묶음, 로그인 요청 본문, 오류 문구 3종)를 유지한다.
- `tests/test_web_sync.py`(Python): `web/src/lib/teams.ts`의 팀 표가 `agent/org_info.py`의 `TEAMS`(코드와 한글 이름)와 **정확히 같고**, `web/src/lib/messages.ts`의 `toolLabel`이 다루는 읽기 도구 집합이 `tool_specs()`의 읽기 도구 집합과 같으며 `sentResultLabel`이 다루는 도구 집합이 부작용 도구 집합과 같다(파일을 텍스트로 읽어 정규식으로 추출하고 추출한 항목 수가 0이 아님을 먼저 단언).

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

- `ui-boundary.test.ts`의 금지 검사를 지우거나 스캔 대상을 줄이지 마라. 저장소 예외는 `lib/theme.ts` 한 파일·키 `govops-theme`·세 값뿐이다. 접힘 상태·사용자 선택 같은 다른 값을 저장하지 마라. 이유: 화면 코드가 토큰·세션을 브라우저 저장소에 두지 못하게 하는 가드이고, 예외를 한 곳으로 좁혀 두면 가드가 계속 의미를 가진다.
- `next/font/google`·외부 CSS·외부 이미지·CDN을 쓰지 마라. 첫 페인트 전 인라인 스크립트와 `dangerouslySetInnerHTML`, 새 의존성도 쓰지 마라. 이유: 빌드와 화면이 외부 다운로드 없이 동작해야 하고 `ui-boundary`가 `dangerouslySetInnerHTML`을 막는다. 이미 설치된 shadcn 컴포넌트와 `lucide-react`만 쓴다.
- 기존 testid 이름을 삭제·변경하지 마라(`switchUser`·`approvalResult`·`approvalTarget` 등 전부 유지). 이유: 기존 컴포넌트 테스트와 e2e 스펙이 쓰고 `make verify`의 `tsc`가 e2e 파일까지 검사한다. 새 이름만 더하고 정리는 마지막 step이 한다(FP-060).
- 서버 코드·Python 소스·API 계약을 수정하지 마라. 목록 항목의 본문 필드(요청·이력·답변)를 가정하거나 표시하지 마라. 이유: 이 step은 클라이언트 셸이고 사이드바는 제목과 상태만 쓴다. 계약이 모자라면 `blocked`로 보고한다.
- 타임존·실제 시계에 의존하는 단언을 쓰지 마라. 이유: 오늘/어제 묶음은 실행 환경의 로컬 타임존에서 달라진다. CI와 로컬에서 같은 결과여야 한다(FP-032).

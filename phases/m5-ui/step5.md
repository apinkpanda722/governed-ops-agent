# Step 5: web-conversation

## 읽어야 할 파일

- `/AGENTS.md`(웹 계층 경계, 화면 비노출 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절), `/docs/THREAT_MODEL.md`(TH4·V4 행)
- 이전 step 산출물: `/web/src/lib/types.ts`(`ToolStep`·새 `TurnView` 필드), `/web/src/lib/messages.ts`(`toolLabel`·`COMPOSER_NOTICE`·`GENERIC_CHECK_BLOCK`·`errorMessage`·`statusMessage`·`blockCategory`), `/web/src/lib/testids.ts`, `/web/src/components/app-shell.tsx`, `/web/src/components/conversation-list-context.tsx`, `/web/src/components/topbar.tsx`, `/web/src/ui-boundary.test.ts`
- 현재 화면 코드: `/web/src/components/thread.tsx`, `/web/src/components/turn-view.tsx`, `/web/src/components/composer.tsx`, `/web/src/components/blocked-card.tsx`, `/web/src/components/quarantine-note.tsx`, `/web/src/components/conflict-notice.tsx`, `/web/src/components/approval-slot.tsx`, `/web/src/hooks/use-conversation.ts`, `/web/src/app/(app)/c/page.tsx`, `/web/src/app/(app)/c/[id]/page.tsx`
- 현재 테스트(정확 일치 단언 포함): `/web/src/components/thread.test.tsx`, `/web/src/components/composer.test.tsx`, `/web/src/hooks/use-conversation.test.tsx`
- shadcn 컴포넌트(이미 설치됨): `/web/src/components/ui/collapsible.tsx`, `/web/src/components/ui/skeleton.tsx`, `/web/src/components/ui/alert.tsx`, `/web/src/components/ui/badge.tsx`, `/web/src/components/ui/button.tsx`, `/web/src/components/ui/card.tsx`, `/web/src/components/ui/textarea.tsx`, `/web/src/components/ui/tooltip.tsx`
- e2e 소비자(읽기 전용): `/web/e2e/approval-flow.spec.ts`, `/web/e2e/blocked-and-isolation.spec.ts`
- `react-markdown`·`remark-gfm`의 설치된 타입과 README: `/web/node_modules/react-markdown/readme.md`, `/web/node_modules/remark-gfm/readme.md`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-032·060·061·066·068·069) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/components/markdown.tsx`(신규), `web/src/components/markdown.test.tsx`(신규), `web/src/components/tool-steps.tsx`(신규), `web/src/components/tool-steps.test.tsx`(신규), `web/src/components/turn-skeleton.tsx`(신규), `web/src/components/check-blocked-card.tsx`(신규), `web/src/components/chat-message.tsx`(신규), `web/src/components/thread.tsx`, `web/src/components/turn-view.tsx`, `web/src/components/composer.tsx`, `web/src/components/blocked-card.tsx`, `web/src/components/quarantine-note.tsx`, `web/src/components/conflict-notice.tsx`, `web/src/hooks/use-conversation.ts`, `web/src/app/(app)/c/page.tsx`, `web/src/app/(app)/c/[id]/page.tsx`, `web/src/components/thread.test.tsx`·`web/src/components/composer.test.tsx`·`web/src/hooks/use-conversation.test.tsx`(새 구조·새 동작에 따른 갱신과 추가에 한함, 단언 약화 금지), `web/src/components/app-shell.test.tsx`·`web/src/components/sidebar.test.tsx`·`web/src/components/approval-inbox.test.tsx`·`web/src/hooks/use-approvals.test.tsx`(`conflict-notice.tsx`를 쓰는 기존 소비자 테스트: `conflict-notice.tsx`의 렌더 결과 변경에 맞춘 갱신에 한함, 단언 약화·삭제 금지). 브라우저(클라이언트) 코드와 화면 테스트다. 서버 코드와 승인 관련 컴포넌트(`approval-*.tsx`)는 읽기만 한다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/src/components/markdown.tsx`, `web/src/components/markdown.test.tsx`, `web/src/components/tool-steps.tsx`, `web/src/components/tool-steps.test.tsx`, `web/src/components/turn-skeleton.tsx`, `web/src/components/check-blocked-card.tsx`, `web/src/components/chat-message.tsx`, `web/src/components/thread.tsx`, `web/src/components/turn-view.tsx`, `web/src/components/composer.tsx`, `web/src/components/blocked-card.tsx`, `web/src/components/quarantine-note.tsx`, `web/src/components/conflict-notice.tsx`, `web/src/hooks/use-conversation.ts`, `web/src/app/(app)/c/page.tsx`, `web/src/app/(app)/c/[id]/page.tsx`, `web/src/components/thread.test.tsx`, `web/src/components/composer.test.tsx`, `web/src/hooks/use-conversation.test.tsx`, `web/src/components/app-shell.test.tsx`, `web/src/components/sidebar.test.tsx`, `web/src/components/approval-inbox.test.tsx`, `web/src/hooks/use-approvals.test.tsx`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 대화 본문 영역이다. 사용자 말풍선은 오른쪽, 답변은 전체 폭 마크다운(표·목록·굵게·코드), 답변 위에 도구 단계 접힘 행, 진행 중에는 단계와 스켈레톤, 차단·오류는 아이콘이 붙은 경고 카드, 아래 고정 입력 카드. 셸(사이드바·상단 바)과 공용 타입·문구·testid는 앞 step이 만들었다. 승인 카드·발송 완료 카드는 다음 step의 범위이며 이 step은 `ApprovalSlot` 호출을 그대로 둔다. 모델 출력은 신뢰하지 않는 입력으로 다룬다(프롬프트 인젝션으로 모델이 악성 마크다운을 낼 수 있다).

### 1. 마크다운 (`markdown.tsx`)

- `Markdown({children: string})`: `react-markdown`(10.1.0) + `remark-gfm`(4.0.1)만 쓴다. **원시 HTML은 렌더링하지 않는다**(`skipHtml`, `rehype-raw` 금지). `img`·`script`·`style`·`iframe`·`object`·`embed`·`form`·`input` 요소는 허용하지 않는다(`disallowedElements`, 원격 이미지는 주소에 데이터를 실어 보내는 유출 경로가 되므로 렌더링하지 않는다).
- 링크는 `http:`·`https:`만 `<a target="_blank" rel="noopener noreferrer nofollow">`로 렌더링하고, 그 밖의 스킴(`javascript:`·`data:`·`vbscript:`·상대 경로 포함)은 링크 없이 텍스트로만 보인다(`urlTransform`과 `components.a`).
- 요소별 스타일은 `components`에 Tailwind 클래스로 준다(새 의존성·typography 플러그인 금지): 제목, 단락, 목록, 인용, 가로선, 인라인 코드(등폭, 배경), 코드 블록(가로 스크롤), 표(`overflow-x-auto` 래퍼, 머리글 강조, 줄무늬 없이 얇은 경계). 부모에 `min-w-0`을 두어 넓은 표·코드가 레이아웃을 밀지 않게 한다. `dangerouslySetInnerHTML`을 쓰지 않는다.

### 2. 메시지·턴 표시

- `chat-message.tsx`: `UserBubble`(오른쪽 정렬, `max-w-[85%]`, 둥근 배경, 줄바꿈 유지)과 `AssistantMessage`(전체 폭, `Markdown`).
- `thread.tsx`: 서버 `history`를 `UserBubble`(testid `historyUser`)과 `AssistantMessage`(testid `historyAssistant`)로, 차단 카드(`blockedCards`)를 `afterMessages` 위치에, 마지막에 현재 턴(`TurnView`)을 그린다. **기존 동작은 그대로다**: 카드 위치 규칙, 현재 턴이 `blocked_input`이면 같은 `turn_id` 카드를 두 번 그리지 않음, `thread` testid, `onChanged` 전달.
- `turn-view.tsx`(현재 턴, 아래 순서):
  1. `turn.request`가 있으면 `UserBubble`(testid `turnRequest`). 입력 차단 턴은 `request`가 없다.
  2. 상태가 `running`·`awaiting_approval`일 때만 상태 문구(`turnStatus`, `statusMessage`). **`completed`에서는 숨긴다.**
  3. `turn.tool_steps`가 비어 있지 않으면 `ToolSteps`.
  4. `running`이면 `TurnSkeleton`(`turnSkeleton`, 스켈레톤 줄 3개, `aria-busy`)과 `role="status"`·`aria-live="polite"` 진행 문구. 도구 단계는 펼친 상태로 시작해 단계가 쌓이는 것이 보인다.
  5. `completed`면 답변 `Markdown`(testid `turnAnswer`의 요소에 답변 텍스트 포함)과 근거 문서 칩(`turnCitedDoc`, **칩 텍스트는 문서 ID 그대로**).
  6. `guardrail.quarantined > 0`이면 `QuarantineNote`. 아이콘을 더하되 문구와 testid는 그대로.
  7. `failed`·`rejected`·`expired`·`interrupted`면 아이콘이 붙은 경고 카드. **`turnError` 요소의 `textContent`는 `statusMessage(...)` 문구와 정확히 같아야 한다**(아이콘은 문구가 없는 별도 요소, `aria-hidden`). 이 네 상태에서는 `turnStatus`를 따로 내지 않는다.
  8. `blocked_input`이면 `BlockedCard`(입력 차단: 사유 분류 `blockedCardCategory`와 서버 안내 `blockedCardMessage`만, 아이콘 추가, 기존 testid·텍스트 구조 유지, `hideBlock` 동작 유지). **`blocked`(결정론 검사 차단)는 `BlockedCard`가 아니라 `CheckBlockedCard`**: `checkBlockedCard` testid, 분류 배지 "발송 차단", 본문은 `turn.check?.message`, 없으면 `turn.answer`, 그것도 없으면 `GENERIC_CHECK_BLOCK`. (지금은 `blocked`가 `turn.block`이 없는 `BlockedCard`로 그려져 "정책 차단"과 빈 문구가 나온다.)
  9. `ApprovalSlot`(변경 없음).
- `tool-steps.tsx`: `ToolSteps({steps, defaultOpen})`. 접힘 행(`Collapsible`): 요약 "도구 N단계 사용"(실패가 있으면 "도구 N단계 사용 (실패 M)"), 체크·경고 아이콘, 펼침 화살표, 트리거 버튼에 `aria-expanded`. 펼치면 단계마다 `toolStepRow`(성공/실패 아이콘 + `toolLabel(name)`, 원래 이름은 `title`)를 보인다. 컨테이너 testid는 `toolSteps`. **이름과 성공 여부 외에는 props로도 받지 않는다.** 완료된 턴은 접힌 채 시작한다.
- `conflict-notice.tsx`: 경고 아이콘을 더한다. **`conflictNotice` 요소의 `textContent`는 `errorMessage(code)`와 정확히 같다**(아이콘은 별도 요소).

### 3. 입력 카드 (`composer.tsx`)

- 둥근 카드(큰 모서리, 카드 그림자) 안에 자동으로 늘어나는 텍스트 영역(최대 높이 제한 후 스크롤), 아래 줄에 글자 수(`composerCount`, `N / 2000`)·비활성 이유·전송 아이콘 버튼. 카드 아래에 안내 문구 `composerHint`(`COMPOSER_NOTICE`). `<label htmlFor>`는 화면에서 숨기는 접근성 레이블로 유지한다(`sr-only`).
- 전송 버튼(`composerSubmit`)은 아이콘 버튼이며 `aria-label="전송"`. **Enter는 전송, Shift+Enter는 줄바꿈.** 한글 입력기 조합 중의 Enter(`event.nativeEvent.isComposing` 또는 `keyCode === 229`)는 전송하지 않는다. 키 처리는 폼 제출과 같은 내부 `submit()` 함수를 직접 부른다(jsdom의 `requestSubmit`에 기대지 않는다).
- 기존 동작은 그대로다: `maxLength` 2000과 잘라 담기, 공백뿐이면 비활성, 진행 중(`running`·`awaiting_approval`)이면 입력·전송 비활성과 이유 문구(`errorMessage`), 중복 제출 방지, 실패 시 입력 유지와 `conflictNotice`, 성공 시 입력 비움.

### 4. 훅과 페이지

- `use-conversation.ts`: **`running`인 동안 2초 간격으로 대화를 다시 읽는다**(진행 중 도구 단계를 보이기 위해). 이 폴링은 **기존 단일 타이머를 공유한다**(타이머는 항상 최대 1개: SSE 오류 폴백과 `running` 폴링은 같은 타이머). `awaiting_approval`에서 **SSE가 살아 있으면**(연결이 열려 있고 `onerror`가 한 번도 호출되지 않은 상태) SSE 이벤트와 `refresh()`로만 갱신한다. SSE 오류 폴백 폴링은 `running`·`awaiting_approval` 모두에서 기존대로 2초 간격이다(같은 단일 타이머). 탭이 숨겨지면(`visibilitychange`, `document.visibilityState === "hidden"`) 타이머를 멈추고 다시 보이면 즉시 한 번 읽어 재개한다. 종료 상태에서는 즉시 모든 타이머와 스트림을 닫는다. 기존 반환 키·차단 카드 유지·StrictMode 정리·401 처리는 그대로다.
- `(app)/c/[id]/page.tsx`: 본문 열은 세로 flex. 스크롤 영역(최대 폭 약 3xl, 가운데)에 `Thread`, 아래에 `sticky bottom-0` 입력 영역(`Composer`). 새 내용이 오면 맨 아래로 스크롤하되 사용자가 위로 스크롤해 둔 동안은 끌어내리지 않는다. 대화 상태가 바뀔 때(상태 문자열 변화)와 제출 성공 뒤에 `useConversationList().refresh()`를 부른다. `onChanged`는 기존처럼 대화 `refresh`를 넘긴다.
- `(app)/c/page.tsx`: 안내 문구("무엇을 도와드릴까요?"와 `AGENT_DESCRIPTION`)와 `Composer`. 제출이 성공하면 목록 `refresh()` 뒤 `/c/{conversation_id}`로 이동한다.

### 5. 테스트

- `markdown.test.tsx`: GFM 표·목록·굵게·인라인 코드·코드 블록이 요소로 렌더링되고 표가 가로 스크롤 래퍼 안에 있다(요소가 **실제로 존재함**을 먼저 단언, FP-069). 악성 입력 — `<script>alert(1)</script>`, `<img src=x onerror=alert(1)>`, `![x](https://example.invalid/p.png?q=secret)`, `[x](javascript:alert(1))`, `[x](data:text/html,x)`, `<iframe src="https://example.invalid">`, `<a href="javascript:alert(1)">x</a>`, `<style>`, 상대 링크 `[x](/c/1)` — 을 넣었을 때 결과 DOM에 `script`·`img`·`iframe`·`style`·`object`·`form`·`input` 요소가 없고, `on`으로 시작하는 속성과 `style` 속성이 없으며, 모든 `<a>`가 `http(s)` 주소와 `rel`에 `noopener`를 가지고, 위험 스킴·상대 경로 링크는 `<a>`가 아니라 텍스트로만 남는다.
- `tool-steps.test.tsx`: 접힌 상태 요약(성공·실패 개수), 펼치면 단계 행과 한국어 라벨(모르는 이름은 그대로), `aria-expanded`, `defaultOpen`, **센티널 인자·결과를 담은 객체를 넘겨도 DOM에 없다**(허용 필드만 렌더링).
- `thread.test.tsx`(갱신): 기존 단언을 유지한다 — 차단 카드 유지(센티널 부재 포함), 격리 문구, 알 수 없는 키·차단 요청 비노출, 상태별 `turnError` 정확한 문구 4종, 인용 칩 텍스트, 카드 위치 순서, 승인 슬롯 전달. 새로 추가한다: `completed`에서 `turnStatus`가 없고 `running`에서 있음, `running`에서 `turnSkeleton`·펼쳐진 도구 단계, `blocked`가 `checkBlockedCard`로 그려지며 문구가 `check.message` → `answer` → 일반 문구 순으로 나오고 "정책 차단"·빈 문구가 아님(분류가 입력 차단 카드와 구분됨), 답변이 마크다운으로 렌더링됨, 이력의 assistant 메시지도 마크다운, 도구 단계의 센티널 비노출.
- `composer.test.tsx`(갱신 + 추가): 기존 단언 유지(409 세 종류와 `invalid_request` 문구·입력 유지, 길이·공백·성공 시 비움, 진행 중 비활성). 추가: Enter 한 번에 제출 1회, Shift+Enter는 제출 없음, 한글 조합 중 Enter(`isComposing` 참, `keyCode` 229) 제출 없음, 진행 중 Enter 무시, `composerHint` 문구, 전송 버튼 `aria-label`.
- `use-conversation.test.tsx`(갱신 + 추가): **기존 테스트를 그대로 통과**해야 한다(특히 SSE 오류 시 2초 폴링과 타이머 수, 언마운트 시 타이머 0, 폴링 중 타이머 수 1). 추가: SSE가 살아 있어도 `running`이면 2초마다 읽고 종료 상태에서 멈춤, `awaiting_approval`에서 SSE가 살아 있으면(연결이 열려 있고 `onerror`가 한 번도 호출되지 않은 상태) 타이머 수가 0이고 시간이 지나도 읽지 않음(SSE 오류 뒤 `awaiting_approval`의 2초 폴백은 기존 `stream error closes and polls every two seconds until terminal`이 이미 덮으므로 중복 추가하지 않는다), 탭이 숨겨지면 타이머 0이고 보이면 즉시 읽기, 폴링과 SSE 이벤트가 겹쳐도 동시에 한 번만 읽기. 가짜 타이머와 유한 timeout만 쓴다(FP-066).

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

- 마크다운에서 원시 HTML·원격 이미지·위험 스킴 링크를 렌더링하지 마라. `rehype-raw`·`dangerouslySetInnerHTML`·`react-markdown`/`remark-gfm` 밖의 새 의존성을 쓰지 마라. 이유: 모델 출력은 신뢰하지 않는 입력이다. 원격 이미지는 주소에 데이터를 실어 보내는 유출 경로이고 위험 스킴 링크는 실행 경로다. 악성 입력 테스트가 막는다.
- 기존 testid가 달린 요소의 텍스트 구조를 바꾸지 마라(`turnError`·`conflictNotice`·`turnCitedDoc`·`blockedCardCategory`·`blockedCardMessage`의 `textContent`는 문구 또는 ID와 정확히 같고 아이콘은 별도 요소다). 기존 테스트 단언을 약화하지 마라. 이유: 기존 테스트와 e2e가 그 텍스트를 정확히 비교한다(FP-060·061).
- 도구 단계에 이름과 성공 여부 외의 정보(인자·결과·문서 ID·본문)를 표시하거나 props로 받지 마라. `masked`·`stop_reason`·판정 근거·입력 원문도 표시하지 마라. 이유: 요청자 본인 화면에서도 도구 인자·결과는 노출 대상이 아니고 `ui-boundary` 스캔이 금지 이름을 막는다.
- 별도 폴링 타이머를 만들어 기존 타이머와 병행하지 마라. `use-conversation.test.tsx`의 기존 타이머 수 단언을 바꾸지 마라. 이유: 타이머는 항상 최대 1개여야 정리가 새지 않고(StrictMode·언마운트), 기존 테스트가 그 수를 단언한다. 탭이 숨겨졌을 때와 종료 상태에서 즉시 멈춘다.
- `approval-slot.tsx`·`approval-card.tsx`·`approval-inbox.tsx`와 서버 코드를 수정하지 마라. 이유: 승인 카드·발송 완료 카드·승인함은 다음 step의 범위이고(한 step에 한 계층·한 영역), 이 step의 `thread.test.tsx` 갱신은 `ApprovalSlot` 호출이 그대로임을 확인하는 데 그친다.

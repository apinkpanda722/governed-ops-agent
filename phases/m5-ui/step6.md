# Step 6: web-approvals

## 읽어야 할 파일

- `/AGENTS.md`(승인·신원 CRITICAL 규칙: 승인은 초안 해시에 묶임, 승인자는 본문만 수정, 수신처는 못 바꿈), `/docs/ADR.md`(ADR-011·ADR-018), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절)
- 이전 step 산출물: `/web/src/lib/types.ts`(`TurnView`의 `approval_method`·`effect_at`·`effect`·`draft`), `/web/src/lib/messages.ts`(`sentResultLabel`·`approvalMethodLabel`·`errorMessage`), `/web/src/lib/teams.ts`(`userDisplay`), `/web/src/lib/testids.ts`(승인·발송 완료·승인함 이름), `/web/src/components/turn-view.tsx`, `/web/src/components/thread.tsx`, `/web/src/components/chat-message.tsx`, `/web/src/components/sidebar.tsx`, `/web/src/ui-boundary.test.ts`
- 현재 승인 화면: `/web/src/components/approval-card.tsx`, `/web/src/components/approval-slot.tsx`, `/web/src/components/approval-inbox.tsx`, `/web/src/hooks/use-approvals.ts`, `/web/src/app/(app)/approvals/page.tsx`
- 현재 테스트(정확 일치 단언 포함): `/web/src/components/approval-card.test.tsx`, `/web/src/components/approval-inbox.test.tsx`, `/web/src/components/thread.test.tsx`, `/web/src/hooks/use-approvals.test.tsx`
- 수정 가능한 필드 규칙: `/src/govops/agent/approval.py`(`EDITABLE_FIELDS`), `/src/govops/agent/draft.py`(`_FIELDS`)
- shadcn 컴포넌트(이미 설치됨): `/web/src/components/ui/table.tsx`, `/web/src/components/ui/collapsible.tsx`, `/web/src/components/ui/badge.tsx`, `/web/src/components/ui/button.tsx`, `/web/src/components/ui/card.tsx`, `/web/src/components/ui/textarea.tsx`
- e2e 소비자(읽기 전용): `/web/e2e/approval-flow.spec.ts`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-003·060·061·069) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/lib/time.ts`(신규), `web/src/lib/time.test.ts`(신규), `web/src/components/sent-card.tsx`(신규), `web/src/components/sent-card.test.tsx`(신규), `web/src/components/approval-card.tsx`, `web/src/components/approval-slot.tsx`, `web/src/components/approval-inbox.tsx`, `web/src/app/(app)/approvals/page.tsx`, `web/src/components/approval-card.test.tsx`·`web/src/components/approval-inbox.test.tsx`·`web/src/components/thread.test.tsx`(새 구조에 따른 갱신과 추가에 한함, 단언 약화 금지). 브라우저(클라이언트) 코드와 화면 테스트다. 서버 코드와 앞 step의 대화 컴포넌트는 읽기만 한다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/src/lib/time.ts`, `web/src/lib/time.test.ts`, `web/src/components/sent-card.tsx`, `web/src/components/sent-card.test.tsx`, `web/src/components/approval-card.tsx`, `web/src/components/approval-slot.tsx`, `web/src/components/approval-inbox.tsx`, `web/src/app/(app)/approvals/page.tsx`, `web/src/components/approval-card.test.tsx`, `web/src/components/approval-inbox.test.tsx`, `web/src/components/thread.test.tsx`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 승인 카드는 구조화 필드(수신처, 본문, 검사 결과 배지, 남은 시간)와 버튼 위계(승인=주, 수정 후 승인=보조, 거부=위험)로, 실행 뒤에는 **발송 완료 카드**(수신처, 최종 본문, 처리 방식, 라벨 붙은 결과 ID, 시각)로 바뀐다. 승인함은 표(요청자·수신처·남은 시간·상태 배지)이고 행을 열면 상세 승인 카드가 나온다. 승인 규칙은 그대로다: 요청은 항상 서버가 마지막으로 준 `draft_sha256`만 보내고, 승인자는 본문만 수정하며 수신처 입력은 어디에도 없다. 서버가 허용한 행과 본인 대화의 대기 턴에만 카드를 그린다.

### 1. 시간 유틸 (`time.ts`)

- `formatRemaining(seconds: number): string`: `"N분 M초"`(분과 초를 항상 둘 다 쓴다. 예: 2초 → `"0분 2초"`, 1721초 → `"28분 41초"`, 음수는 0). 기존 테스트가 `toContain("2초")`·`toContain("1초")`를 단언하므로 이 형식을 지킨다.
- `formatClock(iso: string, now?: Date): string`: 오늘이면 `HH:mm`, 아니면 `M월 D일 HH:mm`(로컬 시간대), 파싱 실패는 빈 문자열.

### 2. 승인 카드 (`approval-card.tsx`)

기존 동작은 그대로다(중복 제출 방지, 202·200·오류 처리와 문구, `rejected_edit` 처리와 최신 해시, 만료 카운트다운과 알림 1회, 수정 가능한 필드, `onChanged`). 바뀌는 것은 표시 구조뿐이다.

- 카드 머리: 제목 "승인 요청"과 검사 배지. 배지는 `check?.status === "passed"`이면 "검사 통과", 그 밖이면 "확인 필요"(**상태 문자열은 `passed`와만 비교한다.** `ui-boundary` 스캔이 막는 단어를 쓰지 않는다). 남은 시간(`approvalExpires`)은 시계 아이콘과 함께 `"남은 시간 " + formatRemaining(remaining)`.
- 본문 영역: 수신처(`approvalTarget`, 요소의 텍스트는 채널 또는 티켓 ID **그 값만**), 본문(`approvalBody`, 요소의 텍스트는 본문 값들만, 필드별 문단), 검사 결과(`approvalCheck`, 요소의 텍스트는 서버 `check.message` 그대로). 항목 이름("수신처"·"본문"·"검사 결과")은 값 요소 **밖**의 레이블로 둔다. 요청자(`approvalRequester`)는 `userDisplay(label)`로 보이고 파싱할 수 없는 라벨은 원문 그대로, `title`에 원래 라벨. 대화 링크(`approvalOpenConversation`, 문구 "대화 열기")는 기존대로.
- 알림 문구(`approvalRejectedEdit`·`approvalExpired`·`approvalResult`)는 경고 아이콘을 더하되 **요소의 텍스트는 기존 문구와 정확히 같다**(아이콘은 문구 없는 별도 요소).
- 버튼 위계: 승인 = 주(`variant` 기본), 수정 후 승인 = 보조(`outline`/`secondary`), 거부 = 위험(`destructive`). 각 버튼에 비시각 표식 `data-hierarchy="primary"`·`"secondary"`·`"danger"`를 달아 테스트가 위계를 확인한다. 버튼 순서는 거부, 수정 후 승인, 승인(오른쪽 정렬). **카드 안에 이 세 버튼 외의 `button`·`textbox`를 더하지 않는다**(기존 테스트가 모든 버튼의 비활성과 텍스트 영역 목록을 순회한다). 수정 입력의 레이블은 "본문"·"제목"·"설명"이고 testid(`approvalEditInput(field)`)는 그대로다.

### 3. 발송 완료 카드 (`sent-card.tsx`)와 슬롯

- `SentCard({turn})`: `turn.status === "completed"`이고 `turn.effect`가 있을 때 `ApprovalSlot`이 그린다(`approval-slot.tsx`: 대기 중이면 `ApprovalCard`, 그 조건이면 `SentCard`, 나머지는 `null`). `testids.sentCard`.
- 제목(성공 아이콘 + 문구): `slack_send_message`는 "발송 완료", `jira_add_comment`는 "코멘트 등록 완료", `jira_create_followup_ticket`은 "티켓 생성 완료", 도구를 모르면 "처리 완료". 행(라벨 붙은 글머리, 값이 없는 행은 **생략**한다):
  - 수신처: `sentTarget` = 채널·티켓 ID 값만(`turn.draft.args`)
  - 처리 방식: `sentMethod` = `approvalMethodLabel(turn.approval_method)`
  - 결과 ID: `sentResultId` = `"<sentResultLabel(tool)>: <effect.mock_id>"`(예: `Slack 메시지 ID: message-4`)
  - 시각: `sentAt` = `formatClock(turn.effect_at)`
  - 본문: `sentBody` = **최종 발송된 본문**(`turn.draft.args`의 본문 필드들, 수정 후 승인이면 수정본). 본문이 길면(200자 초과 또는 6줄 초과) 처음에는 6줄로 접어 보이고 "펼치기"/"접기" 버튼(`aria-expanded`)으로 전환한다. **DOM에는 전체 텍스트가 항상 있다**(접힘은 줄 수 제한 스타일로 한다). `draft`가 없으면 수신처·본문 행을 생략하고 결과 ID만 보인다.
- 표시하지 않는 것: `effect.reason`, 검사 사유 코드, 내부 상태. 다른 사용자의 뷰에는 이 카드가 없다(요청자 본인 턴에서만 그려진다).

### 4. 승인함 (`approval-inbox.tsx`)

- 표(`inboxTable`, shadcn `Table`): 열은 요청자, 수신처, 남은 시간, 상태. 항목마다 `<tbody data-testid={inboxRow}>` 하나(요약 행 + 펼쳤을 때 상세 행). 요약 행: 요청자 칸은 행을 펼치는 `button`(testid `inboxRequester`, `aria-expanded`, 텍스트 `userDisplay(requester_label)`, 파싱 불가 라벨은 원문), 수신처 `inboxTarget`(값만), 남은 시간 `inboxRemaining`(`formatRemaining`, 1초마다 갱신하는 공용 타이머 하나), 상태 배지 `inboxStatus`("승인 대기", 남은 시간이 0이면 "만료"). 처음에는 모두 접혀 있고 요청자 버튼을 누르면 상세 행에 `ApprovalCard`가 나온다(다시 누르면 접힘). 결정 뒤에는 목록을 다시 읽는다(기존 `onChanged={refresh}`).
- 비어 있으면 `inboxEmpty`("대기 중인 승인 요청이 없습니다.", 기존 문구)이고 `inboxRow`는 없다. 로딩·오류 표시는 기존과 같다. 대화 이력·요청 원문·답변은 행 데이터로도 받지 않는다.
- `(app)/approvals/page.tsx`: 화면 제목은 시각적으로 숨겨진 `h1`("승인함")과 안내 문구, `ApprovalInbox`.

### 5. 테스트

- `time.test.ts`: `formatRemaining` 경계(0·1·59·60·1721·음수·비정수), `formatClock` 오늘/다른 날/파싱 실패(고정 `now`와 로컬 날짜 생성자를 쓰고 타임존에 의존하지 않는다, FP-032).
- `approval-card.test.tsx`(갱신 + 추가): **기존 단언을 모두 유지한다**(수신처·본문·검사 문구 정확 일치, 요청 본문, 수정 가능한 필드 3종과 `getAllByRole("textbox")` 순서, 더블 클릭 1회와 전체 버튼 비활성, 오류 4종 문구, 수정본 거부 뒤 새 해시로 결정, 카운트다운과 만료, 빈 필드 제외). 추가: 배지가 "검사 통과"(`passed`)와 "확인 필요"(그 밖), 남은 시간 `"N분 M초"` 형식, 세 버튼의 `data-hierarchy`와 순서, 요청자 표시(파싱 가능/불가).
- `sent-card.test.tsx`: 도구 3종의 제목과 결과 ID 라벨(`Slack 메시지 ID: …`·`Jira 코멘트 ID: …`·`생성된 티켓 ID: …`), 처리 방식(승인/수정 후 승인), 시각, 최종 본문(수정 후 승인이면 수정본), 긴 본문 접기·펼치기(`aria-expanded`, DOM에 전체 텍스트 유지), 값이 없는 행 생략, `draft`가 없어도 결과 ID만 렌더링, **`effect.reason`·센티널 키가 DOM에 없다**(렌더링된 행이 실제로 존재함을 먼저 단언, FP-069).
- `approval-inbox.test.tsx`(재작성, 같은 강도): 빈 목록(`inboxRow` 0개와 `inboxEmpty`), 두 행이 서버 순서 그대로 렌더링, 요약 칸(요청자 표시·수신처·남은 시간·상태), 요청자 버튼을 눌러야 상세 카드가 나타나고 상세의 본문·검사·수신처가 행별로 맞음(모든 행을 펼쳐 확인), 대화 링크는 `conversation_id`가 있는 행에만 `href="/c/<id>"`, 행 데이터에 `request`·`answer`·`history` 센티널을 섞어도 DOM에 없음(허용 필드는 **실제로 보임**을 먼저 확인), 결정 뒤 목록 재조회 1회 추가.
- `thread.test.tsx`(갱신): 승인 슬롯 사례는 완료 뒤 `sent-card`가 `syn-effect`를 포함하고 `approval-card`가 없음을 단언하도록 고친다(강도 유지). `expired`의 `turn-error` 문구 등 나머지는 그대로다.

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

- `approvalTarget`·`approvalBody`·`approvalCheck`·`approvalRejectedEdit`·`approvalExpired`·`approvalResult` 요소의 텍스트에 레이블·아이콘 문구·장식을 섞지 마라(값 또는 기존 문구 그대로, 장식은 별도 요소). 기존 단언을 약화하지 마라. 이유: 기존 테스트가 이 텍스트를 정확히 비교하고 e2e가 일부 문구를 쓴다(FP-060·061).
- `ApprovalCard` 안에 승인·수정 후 승인·거부 외의 `button`이나 `textbox`를 더하지 마라(접기 버튼은 `SentCard`에만 둔다). 수신처 입력을 만들지 마라. 이유: 기존 테스트가 카드 안의 모든 버튼과 텍스트 영역을 순회하고, 승인자는 본문만 수정할 수 있다(AGENTS.md CRITICAL).
- 이전에 보낸 `draft_sha256`을 캐시해 재사용하지 마라. 이유: 수정본이 검사에 막혀 대기로 돌아오면 서버의 현재 해시가 원래 초안의 해시다. 항상 최신 view의 해시만 보낸다(FP-003).
- `SentCard`에 `effect.reason`·검사 사유 코드·`tool_steps`·내부 상태·입력 원문을 표시하지 마라. 검사 상태 문자열은 `passed`와만 비교하라. 이유: 판정 근거는 노출 대상이 아니고 `ui-boundary` 스캔이 금지 이름을 막는다.
- 서버 코드·Python 소스·앞 step의 대화 컴포넌트(`thread.tsx`·`turn-view.tsx`·`composer.tsx` 등)를 수정하지 마라. 이유: 이 step은 승인 영역만 맡고, 슬롯 연결(`ApprovalSlot`의 호출 위치)은 이미 `TurnView`에 있다. 모자라면 `blocked`로 보고한다.

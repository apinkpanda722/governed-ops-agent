# Step 4: ui-approvals

## 읽어야 할 파일

- `/AGENTS.md`(승인·신원 CRITICAL 규칙: 승인은 초안 해시에 묶임, 승인자는 본문만 수정, 수신처는 못 바꿈), `/docs/ADR.md`(ADR-011·ADR-018), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절)
- `/src/govops/agent/approval.py`(`EDITABLE_FIELDS`·`apply_edit`: 수정 가능한 필드와 제한), `/src/govops/agent/draft.py`(`_FIELDS`), `/src/govops/api/tasks.py`(`_approve`의 상태 코드 200/202/403/404/409/422, `rejected_edit`·`effect` 의미)
- 이전 step 산출물: `/web/src/lib/types.ts`, `/web/src/lib/bff.ts`, `/web/src/lib/messages.ts`, `/web/src/lib/testids.ts`(승인 이름 상수), `/web/src/hooks/use-conversation.ts`, `/web/src/components/approval-slot.tsx`(자리 컴포넌트), `/web/src/components/turn-view.tsx`, `/web/src/components/thread.tsx`, `/web/src/components/app-shell.tsx`, `/web/src/components/thread.test.tsx`, `/web/src/lib/server/project.ts`(승인 행 투영), `/web/src/app/api/tasks/[id]/approval/route.ts`
- `/web/src/ui-boundary.test.ts`, `/web/src/guards.test.ts`
- Next 16 문서(`/web/node_modules/next/dist/docs/01-app/` 아래): `02-guides/forms.md`, `02-guides/testing/vitest.md`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-003·060·069) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/components/approval-card.tsx`(신규), `web/src/components/approval-inbox.tsx`(신규), `web/src/components/approval-slot.tsx`, `web/src/components/turn-view.tsx`, `web/src/components/app-shell.tsx`, `web/src/hooks/use-approvals.ts`(신규), `web/src/lib/messages.ts`, `web/src/app/(app)/approvals/page.tsx`(신규), `web/src/components/approval-card.test.tsx`(신규), `web/src/components/approval-inbox.test.tsx`(신규), `web/src/hooks/use-approvals.test.tsx`(신규), `web/src/components/thread.test.tsx`(`ApprovalSlot` 연결에 따른 갱신에 한함, 단언 약화 금지). 브라우저(클라이언트) 코드와 화면 테스트다. 서버 코드는 읽기만 한다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/src/components/approval-card.tsx`, `web/src/components/approval-inbox.tsx`, `web/src/components/approval-slot.tsx`, `web/src/components/turn-view.tsx`, `web/src/components/app-shell.tsx`, `web/src/hooks/use-approvals.ts`, `web/src/lib/messages.ts`, `web/src/app/(app)/approvals/page.tsx`, `web/src/components/approval-card.test.tsx`, `web/src/components/approval-inbox.test.tsx`, `web/src/hooks/use-approvals.test.tsx`, `web/src/components/thread.test.tsx`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 대화 안 승인 카드와 승인함(요청자 본인의 대기 승인, 전사 공지의 자격 있는 승인자의 대기 승인)이다. 승인은 **초안 내용 해시에 묶인다**: 요청은 항상 서버가 마지막으로 준 `draft_sha256`을 보낸다. 승인자는 **본문만** 수정할 수 있고 수신처는 바꿀 수 없다. 서버가 어느 승인자를 허용할지 정하므로(`GET /approvals`에 나온 행만 보인다) 화면은 허용 여부를 추측하지 않는다.

### 1. `approval-card.tsx` — `ApprovalCard`

props: `{view, onChanged}`. `view`는 `{turn_id, status, draft, draft_sha256, check, expires_at, rejected_edit?, effect?, requester_label?, conversation_id?}`(대화 안 카드는 현재 턴 view에서, 승인함은 승인 행에서 같은 모양으로 만든다).

- 표시: 수신처(`approvalTarget`: `draft.args`의 `channel`·`ticket_id`·`parent_ticket_id` 중 하나, **읽기 전용 텍스트**), 본문(`approvalBody`: `slack_send_message`는 `text`, `jira_add_comment`는 `body`, `jira_create_followup_ticket`은 `title`·`description`), 결정론 검사 결과(`approvalCheck`: 서버가 준 `check.message`를 **그대로** 보인다. 개인정보를 가린 경우의 "개인정보를 가렸습니다" 안내도 서버 문구이며, 화면은 `check.status` 문자열을 비교하지 않는다 — 화면 소스에는 `masked` 문자열이 들어갈 수 없다는 `ui-boundary.test.ts` 가드가 있다), 만료까지 남은 시간(`approvalExpires`, 초 단위로 갱신하는 카운트다운). 승인함 행이면 요청자(`approvalRequester`)와 대화 링크(`approvalOpenConversation`, `conversation_id`가 있을 때만 `/c/{id}`).
- 버튼: 승인(`approvalApprove`) `{decision:"approve", draft_sha256}`, 거부(`approvalReject`) `{decision:"reject", draft_sha256}`, 수정 후 승인: `approvalEditToggle`로 편집 모드를 열면 **수정 가능한 필드**(`slack_send_message`: `text`, `jira_add_comment`: `body`, `jira_create_followup_ticket`: `title`·`description`)마다 입력(`approvalEditInput(field)`)이 나오고 `approvalEditSubmit`이 `{decision:"edit_approve", draft_sha256, edited:{<바뀐 필드만>}}`를 보낸다. 값이 비었거나 원래와 같으면 그 필드는 `edited`에 넣지 않고, 바뀐 필드가 없으면 수정 제출을 비활성으로 한다. **수신처 입력은 어디에도 없다.** 요청은 `POST /api/tasks/{turn_id}/approval`.
- **중복 제출 방지**: 한 번 누르면 응답이 올 때까지 모든 버튼을 비활성으로 하고 요청은 한 번만 간다.
- 응답 처리: `202`는 "발송을 처리하는 중입니다…"(`approvalResult`)로 바꾸고 `onChanged()`로 view를 다시 읽는다(실행은 비동기이며 진행은 대화 훅의 SSE·폴링이 갱신한다). `200`은 서버 view의 `status`·`effect`로 결과를 낸다(`completed` → "처리했습니다"와 `effect.mock_id`, `rejected` → "거부했습니다"). 실패는 `messages.ts`의 승인 오류 문구: `approval_forbidden` → "이 요청을 승인할 권한이 없습니다.", `task_not_found` → "승인 요청이 만료되었거나 이미 처리되었습니다.", `approval_conflict` → "초안이 바뀌었거나 이미 처리 중입니다. 화면을 새로 고쳐 주세요.", `invalid_edit` → "수정한 내용을 확인해 주세요. 비워 둘 수 없고 길이 제한이 있습니다.". 실패 뒤에도 `onChanged()`로 다시 읽는다.
- **수정본이 결정론 검사에 막혀 대기로 돌아온 경우(FP-003)**: view에 `rejected_edit`가 있으면 `approvalRejectedEdit`에 그 `message`를 내고("수정한 내용이 검사를 통과하지 못했습니다: …"), 카드는 **원래 초안과 서버가 준 현재 `draft_sha256`**로 다시 결정할 수 있어야 한다. 이전에 보냈던 해시를 재사용하지 말고 항상 최신 view의 `draft_sha256`을 보낸다.
- 만료: 남은 시간이 0이 되면 `approvalExpired` 문구("승인 시간이 지났습니다")를 내고 모든 버튼을 비활성으로 한 뒤 `onChanged()`로 다시 읽는다. 서버가 `expired`로 바꾼 view는 카드를 지우고 상태 문구만 보인다.
- 승인자(요청자가 아닌 사람) 행에는 대화 이력·요청 원문·답변을 표시하지 않는다(props로도 받지 않는다).

### 2. `approval-slot.tsx`·`turn-view.tsx`

`ApprovalSlot`을 구현한다: 현재 턴이 `awaiting_approval`이고 `draft`와 `draft_sha256`이 있을 때 `ApprovalCard`를 그린다. 턴이 `completed`이고 `effect`가 있으면 결과(`approvalResult`: "처리했습니다"와 `effect.mock_id`)를 낸다. `turn-view.tsx`는 슬롯에 필요한 props를 넘기고 슬롯의 `onChanged`로 대화를 다시 읽는다.

### 3. 승인함

- `use-approvals.ts`: `GET /api/approvals`를 읽고 **5초 간격 폴링**(탭이 보이지 않는 동안 쉬고, 언마운트에서 타이머 정리). `refresh()`를 노출한다. 401이면 로그인으로 보낸다(기존 `bff.ts` 훅).
- `approval-inbox.tsx`·`(app)/approvals/page.tsx`: 행마다 `inboxRow`와 `ApprovalCard`. 비어 있으면 `inboxEmpty`("대기 중인 승인 요청이 없습니다."). 결정 뒤 목록을 다시 읽는다.
- `app-shell.tsx`: 승인함 링크에 대기 개수 배지(`navApprovalsCount`, 0이면 숨김). 개수는 `use-approvals`의 값(10초 간격)이다.

### 4. 테스트 (`@testing-library/react`, 가짜 `fetch`)

- 요청 본문: 승인/거부/수정 후 승인이 각각 정확히 `{decision, draft_sha256}`·`{decision, draft_sha256, edited}`로 가며 `edited`에는 **바뀐 본문 필드만** 있다. 수신처 입력이 DOM에 없다. 수정 가능한 필드 목록은 도구별로 확인한다(3개 도구).
- 더블 클릭해도 요청 1건(가짜 fetch 호출 수 1), 응답 전에는 버튼 비활성.
- `202`는 진행 문구와 재조회, `200 completed`는 `effect.mock_id` 표시, 404·403·409·422는 정확한 문구.
- `rejected_edit`가 있는 view는 안내 문구를 내고, 이어서 **새 `draft_sha256`을 가진 view**로 바뀌면 다음 결정이 그 새 해시를 보낸다(FP-003).
- 만료: 가짜 타이머로 `expires_at`을 지나면 버튼이 비활성이고 `approvalExpired`가 보인다. 남은 시간 표시가 줄어든다.
- 승인함: 비어 있을 때 `inboxEmpty`(행이 없음을 확인), 두 행이 `expires_at` 순서 그대로 렌더, `conversation_id`가 있는 행에만 대화 링크, 승인자 행의 픽스처에 `request`·`answer`·`history`에 센티널 문자열을 담아도 DOM에 센티널이 없다(허용 필드는 **실제로 보임**을 먼저 확인, FP-069).
- `use-approvals`: 폴링 간격, 탭 숨김 시 중지, 언마운트 정리(가짜 타이머).
- 기존 `ui-boundary.test.ts`(금지 키 이름·import 스캔)와 `guards.test.ts`가 새 파일에도 통과한다.

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

- 이전에 보낸 `draft_sha256`을 캐시해 재사용하지 마라. 이유: 승인은 초안 해시에 묶인다. 수정본이 검사에 막혀 대기로 돌아오면 서버의 현재 해시가 원래 초안의 해시다. 화면은 항상 최신 view의 해시만 보낸다(FP-003).
- 수신처 입력을 만들거나 `edited`에 수신처 필드를 넣지 마라. 이유: 승인자는 본문만 수정할 수 있고 수신처는 거부 후 새 요청으로만 바꾼다(AGENTS.md CRITICAL).
- 서버가 허용하지 않은 사용자에게 승인 버튼을 "추측해서" 보이지 마라. 이유: 승인 가능 여부는 서버(`GET /approvals`, 승인자 정책)가 정한다. 승인함에 나온 행과 본인 대화의 대기 턴에만 카드를 그린다.
- 승인 결과 문구에 `effect.reason`·검사 `reason` 코드·내부 상태를 그대로 쓰지 마라. 이유: 화면에는 `check.message`와 정해진 문구만 낸다. 판정 근거는 노출 대상이 아니다.
- 승인함이 비어 있을 때 통과하는 단언만 쓰지 마라. 이유: 행이 있는 경우와 없는 경우를 따로 확인하지 않으면 렌더 실패도 "비어 있음"으로 통과한다(FP-069).

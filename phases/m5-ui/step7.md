# Step 7: e2e-docs

## 읽어야 할 파일

- `/AGENTS.md`(기술 스택의 테스트 줄, 명령어 절, 고정 제약 CRITICAL 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절과 상태 관리 절), `/docs/ADR.md`(ADR-011·ADR-021·ADR-023의 형식), `/docs/THREAT_MODEL.md`(TH4·V4 행과 표 구조), `/docs/EVAL.md`(프롬프트 해시 항목), `/docs/PRD.md`(verify·e2e 문단)
- 이전 step 산출물: `/web/src/lib/testids.ts`, `/web/src/components/`(사이드바·사용자 카드·도구 단계·발송 완료 카드·발송 차단 카드), `/web/src/lib/messages.ts`, `/src/govops/agent/requester.py`, `/src/govops/agent/graph.py`(`writes_team`), `/docs/results/hypotheses-m5-prompt.md`(읽기 전용)
- e2e: `/web/e2e/support.ts`, `/web/e2e/approval-flow.spec.ts`, `/web/e2e/blocked-and-isolation.spec.ts`, `/web/playwright.config.ts`, `/web/src/e2e-config.test.ts`, `/tests/e2e_backend.py`, `/tests/test_e2e_backend.py`
- 스모크: `/src/govops/api/smoke_conversation.py`, `/tests/test_api_smoke_conversation.py`
- `/Makefile`(`e2e-web` 타깃), `/tests/test_makefile.py`, `/tests/test_docs_consistency.py`, `/tests/test_repo_rules.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-060·061·066·067·070) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/e2e/support.ts`, `web/e2e/approval-flow.spec.ts`, `web/e2e/blocked-and-isolation.spec.ts`, `web/src/lib/testids.ts`, `web/src/components/user-card.tsx`(구 "사용자 전환" 버튼(`switchUser`) 제거에 한함. 로그아웃 아이콘 버튼 `logout`·팀 배지·툴팁·`ConflictNotice`는 바꾸지 않는다), `web/src/e2e-config.test.ts`, `tests/e2e_backend.py`, `tests/test_e2e_backend.py`, `src/govops/api/smoke_conversation.py`, `tests/test_api_smoke_conversation.py`, `Makefile`, `tests/test_makefile.py`, `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/THREAT_MODEL.md`, `docs/EVAL.md`, `tests/test_docs_consistency.py`(새 단언 **추가에 한함**, 기존 단언 약화·삭제 금지). 이 step은 e2e 스펙·e2e 백엔드·스모크·Makefile·문서를 새 UI와 새 프로필에 맞춘다. TDD(가드 테스트와 소비자 테스트를 먼저).

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/e2e/support.ts`, `web/e2e/approval-flow.spec.ts`, `web/e2e/blocked-and-isolation.spec.ts`, `web/src/lib/testids.ts`, `web/src/components/user-card.tsx`, `web/src/e2e-config.test.ts`, `tests/e2e_backend.py`, `tests/test_e2e_backend.py`, `src/govops/api/smoke_conversation.py`, `tests/test_api_smoke_conversation.py`, `Makefile`, `tests/test_makefile.py`, `AGENTS.md`, `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/THREAT_MODEL.md`, `docs/EVAL.md`, `tests/test_docs_consistency.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

### 1. e2e 백엔드와 스모크

- `tests/e2e_backend.py`: 서비스 프로필을 `profile_for("writes_team")`로 바꾼다(요청자 절이 messages에 들어가는 경로를 소켓으로 확인). `StoryChat`에 분기를 더한다: 마지막 user 메시지에 `다른 팀 채널`이 있으면 요청자 팀이 **아닌** 다른 팀 채널(예: 플랫폼 사용자에게 `team-finance`)로 `slack_send_message` 초안을 낸다(결정론 검사의 `recipient_not_allowed` 차단 경로). 기존 분기와 파일 머리의 "실제 Guardrail 검증이 아니다" 주석은 유지한다.
- `tests/test_e2e_backend.py`(소켓 서브프로세스 테스트에 추가): ① 플랫폼 사용자가 조회 후 `GET /conversations`에 그 대화가 제목(요청 앞부분)·상태와 함께 보이고 재무 사용자의 목록에는 없다 ② 요청자 본인의 대화 뷰에 `tool_steps`(이름·성공만, 센티널 없음)가 있고 승인자 행(`GET /approvals`)에는 없다 ③ 승인 뒤 본인 뷰에 `approval_method`와 파싱 가능한 UTC `effect_at`이 있다 ④ `다른 팀 채널` 요청이 `blocked` 상태와 본인 팀 채널만 안내하는 `check.message`로 끝난다(다른 팀 채널 ID가 응답에 없음) ⑤ 목록 응답의 키 집합이 정확히 일치한다. 기존 시나리오는 그대로 통과해야 한다. 모든 대기에 timeout을 둔다(FP-066).
- `src/govops/api/smoke_conversation.py`: 두 번째 턴의 `SHARE` 문구를 `"INC-2026-012 장애보고 요약해서 팀 채널에 공유해줘"`로 바꾼다(채널 ID를 말하지 않는다). 초안이 만들어진 뒤 **초안 수신처가 요청자(`demo-platform-g3`) 팀 채널 `team-platform`**인지 확인하는 새 단계 `share_targets_own_team_channel`을 더한다(되묻지 않고 요청자 팀 채널로 초안이 만들어지는지 live에서 보는 검사). 세 번째 턴의 문구와 기존 단계·출력 형식(PASS/FAIL 줄, 예외 클래스 이름만)은 바꾸지 않는다. `tests/test_api_smoke_conversation.py`는 새 단계의 성공·수신처 불일치 실패 사례를 추가한다(응답 본문을 출력하지 않는 기존 규칙 유지).

### 2. e2e 스펙 (`web/e2e/`, 셀렉터는 `../src/lib/testids`만)

- `testids.ts`: 더 이상 쓰지 않는 `switchUser`를 **제거**한다. 사용처는 e2e `support.ts`와 `web/src/components/user-card.tsx`의 구 "사용자 전환" 버튼이며 `rg`로 확인하고 둘을 함께 고친다(`user-card.tsx`에서는 그 버튼만 제거한다. 같은 `logout` 핸들러를 부르는 로그아웃 아이콘 버튼 `logout`이 남는다). 다른 이름은 지우지 않는다.
- `support.ts`: `switchTo(page, label)`는 `logout`을 눌러 `/login`으로 간 뒤 `loginAs`로 다시 로그인한다. 클릭 대상은 `testids.logout`(기존 `switchUser`와 같은 로그아웃 동작)이고 `/login` URL 대기 단언과 `loginAs`는 그대로 유지한다(단언을 약화하지 않는다). 필요하면 대화 목록 항목을 여는 도우미를 더한다.
- `approval-flow.spec.ts`(기존 흐름 유지 + 새 UI 검증): 조회 뒤 `turnAnswer`·`turnCitedDoc`과 함께 `toolSteps`가 보이고 단계 행이 1개 이상, 사이드바에 `conversationItem(id)`가 보이며 제목에 `INC-2026-012`가 있음, 공유 요청 뒤 승인 카드와 `composerHint`, 승인 대기 중 입력창 비활성과 409(`approval_pending`), `navApprovals`로 승인함 `inboxTable`에 들어가 **요청자 버튼(`inboxRequester`)으로 행을 펼친 뒤** `approvalOpenConversation`으로 대화로 이동, 승인 후 `sentCard`가 보이고 `sentResultId`가 "Slack 메시지 ID"를 포함하며 `sentTarget`이 `team-platform`·`sentMethod`가 "승인"·`sentAt`이 비어 있지 않음, 발송 1건(관측 포트), 새로 고침 뒤에도 `sentCard`와 1건, 같은 승인 재전송에도 1건, **`/approvals`로 이동한 뒤 사이드바 `conversationItem(id)`를 눌러 같은 대화가 다시 열리고 `sentCard`가 보임**, 재무 사용자로 로그아웃·재로그인한 뒤 같은 조회가 `찾을 수 없습니다`이고 사이드바에 플랫폼 대화 `conversationItem(id)`가 없음(`toHaveCount(0)`)·승인함 비어 있음·발송 1건 그대로·플랫폼 대화 API 404.
- `blocked-and-isolation.spec.ts`(기존 흐름 유지 + 추가): 입력 차단 카드와 센티널 비노출, 이어서 정상 요청 뒤에도 카드 유지, 쿠키 속성·CSRF·세션 폐기(로그아웃 버튼 `logout` 사용)는 그대로. 추가: `다른 팀 채널` 요청이 `checkBlockedCard`로 그려지고 문구에 `team-platform`이 있으며 `team-finance`가 없고 입력 차단 카드(`blockedCard`)는 아님, 테마 버튼 `themeToggle("dark")` 클릭 뒤 `<html>`에 `dark` 클래스가 있고 새로 고침 뒤에도 유지(불리언으로만 단언).
- 두 스펙 파일 머리 주석의 "UI 계약 검증이며 실제 Guardrail·모델 동작 검증이 아니다" 문장은 유지한다. 스펙은 쿠키 값·`document.cookie` 값·응답 본문 원문을 출력하지 않고, 고정 `waitForTimeout`을 쓰지 않으며, `data-testid` 문자열 리터럴을 직접 쓰지 않는다(기존 가드). `e2e-config.test.ts`의 가드는 약화하지 않고 필요하면 새 규칙(`switchUser` 미사용)만 **추가**한다.

### 3. `make e2e-web` 청결 검사 개선 (FP-067)

- 지금의 검사("실행 뒤 `git status --porcelain`이 비어 있지 않으면 실패")는 실행 전에 이미 더러운 작업 트리(예: dev 재실행이 만든 결과 디렉터리·골든 수정·비용 원장)에서 오탐한다. **실행 전후의 `git status --porcelain` 출력을 비교해 e2e가 새로 만든 변경만** 실패로 한다.
- 레시피: 실행 전 상태를 임시 파일(`mktemp`)에 정렬해 저장 → Playwright 실행(종료 코드 보존) → 실행 후 상태를 정렬해 저장 → `comm -13`으로 **후에만 있는 줄**을 얻음 → 있으면 그 경로(`cut -c4-`)만 출력하고 청결 실패(종료 코드 1) → 임시 파일 삭제. Playwright가 실패했으면 그 종료 코드로 끝나고, 둘 다 성공해야 0이다. make는 `sh`로 실행하므로 프로세스 치환(`<(...)`)을 쓰지 않는다. 변경된 파일의 **내용**은 출력하지 않는다.
- 이미 변경된 파일을 e2e가 또 바꾼 경우는 `git status` 줄이 같아 검사가 잡지 못한다. 이 한계와 "e2e는 추적 파일을 쓰지 않는다"는 전제를 타깃 위 주석에 한 줄로 적는다. `Makefile` 주석과 `AGENTS.md` 명령 줄에는 기존 두 안내(포트 3200·8200·8201 사용 중이면 시작 거부, `next build`가 `web/.next`를 덮어쓰므로 `next start` 데모 서버(3100)는 먼저 끌 것)를 **같은 표현으로 유지**하고 새 검사 방식("e2e가 새로 만든 변경만 실패")을 더한다.
- `tests/test_makefile.py`의 기존 `e2e-web` 단언을 **같은 강도로** 갱신한다: Playwright 종료 코드 보존, `git status --porcelain`이 실행 전후 두 번, `comm -13`, `mktemp`, 프로세스 치환 없음, `verify` 레시피에 `e2e-web`이 없음, "사용자 실행" 주석.

### 4. 문서 (결정 내용과 현재 사실만, 결정 주체·경위는 쓰지 않는다)

- `docs/ADR.md`: 새 절 `### ADR-024: M5 결정 — 요청자 절 프로필, 대화 목록·도구 단계, 렌더링·테마 정책`(ADR-023 뒤, 같은 형식: 상태·날짜 2026-10-08·결정·이유·트레이드오프·변경 기록). 결정 항목: ① 프로필 `writes_team` = 템플릿(발송 규칙) + 요청자 절(세션 사용자의 소속 팀·보낼 수 있는 채널·전사 공지 승인 조건), `writes_org`는 동결(삭제하지 않고 새 기본값으로 쓰지 않음), `GOVOPS_AGENT_PROFILE` 기본값은 `writes_team`, 골든은 사용자 dev 재실행 때 러너가 `writes_team`으로 재기록 ② 프롬프트 해시 기록: `prompt_hash` = 템플릿(도구·모델·파라미터 포함) 해시, 사용자별 요청자 절은 `requester_section_sha256`(manifest·골든 헤더), 호출별 `request_key`가 전체 messages를 덮음 ③ 채널·승인 급수의 단일 출처(`allowed_channels`·`ANNOUNCE_APPROVER_MIN_GRADE`) ④ `GET /conversations`(소유자 본인, 상한 50, 색인=힌트·체크포인트=권위, 읽기 전용, 제목 규칙)과 요청자 본인 턴 뷰의 `tool_steps`(읽기 도구의 이름·성공만)·`approval_method`·`effect_at` ⑤ 렌더링 정책: `react-markdown` 10.1.0·`remark-gfm` 4.0.1 정확 고정, 원시 HTML 미렌더링, 이미지·위험 스킴 링크 금지(http/https만), 새 의존성 없음 ⑥ 테마 저장소 예외: `localStorage`는 `lib/theme.ts` 한 파일·키 `govops-theme`·값 `light`/`dark`/`system`뿐, 접근은 `try/catch` ⑦ e2e-web 청결 검사는 실행 전후 비교. 다른 ADR의 변경 기록 항목 수는 바꾸지 않는다.
- `docs/ARCHITECTURE.md`: "BFF 인증 흐름" 절 뒤에 "UI 계약" 소절(대화 목록 API, 턴 뷰의 새 필드와 승인자 뷰 제외, BFF 투영, 마크다운 렌더링 정책, 테마 저장소 예외, 글꼴 스택과 외부 다운로드 없음)과 프롬프트 구성(요청자 절, 해시 기록) 한 문단을 현재 사실로 쓴다. `<!-- m4-current:start -->`~`<!-- m4-current:end -->` 표식 블록은 건드리지 않는다.
- `docs/THREAT_MODEL.md`: 모델 출력 렌더링 위협(마크다운 안의 스크립트·원격 이미지·위험 링크로 인한 실행·정보 유출)과 완화(`skipHtml`·이미지 금지·http/https만·`rel` 속성)·검증(`web/src/components/markdown.test.tsx`)을 **기존 표 구조를 바꾸지 않고** 한 행으로 더하고, 도구 단계 비노출(이름·성공만, 승인자 뷰 제외)을 해당 행의 완화·검증에 한 줄 더한다. 표를 파싱하는 `tests/test_docs_consistency.py`의 기존 단언이 통과해야 한다.
- `docs/EVAL.md`: 프롬프트 해시 항목에 "`writes_team`은 템플릿 해시이고 사용자별 요청자 절은 별도 해시로 기록한다"를 현재 사실로 더한다(기존 문장의 다른 부분은 바꾸지 않는다).
- `AGENTS.md`: 명령어 절의 `make e2e-web` 줄만 위 3절 내용으로 갱신한다. **`CRITICAL:` 줄을 추가·삭제하지 않는다**(26개 단언). `tests/test_docs_consistency.py`에 새 단언을 **추가**한다: ADR-024 절 존재와 필수 표현(`writes_team`·`requester_section_sha256`·`govops-theme`·`react-markdown`), `make e2e-web` 줄이 여전히 하나이고 "사용자 실행" 포함.

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

- `make e2e-web`, `pnpm -C web exec playwright test`, `make smoke-conversation`, `make eval-run-dev`를 실행하지 마라. 이유: 브라우저·live 실행은 사용자가 한다(AGENTS.md CRITICAL). 이 step은 스펙·스모크·가드를 작성하고 AC의 검증 명령으로 타입·린트·가드·소켓 테스트를 확인한다.
- 기존 e2e 단언을 약화하거나 `data-testid` 문자열 리터럴을 쓰지 마라. 셀렉터는 `testids.ts` 한 곳이다. 이유: 이름 변경과 제거는 `tsc`가 잡아 주지만 문자열 리터럴은 잡지 못한다. 기존 가드(`e2e-config.test.ts`)가 리터럴을 막는다. 스펙에서 쿠키 값·`document.cookie` 값·토큰을 출력하지 말고 trace·screenshot·video를 켜지 마라.
- `Makefile`의 `verify` 레시피를 바꾸거나 `AGENTS.md`에 `CRITICAL:` 줄을 더하지 마라. `docs/PRD.md`의 `| M5 |` 행, ADR-007·009·010의 변경 기록 항목 수, `m4-current` 표식 블록을 바꾸지 마라. 이유: 기존 문서·Makefile 일관성 테스트가 정확히 단언한다(FP-060·061).
- `e2e-web`의 청결 검사에 프로세스 치환이나 변경된 파일 내용 출력을 쓰지 마라. 이유: make는 `sh`로 실행해 프로세스 치환이 동작하지 않고, 파일 내용 출력은 비밀을 노출할 수 있다. 경로만 출력한다. 실행 전부터 더러운 경로는 실패 대상이 아니다(FP-067).
- 문서에 이번 live 실행의 결과 수치·결정 주체·경위를 쓰지 마라. `docs/results/`와 `.claude/`는 수정하지 마라. 이유: 결과 문서는 사용자 live 뒤 결과 PR에서 쓰고, 이 step의 문서는 결정 내용과 현재 사실만 담는다.

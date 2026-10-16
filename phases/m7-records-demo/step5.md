# Step 5: web-records-ui

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/web/src/lib/server/proxy.ts`, `/web/src/lib/server/project.ts`, `/web/src/lib/server/project.test.ts`
- `/web/src/app/api/conversations/[id]/route.ts`(BFF 형식), `/web/src/app/api/routes.test.ts`, `/web/src/app/api/bff.integration.test.ts`
- `/web/src/app/(app)/c/[id]/page.tsx`, `/web/src/components/thread.tsx`, `/web/src/lib/messages.ts`, `/web/src/lib/types.ts`, `/web/src/lib/testids.ts`, `/web/src/ui-boundary.test.ts`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/app/api/conversations/[id]/audit/route.ts`, `web/src/app/api/audit/verify/route.ts`, `web/src/app/(app)/records/[id]/page.tsx`, `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/lib/messages.ts`, `web/src/lib/types.ts`, `web/src/lib/testids.ts`, `web/src/components/records-view.tsx`, `web/src/components/records-view.test.tsx`, `web/src/components/thread.tsx`, `web/src/components/thread.test.tsx`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `web/src/app/api/conversations/[id]/audit/route.ts`, `web/src/app/api/audit/verify/route.ts`, `web/src/app/(app)/records/[id]/page.tsx`, `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/lib/messages.ts`, `web/src/lib/types.ts`, `web/src/lib/testids.ts`, `web/src/components/records-view.tsx`, `web/src/components/records-view.test.tsx`, `web/src/components/thread.tsx`, `web/src/components/thread.test.tsx`, `web/src/app/api/routes.test.ts`, `web/src/app/api/bff.integration.test.ts`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: BFF 두 개와 기록 화면이다. BFF는 허용 목록 투영으로만 응답하고 브라우저는 FastAPI를 직접 부르지 않는다.

### 1. BFF

- `GET /api/conversations/[id]/audit` → `proxyJson(request, { path: "/conversations/<id>/audit", project: projectAudit, id })`(`<id>`는 검증된 대화 ID).
- `GET /api/audit/verify` → `proxyJson(request, { path: "/audit/verify", project: projectVerify })`.
- `projectAudit`·`projectVerify`(`web/src/lib/server/project.ts`): 허용 목록 투영. `projectAudit`는 최상위 `conversation_id`(ID 형식)·`turns`·`truncated`, 턴의 `turn_id`·`turn_index`·`events`, 이벤트의 `seq`(정수)·`ts`(ISO 길이 제한)·`event`(`^[a-z_.]{1,40}$`)·`actor`(`^demo-[a-z-]+-g[0-9]+$`)·`tool`·`decision`·`check_result`·`reason`·`code`(`^[a-z_]{1,40}$`)·`mock_id`(`^[A-Za-z0-9._-]{1,40}$`)·`fallback`(bool)·`cost_usd`(숫자 소수 6자리 문자열)·`hash12`·`draft_hash12`(`^[0-9a-f]{12}$`)만 통과시킨다. `projectVerify`는 `status`(`pass`|`fail`)·`checked_at`·`chain`(`records`·`files`·`anchors_checked`·`problems[{code(^[a-z_]{1,40}$), seq}]`)·`anchors`·`legacy_unverified`·`known_limit`만 통과시킨다. 64 hex 해시·`kid`·서명·경로 형태 값은 어떤 필드에서도 통과하지 않는다.
- 새 오류 코드를 만들지 않는다. 404는 기존 `conversation_not_found`, 업스트림 5xx·503은 `502 upstream_unavailable`이다.

### 2. 화면

- `web/src/app/(app)/records/[id]/page.tsx`와 `web/src/components/records-view.tsx`: ① 상단 검증 배지(PASS/FAIL, 레코드 수, 고정점 수, "마지막 고정점 이후는 탐지되지 않습니다", "이 호스트의 체인 기준입니다") ② 턴별 타임라인(이벤트·사유·결정 코드의 한국어 라벨은 `web/src/lib/messages.ts`) ③ 404 안내 "기록을 찾을 수 없습니다. 정리된 대화는 기록 화면에서 볼 수 없고 체인은 audit-verify로 확인합니다". 텍스트만 렌더링한다(마크다운·HTML 금지). 모르는 코드는 코드 그대로 표시한다.
- 대화 화면(`thread.tsx`)에 "기록" 링크를 하나 더한다. 사이드바는 바꾸지 않는다.
- 세션 토큰·쿠키 값을 JS가 읽을 수 있는 곳에 두지 않는다.
- **소스 문자열 제약(기존 가드 테스트가 파일을 수집해 정규식으로 검사한다)**: `web/src/ui-boundary.test.ts`는 `components`·`hooks`·`app/(app)`·`app/login`의 모든 비테스트 `.ts(x)`와 `lib/messages.ts`·`lib/types.ts`를 읽어 `masked|output_flagged|guardrail_block|stop_reason|evidence_ids|guardrail_signals|guardrail_post|dangerouslySetInnerHTML|@/lib/server|next/headers` 문자열이 **소스에 없을 것**을 단언한다. 감사 이벤트 이름 `guardrail.tool_masked`·`guardrail.output_flagged`가 이 문자열을 포함하므로 **이 이벤트 이름 리터럴을 소스에 쓰지 않는다**: 이벤트 라벨은 `guardrail.` 접두사(`startsWith`)로 "Guardrail 판정" 한 라벨로 묶고 개별 이름을 키로 두지 않는다. 같은 파일들에 `localStorage`·`sessionStorage`·`document.cookie`도 쓰지 않는다.
- `web/src/app/api/routes.test.ts`는 각 `route.ts`에 `export const dynamic = "force-dynamic"`가 있고 `next/headers`를 import하지 않으며 변경 메서드에는 `requireSameOrigin(request)`가 있을 것을 요구한다. 새 route 두 개 모두 `export const dynamic = "force-dynamic"`를 둔다(GET만 있으므로 Origin 검사는 `proxyJson`이 한다).
- `tests/test_web_sync.py`는 `web/src/lib/messages.ts`의 `toolLabel`·`sentResultLabel` 함수 본문을 파싱한다. 이 두 함수와 기존 상수(`APPROVED_RUN_NOTICE`, `DELETE_CONFIRM_NOTICE` 등)는 바꾸지 말고 새 라벨·문구는 **파일 끝에 새 export로** 더한다.

### 3. 테스트

- `project.test.ts`: 투영 허용 목록(64 hex 해시·`kid`·서명·허용 밖 키가 섞인 입력에서 제거).
- `bff.integration.test.ts`(**프로브 추가에 한함**): 사전 등록 B1~B7.
- `routes.test.ts`(**route 파일 이름 목록에 `audit/verify/route.ts`·`conversations/[id]/audit/route.ts` 두 줄을 더하는 데 한함**).
- `records-view.test.tsx`: PASS·FAIL 배지, 타임라인 순서, 404 문구, 모르는 코드 표시.
- `thread.test.tsx`(**기록 링크 추가로 필요한 mock 추가에 한함, 기존 단언 약화 금지**).

**사용자 실행(이 step에서 실행하지 않는다)**: `make e2e-web`(step 6 이후 사용자 실행).

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드)를 바꾸지 않았는가?
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

- 브라우저 코드에서 FastAPI(`GOVOPS_API_URL`)를 직접 부르지 마라. 이유: BFF 경계, 세션 토큰 비노출(AGENTS.md 웹 행).
- 응답 본문·화면에 `NEXT_PUBLIC_*`·`localStorage`·`document.cookie`로 토큰을 두지 마라. 이유: AGENTS.md 웹 경계.
- `projectAudit`에서 객체를 통째로 통과시키지 마라. 이유: 업스트림이 필드를 추가해도 브라우저에 새지 않게 하는 허용 목록이 이 계층의 보증이다.
- `web/src/app/api/routes.test.ts`·`bff.integration.test.ts`의 기존 단언을 약화하지 마라. 이유: 정확 일치 단언은 항목을 정확히 더해서만 갱신한다(FP-071).
- 승인 UI·대화 스레드의 기존 `data-testid`와 문구를 바꾸지 마라. 이유: e2e 스펙 3개가 의존한다.
- 소스에 `guardrail.tool_masked`·`guardrail.output_flagged` 같은 이벤트 이름 리터럴을 쓰지 마라. 이유: `ui-boundary.test.ts`가 `masked`·`output_flagged` 문자열을 소스에서 금지한다(`guardrail.` 접두사 매칭으로 대체).

## 소비자 점검(시뮬레이션 통과, 추가 grep 점검 포함)

추가 grep 점검: `web/src`의 파일 수집 테스트(`guards.test.ts`는 `web/src` 전체, `ui-boundary.test.ts`는 위 디렉터리·고정 `lib` 목록, `routes.test.ts`는 `app/api`의 `route.ts`)와 `tests/test_web_sync.py`를 확인해 위 소스 문자열 제약을 작업에 옮겼다.

스텁 route·페이지를 넣은 vitest에서 `src/app/api/routes.test.ts`(route 파일 이름 목록 정확 일치)가 실패했다. `src/app/api/bff.integration.test.ts`·`src/lib/server/project.test.ts`·`src/ui-boundary.test.ts`는 스텁 단계에서 통과했으나 실제 투영 구현에서 영향받을 수 있어 수정 허용 목록에 "추가에 한함"으로 넣었다(WARN은 허용 목록에 올리지 않았고 이 단락에만 적는다). 시뮬레이션의 `tsc --noEmit` 오류 `LayoutProps`는 복사본에 `next typegen` 산출물이 없어서 생긴 환경 문제이며 스텁과 무관하다(원본에서는 `make verify`의 `pnpm -C web typecheck`가 먼저 `next typegen`을 실행한다).

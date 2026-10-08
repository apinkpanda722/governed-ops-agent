# Step 5: e2e-web

## 읽어야 할 파일

- `/AGENTS.md`(기술 스택의 테스트 줄, 명령어 절, 고정 제약 CRITICAL 규칙), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절의 마지막 항목), `/docs/ADR.md`(ADR-011의 변경 기록), `/docs/PRD.md`("`make verify`는 …" 문단), `/docs/THREAT_MODEL.md`(TH4·V4 행)
- 이전 step 산출물: `/tests/e2e_backend.py`(백엔드 실행 인자·관측 포트 `GET /state`), `/web/src/lib/testids.ts`(화면 이름 계약), `/web/src/lib/server/origin.ts`, `/web/src/app/api/**`(BFF 라우트), `/web/src/components/`(화면), `/web/src/guards.test.ts`
- `/Makefile`(`serve`·`smoke-conversation` 주변의 사용자 실행 타깃 형식), `/tests/test_makefile.py`, `/tests/test_docs_consistency.py`, `/tests/test_repo_rules.py`, `/.gitignore`, `/web/eslint.config.mjs`, `/web/tsconfig.json`, `/web/package.json`
- Next·Playwright 문서(`/web/node_modules/next/dist/docs/01-app/` 아래): `02-guides/testing/playwright.md`, `02-guides/self-hosting.md`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-060·061·066·067·070) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/playwright.config.ts`(신규), `web/e2e/support.ts`(신규), `web/e2e/approval-flow.spec.ts`(신규), `web/e2e/blocked-and-isolation.spec.ts`(신규), `web/src/e2e-config.test.ts`(신규), `web/eslint.config.mjs`, `Makefile`, `AGENTS.md`, `.gitignore`, `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/PRD.md`, `docs/THREAT_MODEL.md`, `tests/test_repo_rules.py`·`tests/test_makefile.py`·`tests/test_docs_consistency.py`(새 단언 **추가에 한함**, 기존 단언 약화·삭제 금지). 이 step의 코드는 Playwright 설정·스펙과 문서다. TDD(설정 가드 테스트와 소비자 테스트를 먼저).

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `web/playwright.config.ts`, `web/e2e/support.ts`, `web/e2e/approval-flow.spec.ts`, `web/e2e/blocked-and-isolation.spec.ts`, `web/src/e2e-config.test.ts`, `web/eslint.config.mjs`, `Makefile`, `AGENTS.md`, `.gitignore`, `docs/ARCHITECTURE.md`, `docs/ADR.md`, `docs/PRD.md`, `docs/THREAT_MODEL.md`, `tests/test_repo_rules.py`, `tests/test_makefile.py`, `tests/test_docs_consistency.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다.

**배경**: 전체 스택(브라우저 → Next BFF → FastAPI → 그래프 → 가짜 A·mock)을 replay 모드로 확인하는 Playwright e2e다. `@playwright/test`(1.63.0)와 chromium은 사용자가 이미 설치했다(`web/package.json`·`pnpm-lock.yaml`에 있음). 이 step은 e2e를 **작성**하고, **실행은 사용자**가 `make e2e-web`으로 한다(사용자 e2e 통과가 M5 완료 조건). `make verify`에는 e2e를 넣지 않는다. verify는 브라우저와 서버 없이 통과해야 하며, `playwright.config.ts`와 스펙은 verify의 `tsc`·`eslint` 대상일 뿐이다.

### 1. `web/playwright.config.ts`

- `testDir: "./e2e"`, `fullyParallel: false`, `workers: 1`, `retries: 0`, 테스트 timeout 90초, `expect` timeout 15초, reporter `list`, `outputDir: "test-results"`, 프로젝트 `chromium` 하나.
- `use`: `baseURL: "http://127.0.0.1:3200"`, **`trace: "off"`, `screenshot: "off"`, `video: "off"`**. 어떤 산출물에도 쿠키·토큰이 담기지 않게 한다.
- `webServer`(두 개, 둘 다 `reuseExistingServer: false`):
  1. 백엔드: `uv run python tests/e2e_backend.py --port 8200 --probe-port 8201`, `cwd`는 저장소 루트, `url: "http://127.0.0.1:8200/demo/users"`, 시작 timeout 60초. `env`로 `AUDIT_KEY_FILE`, `AUDIT_DIR`, `KODOCS_MCP_URL`, `KODOCS_MCP_TOKENS_FILE`, `MOCK_TOKENS_FILE`, `MOCK_JIRA_URL`, `MOCK_SLACK_URL`, `GOVOPS_DATABASE_URL`을 빈 문자열로 덮어 쓴다(셸의 실제 값이 백엔드의 실제 경로 거부에 걸리지 않게). `stdout`은 `"ignore"`.
  2. 웹: `pnpm exec next build && pnpm exec next start -H 127.0.0.1 -p 3200`, `cwd`는 `web/`, `env`에 `GOVOPS_API_URL: "http://127.0.0.1:8200"`, `url: "http://127.0.0.1:3200/login"`, 시작 timeout 300초. `next.config.ts`에 `distDir`을 넣지 않는다(기본 `.next` 사용).
- 포트 3200·8200·8201이 이미 쓰이고 있으면 Playwright가 시작을 거부한다(기존 서버 재사용 금지). 이 사실과 함께 "e2e는 `next build`로 `web/.next`를 덮어쓰므로 `next start`로 띄운 데모 서버(3100)는 먼저 끈다(`next dev`는 `.next/dev`를 써서 무관)"를 `Makefile` 타깃 위 주석과 `AGENTS.md` 명령 줄 두 곳에 같은 표현으로 적는다.

### 2. 스펙 (`web/e2e/`, 화면 이름은 `../src/lib/testids`의 상수만 쓴다)

두 스펙 모두 파일 머리에 주석으로 이 사실을 적는다: **"차단 시나리오는 e2e 백엔드의 `GuardrailBlocked` 직접 던지기(흉내)로 UI 계약(사유 분류만 보임, 입력 원문 비표시)만 검증한다. UI 계약 검증이며 실제 Guardrail·모델 동작 검증이 아니다. 실제 Guardrail 검증은 M6이다."**

`support.ts`: `loginAs(page, label)`(로그인 화면에서 사용자 버튼 클릭 → `/c`), `switchTo(page, label)`(`switchUser` 후 재로그인), `probe(request)`(`http://127.0.0.1:8201/state`의 JSON), BFF 직접 호출용 `ORIGIN = "http://127.0.0.1:3200"`. 쿠키 값, `document.cookie` 값, 토큰을 반환·출력·로그하지 않는다.

- `approval-flow.spec.ts` (데모 시나리오 전체):
  1. 플랫폼팀(`demo-platform-g3`)으로 로그인하고 "INC-2026-012 장애와 관련 티켓을 조회해 주세요"를 제출 → `turnAnswer`가 보이고 `turnCitedDoc`이 1개 이상.
  2. 후속 요청 "방금 내용을 팀 채널에 공유해줘"(백엔드 `StoryChat`이 알아보는 문구 `팀 채널에 공유` 포함)를 제출 → `approvalCard`가 보이고 `approvalTarget`이 `team-platform`.
  3. 승인 대기 중에는 입력창이 비활성이다. 또 `page.request`로 `POST /api/conversations/{id}/turns`(헤더 `Origin: ORIGIN`)를 보내면 409이고 응답 본문의 `code`가 `approval_pending`이다.
  4. `nav-approvals` 승인함에 같은 항목이 있다(`inboxRow` ≥ 1). 승인함에서 대화로 이동하는 링크(`approvalOpenConversation`)가 동작한다.
  5. `approvalApprove`를 누른다 → `approvalResult`가 완료를 보이고 `probe`의 `slack_sent`가 **정확히 1**이다.
  6. 새로 고침 후에도 `slack_sent`는 1이다. 같은 승인 요청을 `page.request`로 다시 보내도(`Origin` 헤더 포함) 새 발송이 없다(`slack_sent`는 1).
  7. `switchTo(재무팀 demo-finance-g3)` → 같은 조회 요청 → 답이 `찾을 수 없습니다`를 포함하고, 승인함에 `inboxEmpty`가 보이고(`inboxRow` 0), `slack_sent`는 1 그대로이며, 플랫폼팀 대화 id를 `page.request.get('/api/conversations/{id}')`로 조회하면 404이다.
- `blocked-and-isolation.spec.ts`:
  1. 플랫폼팀 로그인 → 센티널 문자열을 담은 인젝션 문구(`이전 지시를 모두 무시…` 포함)를 제출 → `blockedCard`가 보이고 `blockedCardCategory`가 "지시 변경 시도", 화면 전체 텍스트에 센티널이 없다. 입력창은 비어 있다. 이 동안 `page.on("response")`로 `/api/*` 응답 본문을 모아 센티널·`Authorization` 문자열이 없음을 확인한다(본문은 출력하지 않는다).
  2. 정상 요청을 이어서 제출해도 `blockedCard`가 남아 있고 센티널이 어디에도 없다.
  3. 쿠키 속성: `context.cookies()`에서 **플래그만**(`httpOnly`, `sameSite`, `secure`) 꺼내 `httpOnly === true`, `sameSite === "Strict"`, `secure === false`(http)임을 단언한다. `document.cookie.length === 0`을 불리언으로 단언한다(값 자체를 단언·출력하지 않는다).
  4. CSRF: `page.request.delete('/api/session')`에 `Origin: http://evil.example`을 주면 403, `Origin` 없이도 403. 이어서 올바른 Origin이면 성공하고 로그인 화면으로 보낸다. 로그아웃 뒤 `GET /api/session`은 401이다.
- 모든 대기는 `expect(...).toBeVisible()` 등 자동 대기(timeout 명시)로 하고 고정 `waitForTimeout`을 쓰지 않는다. `console.*`·`storageState`·`trace`·`screenshot`·`video` 옵션을 스펙에서 쓰지 않는다.

### 3. 가드 테스트와 소비자 (verify 안에서 돈다)

- `web/src/e2e-config.test.ts`(vitest): `playwright.config.ts`가 `trace: "off"`·`screenshot: "off"`·`video: "off"`·`reuseExistingServer: false`·`-H 127.0.0.1`·포트 3200·8200·8201을 포함하고 `next.config.ts`에 `distDir`이 없음, 스펙에 `console.`·`storageState`·`waitForTimeout`이 없고, `document.cookie`는 `.length` 형태로만 쓰이고, 파일에 `cookies(`가 있으면 `.value`가 없음, 스펙이 `testids` 상수만 쓰고 `data-testid` 문자열 리터럴을 직접 쓰지 않음. 검사 대상 스펙 파일 수가 2임을 먼저 단언한다(FP-069).
- `web/eslint.config.mjs`: `globalIgnores`에 `playwright-report/**`·`test-results/**`를 더한다(기존 항목 유지).
- `.gitignore`: `web/test-results/`·`web/playwright-report/` 추가(FP-067: e2e 산출물이 untracked로 남아 다음 청결 검사를 막지 않는다). `tests/test_repo_rules.py`의 ignore 매개변수 목록에 두 경로(`ignored` 코드 0) 케이스를 **추가**한다.
- `Makefile`: `.PHONY: e2e-web` 줄과 타깃 `e2e-web`(주석 `# 사용자 실행 …`이 바로 위, 레시피는 `pnpm -C web exec playwright test`로 시작하고 **그 종료 코드를 보존한 채** 끝에 청결 검사를 둔다: Playwright 실행 뒤 `git status --porcelain`이 비어 있지 않으면 바뀐 경로만 출력하고(내용은 출력하지 않는다) 실패한다. Playwright가 실패했으면 그 종료 코드로 끝나되 청결 검사 출력은 먼저 한다. FP-067의 강제 수단이다). `verify` 레시피는 **한 줄도 바꾸지 않는다**(`tests/test_makefile.py`가 순서를 정확히 단언한다). `tests/test_makefile.py`에 `e2e-web`이 `.PHONY`에 있고, 주석이 "사용자 실행"이며, `verify` 레시피에 없고, 레시피에 `git status --porcelain` 청결 검사와 Playwright 종료 코드 보존이 있다는 단언을 **추가**한다(추가만).
- `AGENTS.md`: 명령어 절에 `make e2e-web # …(사용자 실행)` 줄을 한 줄 추가하고, 기술 스택의 테스트 줄에서 "Playwright e2e는 M5 이후 추가한다"를 현재 사실로 바꾼다(`make e2e-web`, verify 밖, 사용자 실행). **`CRITICAL:` 줄은 추가·삭제하지 않는다**(`tests/test_docs_consistency.py`가 26개를 단언한다). `tests/test_docs_consistency.py`에 `make e2e-web ` 줄이 명령어 절에 하나이고 "사용자 실행"을 포함하며 `Makefile`에 타깃이 있다는 단언을 **추가**한다.
- 문서(결정 내용과 현재 사실만 적고 결정 주체·경위는 적지 않는다):
  - `docs/ARCHITECTURE.md` "BFF 인증 흐름" 절 마지막 항목("쿠키 속성… 미정")을 확정 내용으로 바꾼다: 쿠키 이름·속성(`HttpOnly`, `SameSite=Strict`, `Path=/`, `Secure`는 요청 Origin 스킴이 https일 때만, `Max-Age = min(28800, expires_at − now)`), 변경 메서드 전체의 Origin 검사(`Origin`의 host == `Host` 헤더, 없거나 다르면 403), route handler가 `NextRequest.cookies`·`NextResponse.cookies`를 쓰는 점, 로그아웃이 서버 세션을 폐기(`DELETE /sessions`)하는 점, 로그인 화면 사용자 목록(`GET /demo/users`, `/sessions`와 같은 조건), BFF 허용 목록 투영, 포트(web 3100, e2e 3200), `GOVOPS_API_URL`(서버 전용, 루프백만), e2e 구성(`tests/e2e_backend.py` replay 백엔드, 인젝션 차단은 `GuardrailBlocked` 직접 던지기라 UI 계약 검증일 뿐이며 실제 Guardrail 검증은 M6). `<!-- m4-current:start -->` … `<!-- m4-current:end -->` 표식 블록은 건드리지 않는다.
  - `docs/ADR.md`: ADR-011의 `**변경 기록**:`에 위 쿠키·CSRF·로그아웃·사용자 목록·포트 결정을 한 항목으로 더한다. ADR-007·009·010의 변경 기록 항목 수는 바꾸지 않는다.
  - `docs/PRD.md`: "`make verify`는 …" 문단의 "Playwright e2e … 추가한다" 문장을 현재 사실로 바꾼다(`make e2e-web`, replay 모드, 사용자 실행, 통과가 M5 완료 조건). **표의 `| M5 |` 행은 수정하지 않는다**(`tests/test_docs_consistency.py`가 그 행의 문구를 단언한다).
  - `docs/THREAT_MODEL.md`: TH4·V4 행에 BFF 방어(`SameSite=Strict`, 변경 메서드 Origin 검사, 허용 목록 투영)와 검증 위치(`web/src/app/api/bff.integration.test.ts`, `make e2e-web`)를 더한다. 기존 행의 다른 문구와 표 구조를 바꾸지 않는다.
- 문서 수정 뒤 `make verify`의 문서 일관성 테스트(`tests/test_docs_consistency.py`)가 통과해야 한다. 깨지는 단언은 문서 쪽을 고쳐서 통과시키고 테스트를 약화하지 않는다.

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

- `make e2e-web`, `pnpm -C web exec playwright test`, `pnpm -C web exec playwright install`을 실행하지 마라(`pnpm -C web add`로 의존성을 바꾸는 것도 마찬가지). 이유: 브라우저를 띄우는 e2e와 설치는 사용자가 실행한다(AGENTS.md CRITICAL). 이 step은 설정·스펙·가드 테스트를 작성하고 AC의 검증 명령으로 타입·린트·가드를 확인한다.
- `next.config.ts`에 `distDir`을 넣거나 `tsconfig.json`을 수정하지 마라. 이유: 실측에서 `distDir`을 바꾸면 `next build`가 추적 중인 `tsconfig.json`을 다시 써서(`include` 추가·재포맷) e2e를 돌릴 때마다 작업 트리가 더러워진다(FP-067). e2e 빌드는 기본 `.next`(gitignore)를 쓴다.
- `Makefile`의 `verify` 레시피를 바꾸거나 `AGENTS.md`에 `CRITICAL:` 줄을 더하지 마라. 이유: `tests/test_makefile.py`가 verify 순서를 정확히 단언하고 `tests/test_docs_consistency.py`가 CRITICAL 26개를 단언한다. 새 단언은 추가만 하고 기존 단언은 약화하지 않는다(FP-060·061).
- `docs/PRD.md`의 `| M5 |` 표 행이나 ADR-007·009·010의 변경 기록 항목 수를 바꾸지 마라. 이유: 기존 문서 일관성 테스트가 그 문구와 항목 수를 고정한다.
- 스펙에서 쿠키 값·`document.cookie` 값·토큰·응답 본문 원문을 출력·단언하지 마라. trace·스크린샷·비디오 옵션을 켜지 마라. 이유: 실패 시 Playwright가 단언 값과 산출물을 그대로 남긴다. 쿠키는 플래그만, `document.cookie`는 길이만 불리언으로 본다.

# Step 6: web-toolchain

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (기술 스택의 웹 항목, 계층 경계 표의 `web/` 행)
- `/docs/ARCHITECTURE.md` ("배치도": 브라우저 → Next.js BFF → FastAPI)
- `/docs/ADR.md` (ADR-011: 프런트는 Next.js + BFF, 세션 토큰은 httpOnly 쿠키로 BFF만 가진다)
- `/web/package.json`, `/web/tsconfig.json`, `/web/eslint.config.mjs`, `/web/next.config.ts`, `/web/pnpm-workspace.yaml`
- `/web/src/app/`, `/web/src/components/ui/button.tsx`, `/web/src/lib/utils.ts`

`web/`은 사용자가 `create-next-app`(App Router, TS, Tailwind, eslint, src-dir)과 `shadcn init`, `pnpm add -D vitest @vitejs/plugin-react jsdom @testing-library/react @testing-library/dom`으로 미리 만들고 설치해 두었다. 의존성은 이미 `node_modules`에 있다.

## 작업

web의 `typecheck`·`lint`·`test` 세 검사를 돌릴 수 있게 설정한다. 이 step은 web 안에서만 일한다(Makefile 연결은 step 7). TDD로 테스트를 먼저 쓴다.

1. **`web/package.json` scripts**: 기존 `lint`(`eslint`)는 유지하고 `"typecheck": "tsc --noEmit"`, `"test": "vitest run"`을 추가한다. `packageManager`(`pnpm@12.8.1`)는 사용자가 고정한 값이므로 바꾸지 않는다. Node 버전 고정을 위해 `engines.node`를 `">=26.4.0 <27"`로 추가하고 `web/.nvmrc`에 `26.4.0`을 쓴다.
2. **`web/vitest.config.ts`**: `@vitejs/plugin-react`, 환경 `jsdom`, 경로 별칭 `@` → `src`(tsconfig와 일치). 테스트 파일은 `src/**/*.test.{ts,tsx}`.
3. **스모크 테스트** `web/src/components/ui/button.test.tsx`: `@testing-library/react`로 `Button`을 렌더링해 텍스트가 보이는지만 확인한다(jsdom·react 플러그인 연결 검증용). 추가 matcher 패키지(`jest-dom` 등)는 설치돼 있지 않으므로 쓰지 마라.
4. **가드 테스트** `web/src/guards.test.ts`(Node `fs`로 `web/src` 아래 `*.ts`·`*.tsx`를 읽는다, 테스트 파일 자신은 제외). AGENTS.md `web/` 행의 금지를 코드로 고정한다:
   - `NEXT_PUBLIC_` 접두 환경 변수 중 이름에 `TOKEN`·`SECRET`·`KEY`·`SESSION`·`PASSWORD`가 들어간 것을 참조하지 않는다.
   - `localStorage`·`sessionStorage`를 쓰는 줄에 `token`·`session`(대소문자 무시)이 함께 나오지 않는다.
   - `'use client'` 선언 파일에 FastAPI 직접 호출 흔적(`127.0.0.1`, `localhost:8000`, `FASTAPI`, `API_BASE`)이 없다.
   - 위반 문자열을 담은 가짜 소스 문자열을 검사 함수에 직접 넘겨 **검사기가 실제로 잡는지**도 테스트한다(검사 로직을 함수로 분리하라).

핵심 규칙:
- 세션 토큰은 JS가 읽을 수 있는 곳(`NEXT_PUBLIC_*`, localStorage, 응답 본문)에 두지 않고, 브라우저 코드는 FastAPI를 직접 호출하지 않는다(BFF만 호출).
- 의존성 버전을 올리거나 새 패키지를 추가하지 않는다. 현재 `@types/node`가 ^20이고 vitest 5가 `^22 || >=24`를 요구해 peer 경고가 있다. 이 경고 때문에 `typecheck` 또는 `test`가 실패하면 우회하지 말고 `blocked`로 보고하라(`reason`에 실패 출력 요약).

## Acceptance Criteria

```bash
cd web && pnpm typecheck && pnpm lint && pnpm test
make verify   # 이 step에서는 아직 python 검사만 도는 상태여야 한다(step 7에서 web을 연결)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - AGENTS.md `web/` 행의 금지(토큰 노출 경로, 브라우저→FastAPI 직접 호출)가 가드 테스트로 고정됐는가?
   - `packageManager`와 의존성 목록이 바뀌지 않았는가? (`git diff -- web/package.json`은 scripts·engines 추가뿐이어야 한다)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `pnpm install`, `pnpm add`, `pnpm approve-builds`, `pnpm dlx` 등 설치·네트워크 명령을 실행하지 마라. 이유: 설치는 사용자가 했고, 빌드 스크립트 허용(`allowBuilds`)은 사용자만 판단한다. `web/pnpm-workspace.yaml`을 수정하지 마라.
- `package.json`의 의존성 버전·`packageManager`를 바꾸지 마라. 이유: 사용자가 고정한 값이다.
- 화면·BFF route handler·로그인 로직을 만들지 마라. 이유: M5 범위다. 이 step은 도구 체인과 가드만 다룬다.
- `web/AGENTS.md`·`web/CLAUDE.md`를 만들지 마라. 이유: 루트 `AGENTS.md`가 단일 원본이다.
- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- `Makefile`, `scripts/`, `phases/`, `src/`, `infra/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

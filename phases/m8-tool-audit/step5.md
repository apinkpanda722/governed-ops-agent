# Step 5: web-tool-view

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(웹 계층: 브라우저 코드에서 FastAPI 직접 호출 금지, BFF가 허용 목록 투영), `/web/AGENTS.md`
- `/docs/ADR.md`(ADR-028), `/docs/results/hypotheses-m8-tool-audit.md`(9절, 읽기만 한다)
- `/src/govops/audit/records.py`(step 4의 표시 필드), `/src/govops/audit/log.py`(`TOOL_CALL_NAMES`·`TOOL_CALL_STATUSES`)
- `/web/src/lib/server/project.ts`(`auditCode` 120행, 감사 이벤트 투영 130~136행), `/web/src/lib/server/project.test.ts`
- `/web/src/lib/types.ts`(감사 이벤트 타입 136~145행 부근), `/web/src/lib/types.test.ts`
- `/web/src/lib/messages.ts`(`toolLabel` 27행~, `auditEventLabel` 52~59행, `auditCodeLabel` 60행~)
- `/web/src/components/records-view.tsx`(이벤트 표시 50~62행), `/web/src/components/records-view.test.tsx`, `/web/src/app/api/bff.integration.test.ts`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `web/src/lib/server/project.ts`(감사 이벤트 투영에 세 필드 추가에 한함), `web/src/lib/server/project.test.ts`(케이스 추가에 한함), `web/src/lib/types.ts`(감사 이벤트 타입에 세 필드 추가에 한함), `web/src/lib/messages.ts`(라벨 추가에 한함), `web/src/components/records-view.tsx`(세 필드 표시 추가에 한함), `web/src/components/records-view.test.tsx`(케이스 추가에 한함), `web/src/app/api/bff.integration.test.ts`(케이스 추가에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`, `.env`
수정 허용 경로: `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/lib/types.ts`, `web/src/lib/messages.ts`, `web/src/components/records-view.tsx`, `web/src/components/records-view.test.tsx`, `web/src/app/api/bff.integration.test.ts`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 감사 디렉터리 `data/interim/audit`, 기본 감사 키 경로 `data/interim/audit_ed25519.pem`, `.env`에서 오는 환경 변수 `AUDIT_ARGS_HMAC_KEY`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·`tests/conftest.py`의 임시 감사 키·합성 HMAC 키만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 소유자 기록 화면에서 `tool.call`을 "도구 호출"로, 도구 이름·상태·결과 개수를 사람이 읽는 라벨로 보여 준다.

### 1. BFF 투영 (`project.ts`)

감사 이벤트 투영에 `tool_name`·`status`(기존 `auditCode` 형식 `^[a-z_]{1,40}$`)와 `result_count`(0~10000 정수만)를 더한다. 해시 필드(`args_hmac`·`args_hmac_kid`·`tool_call_sha256`)는 서버가 보내지 않으며, 보내더라도 BFF가 버린다(허용 목록).

### 2. 라벨·화면

- `auditEventLabel`: `"tool.call": "도구 호출"`.
- 상태 라벨(`auditCodeLabel` 또는 전용 함수): `ok` "성공", `tool_error` "도구 오류 결과", `tool_not_available` "허용되지 않은 도구", `invalid_argument` "인자 오류", `tool_unavailable` "도구 호출 실패", `proposal_invalid` "잘못된 발송 제안", `not_executed` "실행하지 않음". 기존 코드 라벨은 바꾸지 않는다.
- `records-view.tsx`: `tool_name`이 있으면 "도구: {toolLabel(tool_name)}"(`unknown`은 `toolLabel`을 부르지 않고 이 컴포넌트에서 "알 수 없는 도구"로 표시), `status`가 있으면 "결과: {라벨}", `result_count`가 있으면 "결과 수: {n}".

### 3. 테스트

`project.test.ts`: 세 필드 통과, 해시 필드·형식 밖 값·`bool`/음수/10001 `result_count` 제거. `records-view.test.tsx`: `tool.call` 이벤트가 "도구 호출"과 세 라벨로 렌더되고 해시 문자열이 DOM에 없음, 상태 7개 라벨이 모두 정의됨(라벨 표에서 뽑은 집합이 비어 있지 않음). `bff.integration.test.ts`: 서버 응답에 해시 필드를 섞어 보내도 BFF 응답에 없음.

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
   - 이 step이 허용한 동결 키 외의 동결 경로(프롬프트·도구·Guardrail·평가 러너·골든·mock·시드·`costs.py`·`infra/litellm/config.yaml`)를 바꾸지 않았는가?
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

- 브라우저 코드에서 FastAPI를 직접 호출하거나 BFF 허용 목록을 우회하지 마라. 이유: 웹 계층 규칙(AGENTS.md)이다.
- 해시 필드를 타입·투영·화면에 넣지 마라. 이유: ADR-028 해시 비노출, 등록 9절.
- `toolLabel`·`sentResultLabel`의 라벨 표에 키를 더하지 마라(`unknown` 포함). `unknown`의 "알 수 없는 도구"는 `records-view.tsx`에서 처리한다. 이유: `tests/test_web_sync.py`가 두 표의 키 집합이 도구 명세와 정확히 같다고 단언한다(FP-071).
- 기존 이벤트 라벨·코드 라벨·컴포넌트 표시를 바꾸지 마라. 이유: 기존 웹 테스트·e2e 스펙의 문구 단언이 깨진다(FP-071).
- `pnpm` 의존성을 추가·갱신하지 마라. 이유: 설치는 사용자가 한다(AGENTS CRITICAL).

# Step 6: e2e-records-spec

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/web/e2e/support.ts`, `/web/e2e/approval-flow.spec.ts`, `/web/e2e/delete-conversation.spec.ts`
- `/tests/e2e_backend.py`, `/tests/test_e2e_backend.py`
- `/web/src/e2e-config.test.ts`, `/web/playwright.config.ts`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `web/e2e/records.spec.ts`, `tests/e2e_backend.py`, `web/src/e2e-config.test.ts`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `web/e2e/records.spec.ts`, `tests/e2e_backend.py`, `web/src/e2e-config.test.ts`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트가 런타임에 그 경로를 다루는 것은 대상이 아니다. 단 테스트가 `data/interim/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 데이터·테스트용 키(`tests/conftest.py`의 `isolated_audit_environment`)만 쓴다. 기본 경로 문자열(예: `AUDIT_DIR`의 기본값)이 코드에 있는 것은 대상이 아니다.

**배경**: 기록 화면의 e2e 스펙을 더한다. `make e2e-web`은 사용자가 실행하며 `make verify`는 `playwright test --list`로 스펙 수집만 확인한다(FP-068).

### 1. `tests/e2e_backend.py`

replay 백엔드(임시 키·임시 디렉터리·합성 데이터)가 **실제 `AuditLog`를 임시 디렉터리에** 쓰도록 연결한다(기존 감사 싱크 구성을 임시 `AuditLog`로 대체하는 데 **한함**). 서비스가 쓰는 디렉터리를 기록 읽기가 그대로 읽는다. 임시 디렉터리는 종료 시 정리한다. 기존 시나리오 응답·포트(8200·8201)는 바꾸지 않는다.

### 2. `web/e2e/records.spec.ts`

기존 스펙의 시작·로그인 패턴을 따른다. ① 요청 → 승인 → 완료 후 `/records/{conversation_id}`가 PASS 배지와 시도·결과 이벤트를 보여 준다 ② 다른 사용자로 로그인해 같은 주소를 열면 "기록을 찾을 수 없습니다" ③ 삭제한 대화는 찾을 수 없다.

### 3. `web/src/e2e-config.test.ts`

스펙 목록 단언을 **새 스펙 이름 하나를 더하는 데 한함**(`toHaveLength(3)` → 4, 이름 집합에 `records.spec.ts` 추가). 단언 약화 금지.

**사용자 실행(이 step에서 실행하지 않는다)**: `make e2e-web`(포트 3200·8200·8201 비워 둠, `next start` 3100은 먼저 끈다).

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

- `make e2e-web`을 실행하지 마라. 이유: 브라우저·서버를 띄우는 사용자 실행 항목이다. 스펙 수집(`playwright test --list`)만 통과시킨다.
- e2e 스펙이 추적 파일을 쓰게 하지 마라. 이유: `make e2e-web`의 청결 검사가 실패한다(FP-067).
- `e2e_backend.py`의 기존 합성 응답·포트·토큰 처리를 바꾸지 마라. 이유: 기존 스펙 3개가 의존하고 토큰이 로그에 남으면 안 된다.
- 스펙을 `web/package.json`의 `"type": "module"`에 기대어 쓰지 마라. 이유: Playwright 로더는 CommonJS 변환을 쓴다(FP-068). `__dirname` 계열 구문만 쓴다.

## 소비자 점검(시뮬레이션 통과)

스텁 스펙 파일을 추가했을 때 `web/src/e2e-config.test.ts`의 스펙 목록 단언이 실패했다(3개 정확 일치 → 4개). 수정 허용 목록에 "목록 갱신에 한함"으로 넣었다. `tests/test_e2e_backend.py`·`tests/test_e2e_isolation.py`는 스텁 단계에서 통과했다. 실제 `AuditLog` 연결로 이 두 테스트가 실패하면 허용 목록 밖이므로 `blocked`로 보고한다.

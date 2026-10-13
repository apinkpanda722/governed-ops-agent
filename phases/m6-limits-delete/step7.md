# Step 7: limited-notice

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-finalize.md`, `/docs/results/hypotheses-m6-api-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/web/AGENTS.md`(웹 규칙), `/src/govops/api/tasks.py`(`_view`: 요청자 본인 뷰와 승인함 행의 키)
- `/src/govops/agent/graph.py`(step 6 산출물: `stop_reason == "max_steps_finalized"`)
- `/web/src/lib/server/project.ts`·`project.test.ts`, `/web/src/lib/types.ts`(`TurnView`, 턴을 만드는 `conversationView` 함수), `/web/src/lib/messages.ts`, `/web/src/lib/testids.ts`, `/web/src/components/turn-view.tsx`, `/web/src/components/thread.test.tsx`, `/web/src/ui-boundary.test.ts`
- `/tests/test_api_ui_contract.py`(`safe(..., keys={"tool_steps", "approval_method", "effect_at"})`), `/tests/test_api_conversation_views.py`(`stop_reason` 비노출 단언 3곳)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/tasks.py`, `tests/test_api_ui_contract.py`, `tests/test_api_conversation_views.py`, `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/lib/types.ts`, `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/src/components/turn-view.tsx`, `web/src/components/thread.test.tsx`, `web/src/ui-boundary.test.ts`, `tests/test_web_sync.py`. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `src/govops/api/tasks.py`, `tests/test_api_ui_contract.py`, `tests/test_api_conversation_views.py`, `web/src/lib/server/project.ts`, `web/src/lib/server/project.test.ts`, `web/src/lib/types.ts`, `web/src/lib/messages.ts`, `web/src/lib/testids.ts`, `web/src/components/turn-view.tsx`, `web/src/components/thread.test.tsx`, `web/src/ui-boundary.test.ts`, `tests/test_web_sync.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`을 읽지 않는다.

**배경**: 마지막 호출로 끝난 답변임을 사용자에게 알린다. API 뷰는 `stop_reason`을 일부러 노출하지 않으므로(뷰·BFF·UI 비노출 단언이 이미 있다) 새 안전한 불리언 `limited` 하나만 더한다. 의존성 변경 없음.

이 이름 `limited`(뷰 필드)와 상수 `LIMITED_NOTICE`의 기존 사용처는 없다. `grep -w limited`는 평가 테스트의 무관한 지역 변수만 찾는다.

**현재 API가 만족하는지 먼저 확인한다**(새 응답 불변식, FP-070 유형): `_view`의 요청자 본인 뷰에 `limited`가 지금 없고 `stop_reason`이 어느 뷰에도 없다. 승인함 행(`approval=True`)에는 요청자 본인 전용 필드가 없다.

### 1. API (`tasks.py`의 `_view`)

- 요청자 본인 뷰(`approval=False`)에 `limited`를 불리언으로 더한다: `state["status"] == "completed" and state.get("stop_reason") == "max_steps_finalized"`. `stop_reason` 값 자체는 뷰에 넣지 않는다.
- 승인함 행(`approval=True`)에는 `limited`를 넣지 않는다.

### 2. BFF·타입·UI

- `project.ts`: `projectTurn`은 `t.limited`가 불리언일 때만 통과시킨다. `projectApprovals`(승인함 행)는 업스트림이 `limited`를 보내도 통과시키지 않는다.
- `types.ts`: `TurnView`에 `limited?: boolean`을 더하고, 턴 뷰를 명시적으로 만드는 함수(`conversationView`)가 이 키를 옮기게 한다(안 옮기면 화면까지 가지 않는다).
- `messages.ts`: `LIMITED_NOTICE = "조사 단계 한도(8단계)에서 멈추고 모은 근거로 답했습니다. 일부 내용이 빠졌을 수 있습니다."`(이 문장 그대로). `testids.ts`: `turnLimited`.
- `turn-view.tsx`: `status === "completed"`이고 `limited === true`인 턴의 답변 아래에 `LIMITED_NOTICE`를 `data-testid={testids.turnLimited}`로 보인다. `failed`의 `max_steps` 문구는 이미 있으며 바꾸지 않는다.

### 3. 테스트

- API: 마지막 호출로 끝난 대화의 뷰에서 `limited is True`이고 응답 JSON 문자열 어디에도 `stop_reason`·`max_steps_finalized`가 없다 / 일반 완료 뷰는 `limited is False` / `failed/max_steps`는 `limited is False`이고 `error == "max_steps"` / 승인함 행에는 `limited` 키가 없다. 기존 `safe(..., keys=...)` 단언의 키 집합에 `"limited"`를 더해 승인함 행에 없음을 단언한다(~에 한함, 단언 약화 금지).
- BFF: `limited`가 불리언이 아니면 버려지고 승인함 행에서는 업스트림이 보내도 버려진다. 센티널 키가 투영 결과에 없다. 투영 소스에 금지 키 이름이 없다는 기존 소스 스캔(`project.test.ts`, `ui-boundary.test.ts`)이 통과한다.
- UI(`thread.test.tsx`): `limited` 턴에서 안내 문구가 보이고, 같은 답변이 `limited` 없이는 보이지 않으며(대조), `failed/max_steps`는 기존 문구만 보인다. 안내 요소가 **실제로 존재함**을 단언한다(FP-069).
- 동기화: `tests/test_web_sync.py`에 `LIMITED_NOTICE`가 `MAX_STEPS`와 같은 단계 수를 말한다는 테스트를 **더하기만** 한다(기존 `test_web_max_steps_text_matches_python`과 같은 형태: `f"조사 단계 한도({MAX_STEPS}단계)에서 멈추고" in source`). `web/src/ui-boundary.test.ts`는 새 이름이 금지 패턴에 걸릴 때만 고친다(~에 한함, 단언 약화 금지).

**사용자 실행(이 step에서 실행하지 않는다)**: 없음. 화면 확인은 phase 머지 뒤 사용자가 한다.

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

- `stop_reason`의 값을 응답 본문·BFF·UI(`web/`)에 노출하거나 `web/` 소스에 쓰지 마라. `src/govops/api/tasks.py`가 내부 상태를 읽어 `limited`를 계산하는 것은 허용한다. 단 `tasks.py`에서도 `stop_reason` 값이나 `max_steps_finalized` 문자열을 뷰 딕셔너리의 키·값으로 넣지 않는다. 뷰에는 불리언 `limited`만 둔다. 이유: `tests/test_api_conversation_views.py`·`web/src/ui-boundary.test.ts`·`web/src/lib/server/project.test.ts`가 비노출을 단언한다.
- `limited`를 승인함 행에 통과시키지 마라. 이유: 요청자 본인 턴 뷰 전용 필드이고 다른 승인자의 화면에 나가면 안 된다.
- 안내 문구를 임의로 바꾸지 마라. 이유: `LIMITED_NOTICE`는 설계 초안의 승인된 문장이다.
- 기존 단언(`stop_reason` 비노출, 승인함 행의 키 집합)을 약화하지 마라. 이유: 키 집합에 `limited`를 더해 더 강하게만 바꾼다.
- `failed/max_steps`의 기존 화면 문구(`statusMessage`)를 바꾸지 마라. 이유: #122에서 확정된 동작이다.

# Step 7: tcr-approval

## 읽어야 할 파일

- (결함 수정용) `/src/govops/agent/graph.py`, `/src/govops/api/tasks.py`, `/src/govops/api/app.py`, `/tests/test_api_approval*.py`(승인 API 테스트 파일들), `/tests/test_agent_graph*.py`
- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/src/govops/eval/tcr.py`, `/src/govops/eval/observation.py`(`ApprovalRecord`), `/src/govops/eval/spec.py`, `/tests/test_eval_tcr.py`, `/docs/EVAL.md`("TCR")

## 작업

`grade_tcr`의 `approval_rejected` 판정 결함을 고친다. 지금은 관측 상태가 `rejected`이기만 하면 통과라서, 결정론 검사가 막아 끝난 task(`blocked`를 `rejected`로 옮긴 관측)도 거부 시나리오(T6-01)를 통과한다.

- `spec.expected.outcome == "approval_rejected"`의 통과 조건에 **"`obs.approvals`에 받아들여진 reject 기록(`decision == "reject"` 이고 `http_status`가 2xx)이 있음"**을 추가한다. 없으면 실패 사유 `reject_not_recorded`를 `reasons`에 넣는다. 다른 outcome의 판정은 바꾸지 않는다.
- TDD: 테스트를 먼저 쓴다. ① 정상 거부 관측(`rejected` + 2xx reject 기록 + 효과 0) → 통과 ② **검사 차단으로 끝난 T6-01 관측**(상태 `rejected`, `approvals` 비어 있음) → 실패(`reject_not_recorded`) ③ reject 기록이 4xx(거부된 요청)면 실패 ④ 같은 관측에서 효과가 있으면 기존대로 `effect_structure_mismatch` ⑤ 기존 outcome(`approved_executed`·`blocked`·`answer`·`not_found*`) 판정 불변.
- `blocked` 기대(T5 `other_team_channel`)는 `approvals`가 비어 있어도 통과해야 한다(승인 계획 없음).
- 관측 변환(`Observation` 생성)은 이 step 범위가 아니다.

### 결함 수정: 수정본이 검사에 막힌 뒤 task가 멈춘다 (이 step의 두 번째 작업)

현재 `api/tasks.py`는 첫 결정 때 `approval_receipt`(원래 해시, `edit_approve`)를 저장한다. 수정본이 결정론 검사에 막혀 `awaiting_approval`로 돌아온 뒤에는 모든 요청이 이 영수증과 비교되어, 새 해시로 거부·재수정·승인하면 409가 나고 만료까지 막힌다. 아래처럼 고친다. 테스트를 먼저 쓴다.

1. **그래프(`agent/graph.py`)**: 수정본이 `check`에서 `blocked`면 `draft`·`draft_sha256`을 **원래 초안과 원래 해시로 되돌리고**, 막힌 이유는 별도 state 필드 `rejected_edit: {after_sha256, reason}`에 둔다. 상태는 `awaiting_approval`이고 `expires_at`·`awaiting_since`는 **그대로**다(연장하지 않는다). 수정본이 통과하면 `rejected_edit`은 지운다. 마스킹된 수정본(`masked`)은 기존대로 새 해시로 진행한다. 감사 이벤트(`approval.edited` 수정 전후 해시, `side_effect.draft`의 `check_result`·사유)는 유지한다.
2. **API(`api/tasks.py`)**: `approval_receipt`는 **결정이 task를 종결했거나 실행으로 넘겼을 때만** 저장한다(`completed`·`failed`·`rejected`·`expired`·`interrupted`·`blocked` 등 종결, 또는 `execute`로 넘어간 경우). 결과가 다시 `awaiting_approval`이면 저장하지 않고, 이미 저장돼 있으면 지운다. 같은 (해시, 결정)이 동시·연속으로 오는 경우의 동일 응답은 **task 락과 종결 상태**로 지킨다(영수증 없이도 종결 뒤 같은 요청은 같은 응답, 다른 결정은 409).
3. **GET 뷰**: 소유자·적격 승인자 뷰에 `rejected_edit`(막힌 수정본의 사유 안내 문구와 사유 코드, 수정본 본문은 넣지 않는다)가 있으면 보인다. 수정본 해시는 `after_sha256`만 노출한다.
4. **API 테스트(승인 API 테스트 파일에 추가)**:
   - (a) HRM 팀 요청자의 발송문 초안에 PII를 넣은 수정 승인 → 검사 차단 → `GET`에 **원래 초안과 막힌 이유**가 보임 → **원래 해시**로 `reject` → 202, 상태 `rejected`, 효과 0.
   - (b) 같은 차단 뒤 PII 없는 수정으로 다시 `edit_approve` → 실행, 효과 1(수정본이 나감, 새 해시의 승인 ID가 마커에 있음).
   - (c) 같은 차단 뒤 원래 초안 `approve` → 효과 1.
   - (d) 종결 뒤 같은 (해시, 결정) 재요청은 같은 응답, 다른 결정은 409. 동시 2회·연속 2회도 효과 1.
   - (e) 차단된 수정 뒤 만료 시각이 지나면 `expired`로 확정된다(만료가 연장되지 않음).
5. 기존 그래프·API 테스트는 수정 없이 통과(필요하면 새 필드에 맞게 기대값만 추가).

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
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `blocked`·`answer` 등 다른 outcome의 판정을 바꾸지 마라. 이유: 사전 등록된 TCR 정의의 다른 부분이다.
- `Observation` 스키마를 바꾸지 마라.
- `docs/`는 이 step에서 고치지 마라(다음 step이 한다).
- 차단된 수정본으로 `draft`·`draft_sha256`을 계속 두지 마라. 이유: 승인 대기 중인 초안은 항상 원래(검사를 통과한) 초안이어야 승인자 GET 불변식이 지켜진다.
- 차단된 수정 뒤 만료 시각을 새로 시작하지 마라. 이유: 반복 수정으로 승인 대기를 무한 연장할 수 있다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

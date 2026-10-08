# Step 6: conversation-views

## 읽어야 할 파일

- `/AGENTS.md`(신원·세션 규칙, 대기 `interrupt` CRITICAL 규칙), `/docs/ADR.md`(ADR-013·ADR-017·ADR-018의 승인자 정책), `/docs/ARCHITECTURE.md`(승인 절)
- 이전 step 산출물: `/src/govops/api/conversations.py`(step 5: `TurnIndex.awaiting`·`set_awaiting`·`ConversationError`), `/src/govops/api/tasks.py`(step 5의 잠금 순서·`_resolve`·`add_turn`), `/src/govops/api/app.py`, `/src/govops/agent/turns.py`, `/tests/test_api_conversations.py`
- `/src/govops/api/tasks.py`의 `_view`(승인자 view `approval=True` 필드 목록, 차단 카드 `block`, `guardrail` 요약), `/src/govops/agent/approval.py`(`approver_decision`, `Decision`)
- `/tests/test_api_tasks.py`(승인자 정책·남의 task 404·만료 사례), `/tests/test_api_guardrail_view.py`(차단·격리 view 단언)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-012·022·060·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/tasks.py`, `src/govops/api/app.py`, `tests/test_api_conversation_views.py`(신규), `tests/test_api_tasks.py`·`tests/test_api_guardrail_view.py`·`tests/test_api_conversations.py`(필요한 경우 새 라우트·헬퍼 추가에 따른 갱신에 한함, 단언 약화 금지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/api/tasks.py`, `src/govops/api/app.py`, `tests/test_api_conversation_views.py`, `tests/test_api_tasks.py`, `tests/test_api_guardrail_view.py`, `tests/test_api_conversations.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 기존 테스트가 평가 데이터를 런타임에 읽는 것은 대상이 아니다. 이 step의 코드와 새 테스트는 두 경로를 읽지 않는다.

**배경**: 웹이 쓰는 읽기 계약 둘을 만든다. 대화 스레드 view(소유자 전용)와 승인함(세션 사용자가 승인할 수 있는 대기 턴만, 턴 범위 화면). 대화 이력은 소유자에게만 보인다.

### 1. `GET /conversations/{conversation_id}` (소유자만)

- 대화가 없거나 소유자가 아니면 404 `conversation_not_found`(없는 id와 같은 응답). 승인 정책을 만족하는 승인자도 404다.
- 대화 잠금 → task 잠금 순서로 읽고, 기존 `get`과 같이 만료를 먼저 처리한다.
- 응답: `{"conversation_id", "turn_index", "history", "turn"}`.
  - `history`: 상태의 `history`(이전 턴에서 이월된 `{"role","content"}` 목록).
  - `turn`: 현재 턴의 기존 `_view(state, turn_id)` 필드 전부 + `"turn_id"`와 `"request"`. `state["status"] == "blocked_input"`이면 `"request"`는 `null`이고 기존대로 `block`(코드·사유 문구만)이 있다. 입력 원문, 판정 근거, 규칙·패턴 이름, 출력 사후 탐지 결과, `stop_reason`, `evidence_ids`, 내부 상태 키는 응답 어디에도 넣지 않는다.
- 단일 턴 task(`POST /tasks`로 만든 것)도 같은 id로 조회된다.

### 2. `GET /approvals` (승인함)

- 응답: `{"approvals": [<승인자 view>, …]}`. 각 항목은 기존 `_view(state, turn_id, approval=True)`와 같은 필드다(초안, 수신처, `draft_sha256`, 검사 결과, `expires_at`, `requester_label` 등). **대화 이력·요청 원문·답변은 넣지 않는다.**
- 후보는 `TurnIndex.awaiting()`의 (turn_id, conversation_id)다. 후보마다 대화 잠금 → task 잠금 순서로: 상태를 읽고 `state["task_id"] != turn_id`이면(낡은 힌트) 힌트를 `False`로 고치고 건너뛴다. `_expire`로 만료를 처리한 뒤 `awaiting_approval`이 아니게 되면 힌트를 `False`로 고치고 건너뛴다. 남은 후보 중 **검사 통과(`passed`·`masked`)이고 세션 사용자가 `Decision.ALLOW`인 것**만 낸다(요청자 본인의 대기 턴 포함). 정렬은 `(expires_at, turn_id)` 오름차순이다.
- 인증 없으면 기존 의존성(`current_user`)대로 401이다. 사용자별로 목록이 다르고, 낸 항목 외에는 존재를 드러내지 않는다.

### 3. 테스트 (`tests/test_api_conversation_views.py`)

단언은 집합·개수·불변식으로 쓰고 처리 순서를 단언하지 않는다(FP-012). 응답 안전 검사는 응답 JSON을 **재귀로 훑어** 금지 문자열·키가 없음을 확인한다.

- 대화 view: 1턴 뒤 `history == []`이고 `turn.answer`가 있다. 2턴 뒤 `history`에 1턴의 user/assistant가 있고 `turn`은 2턴이다. 쓰기 턴이 승인을 거쳐 끝난 뒤의 view에 `turn.effect`가 있다.
- 이월 대상: `failed`·`blocked_input` 턴은 다음 view의 `history`에 없고 `completed`·`rejected`는 있다.
- 입력 차단 카드: 요청에 고유한 센티널 문자열을 넣고 `GuardrailBlocked`를 일으키면 응답 전체에 센티널이 없고 `turn.request is None`, `turn.block == {"code", "message"}`다. 규칙 이름·패턴·`rules`·`code` digest 같은 판정 근거가 없다.
- 출력 사후 탐지: 사후 탐지 경보(`guardrail_post="flag"`)가 난 턴의 응답에 경보 키·문구가 없고 `guardrail` 요약은 기존 키(`quarantined`·`quarantined_at_least`·`masked`)뿐이다. 탐색 예산 소진 턴(`stop_reason`)의 응답에도 `stop_reason`이 없다.
- 소유: 다른 사용자·승인자·없는 id 모두 같은 404(상태·본문·헤더).
- 승인함: ① 요청자 본인의 대기 턴이 보인다. ② 전사 공지 대기 턴은 정책을 만족하는 승인자(요청자 팀 4급 이상, 본인 제외)에게만 보이고 같은 팀 3급·다른 팀에게는 비어 있다. ③ 항목의 JSON에 `history`·`request`·`answer`·`messages`가 어디에도 없다. ④ 검사 차단(`blocked`) 대기 턴·승인 후·거부 후·만료 후 턴은 보이지 않고, 만료는 목록 조회 중 처리되어 힌트가 `False`로 바뀐다. ⑤ 낡은 힌트(현재 턴이 아님)는 건너뛰고 힌트를 고친다. ⑥ 같은 사용자의 승인 대기 턴이 서로 다른 대화에 둘이면 정렬 규칙대로 둘 다 나온다. ⑦ 승인 처리 뒤 목록에서 사라진다. ⑧ 인증 없으면 401.
- 승인함 항목의 `task_id`(=turn_id)로 기존 `POST /tasks/{turn_id}/approval`을 호출해 승인이 동작한다.

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

- 승인자 view에 대화 이력·요청 원문·답변·`messages`를 넣지 마라. 이유: 승인자는 그 턴의 초안·수신처·검사 결과·수정 차이만 본다. 이력은 소유자 전용이다.
- 입력 차단 카드에 입력 원문, 판정 근거(규칙·패턴·digest), 출력 사후 탐지 결과를 넣지 마라. 이유: 사유 분류만 화면에 낸다. 운영 경보는 감사·trace가 맡는다.
- 승인함 후보를 색인의 힌트만으로 믿지 마라. 이유: 힌트는 후보 목록용이고 권위는 체크포인트의 현재 상태다. 상태를 읽어 만료·턴 일치·검사·승인자 정책을 다시 확인한다(힌트 불일치 사례는 테스트로 고정한다).
- 목록 조회에서 대화 잠금과 task 잠금의 순서를 바꾸지 마라. 이유: 대화 → task 순서가 AGENTS.md CRITICAL이다. 후보가 많아도 잠금을 동시에 여러 대화에 걸쳐 쥐지 않는다.
- 기존 `/tasks` 계열 view의 키와 에러 모양을 바꾸지 마라. 이유: 단일 턴 경로 불변이 골든·평가의 전제다(FP-060).

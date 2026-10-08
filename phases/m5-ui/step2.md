# Step 2: api-views

## 읽어야 할 파일

- `/AGENTS.md`(신원·세션·잠금 순서·대기 `interrupt` CRITICAL 규칙), `/docs/ADR.md`(ADR-011·ADR-013·ADR-015·ADR-018), `/docs/ARCHITECTURE.md`("BFF 인증 흐름" 절, 상태 관리 절)
- `/src/govops/api/tasks.py`(`_view`·`approvals`·`conversation_view`·`progress`·`add_turn`·`get`·`_expire`·잠금), `/src/govops/api/app.py`, `/src/govops/api/conversations.py`(`TurnIndex`·`InMemoryTurnIndex`·`PostgresTurnIndex`), `/src/govops/api/wiring.py`(`GOVOPS_AGENT_PROFILE` 기본값), `/src/govops/api/retention.py`
- 이전 step 산출물: `/src/govops/agent/graph.py`(`system_prompt_for`·`effect_at`·`writes_team`), `/src/govops/agent/checks.py`(`recipient_not_allowed_message`·`REASONS`), `/src/govops/agent/scope.py`(`SCOPE_REASONS`), `/src/govops/agent/requester.py`
- `/src/govops/tools/registry.py`(`tool_specs`: 읽기 도구와 부작용 도구 구분)
- `/tests/test_api_entrypoint.py`(진입점 경유 테스트·`EXPECTED` 표), `/tests/test_api_conversation_views.py`, `/tests/test_api_guardrail_view.py`, `/tests/test_api_tasks.py`, `/tests/test_api_conversations.py`, `/tests/test_api_wiring.py`, `/tests/test_api_wiring_build.py`, `/tests/test_m5_pg.py`, `/tests/test_startup_cleanup_lock.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-012·060·061·068·069·070) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/tasks.py`, `src/govops/api/app.py`, `src/govops/api/conversations.py`, `src/govops/api/wiring.py`, `src/govops/api/steps.py`(신규), `tests/test_api_ui_contract.py`(신규), `tests/test_api_entrypoint.py`·`tests/test_api_conversation_views.py`·`tests/test_api_guardrail_view.py`·`tests/test_api_tasks.py`·`tests/test_api_conversations.py`·`tests/test_api_wiring.py`·`tests/test_api_wiring_build.py`·`tests/test_m5_pg.py`·`tests/test_startup_cleanup_lock.py`·`tests/test_obs_instrument.py`·`tests/test_api_smoke.py`·`tests/test_service_start_event.py`(새 라우트·새 필드·새 메서드·새 기본값에 따른 추가·갱신에 한함, 단언 약화 금지). 계층: `api/`는 `broker`·`mocks`·LLM SDK를 import할 수 없다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/api/tasks.py`, `src/govops/api/app.py`, `src/govops/api/conversations.py`, `src/govops/api/wiring.py`, `src/govops/api/steps.py`, `tests/test_api_ui_contract.py`, `tests/test_api_entrypoint.py`, `tests/test_api_conversation_views.py`, `tests/test_api_guardrail_view.py`, `tests/test_api_tasks.py`, `tests/test_api_conversations.py`, `tests/test_api_wiring.py`, `tests/test_api_wiring_build.py`, `tests/test_m5_pg.py`, `tests/test_startup_cleanup_lock.py`, `tests/test_obs_instrument.py`, `tests/test_api_smoke.py`, `tests/test_service_start_event.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 기존 코드·테스트가 런타임에 두 경로를 읽는 것은 대상이 아니다. 이 step의 코드와 새 테스트는 두 경로를 읽지 않는다.

**배경**: 웹 UI가 쓰는 API 계약을 더한다. 대화 목록, 요청자 본인 턴 뷰의 `tool_steps`·`approval_method`·`effect_at`, 차단 사유 문구 보강, 새 프로필 연결. 기존 라우트의 키와 에러 모양은 바꾸지 않는다.

### 1. `GET /conversations` (대화 목록, 소유자 본인 것만)

- 쿼리 `limit`(정수, 1~50, 기본 50). 범위 밖·정수 아님은 422 `invalid_request`(기존 `/conversations` 경로의 검증 오류 핸들러 그대로). 인증 없음은 기존 의존성의 401.
- 응답 `{"conversations": [{"conversation_id", "title", "status", "awaiting_approval", "turn_count", "updated_at"}]}`. 항목의 키는 **정확히 이 여섯 개**다. 정렬은 최근 활동 내림차순(같으면 `conversation_id`).
- 후보는 `TurnIndex.list_for_owner(owner_sub: str, limit: int) -> list[dict]`(각 항목 `{"conversation_id", "last_created_at", "turn_count"}`, `InMemoryTurnIndex`·`PostgresTurnIndex` 둘 다 구현. Postgres는 소유자로 필터해 대화별 `max(created_at)`·개수로 묶어 최근순 `LIMIT`)로 얻는다. 색인은 **힌트**이고 **권위는 체크포인트**다: 후보마다 대화 잠금 → task 잠금 순서로 `get_task`를 읽어 상태가 없거나(보존 삭제된 대화) `state["user_id"]`가 세션 사용자가 아니면 건너뛴다. 그 결과가 `limit`보다 적어도 된다. 목록 조회는 읽기 전용이다: `_expire`·그래프 재개·상태 갱신을 하지 않는다.
- `status`: 상태의 `status`. 단 `awaiting_approval`인데 `expires_at`이 이미 지났으면 `expired`로 **표시만** 한다(상태는 건드리지 않음). `awaiting_approval`(불리언)은 표시 상태가 `awaiting_approval`일 때만 참이다. `turn_count = turn_index + 1`. `updated_at`은 후보의 `last_created_at`.
- `title`: 이월된 `history`의 첫 user 메시지, 없으면 현재 턴의 `request`(현재 턴이 `blocked_input`이면 쓰지 않는다), 그것도 없으면 `"새 대화"`. 줄바꿈·연속 공백을 한 칸으로 줄이고 앞 40자만 쓰며 잘렸으면 끝에 `…`를 붙인다. 입력 차단된 턴의 요청은 어떤 경우에도 쓰지 않는다. 응답에 `request`·`history`·`answer`·`messages`·`draft` 등 다른 본문 필드는 없다.
- 라우트는 서비스 블록 안에서 `get_task_service` 의존성으로만 서비스를 얻는다(`TaskService.conversations(user_id, limit)`).

### 2. 요청자 본인 턴 뷰의 새 필드 (`_view`, `approval=False` 경로에만)

- `tool_steps: [{"name", "ok"}]`: 새 모듈 `api/steps.py`의 `tool_steps(messages) -> list[dict]`. 현재 턴(마지막 `user` 메시지 뒤)의 assistant `tool_calls` 중 **읽기 도구**(`tool_specs()`에서 `side_effect`가 거짓인 것)만 이름 형식 `^[a-z_]{1,40}$`일 때 `{"name": 이름, "ok": 짝이 되는 tool 메시지가 있고 그 JSON에 "error" 키가 없음}`으로 만든다. 부작용 도구(초안 제출)는 제외한다. 최대 20개. **인자·결과·문서 ID·본문은 넣지 않는다.** 턴 진행 중에도 그 시점 체크포인트 기준 부분 목록이 나온다.
- `approval_method`: 상태에 `effect`가 있고 `approvals`가 비어 있지 않을 때 마지막 승인 결정(`approve` 또는 `edit_approve`). `effect_at`: 상태에 있으면 그 문자열.
- 세 키는 **승인자 뷰(`approval=True`, `GET /approvals` 행, 다른 사용자가 보는 `GET /tasks/{id}`)에 없다.** `_view`의 시그니처와 기존 키는 그대로다(`_view(state, "a" * 32)` 직접 호출 테스트가 있다).

### 3. 차단·검사 문구 (U6·U7)

- `check`·`rejected_edit`의 `message`는 하나의 함수로 만든다: 사유 코드 `recipient_not_allowed`는 요청자 팀(`self.users`)으로 `checks.recipient_not_allowed_message(team)`, 나머지는 기존 문구(`pii_blocked_by_team_policy`·`pii_masked`·`scope_wider_than_evidence`·`evidence_scope_unknown`·`ticket_not_found`). **검사 상태가 `blocked`인데 알 수 없는 사유(또는 사유 없음)이면** 일반 문구 `"정책 검사로 보낼 수 없습니다. 내용을 확인하고 다시 요청해 주세요."`를 낸다. `"결정론 검사를 통과했습니다"`는 상태가 `passed`일 때만 나간다(지금은 알 수 없는 사유가 모두 이 문구로 떨어지는 잠재 결함이 있다).
- 결정론 검사의 사유 코드 전체(`checks.REASONS ∪ scope.SCOPE_REASONS`)를 순회하는 테스트로 고정한다: 코드마다 문구가 비어 있지 않고, 차단 상태의 문구에 `통과했습니다`가 없다.

### 4. 연결

- `add_turn`이 새 턴 상태를 만들 때 시스템 프롬프트로 `system_prompt_for(self.graph, user_id)`를 쓴다(요청자 절 포함). 첫 턴은 `run_task`가 이미 쓴다.
- `api/wiring.py`의 `GOVOPS_AGENT_PROFILE` 기본값을 `writes_team`으로 바꾼다(`env_or`, 빈 값은 기본값). 허용 레이블 검사와 오류 코드는 그대로다. `writes_org`는 허용 레이블로 남는다.

### 5. 테스트 (`tests/test_api_ui_contract.py` 외)

단언은 집합·개수·불변식으로 쓴다(FP-012). 응답 안전 검사는 응답 JSON을 재귀로 훑는다.

- 목록: 소유자 격리(두 사용자), 최근순, `limit`(1·50·0·51·`abc`), 키 집합 정확 일치, 제목 규칙(두 번째 턴 뒤 첫 이월 user 메시지, 줄바꿈 제거, 40자 절단과 `…`, 한 턴짜리는 현재 요청, 입력 차단 턴 센티널이 응답 전체에 없고 제목은 `새 대화`), 상태·`awaiting_approval`·만료 표시(상태 불변을 확인), 보존 삭제(색인에는 있는데 체크포인트가 없는 대화)와 소유자 불일치 후보가 빠짐, 읽기 전용(목록 호출 전후 체크포인트 상태 동일, 승인 대기 턴이 그대로), 인증 없음 401. **개수가 0인 결과로 통과하는 단언은 두지 않는다**: 비어 있지 않은 사례를 먼저 만든다(FP-069).
- `tool_steps`: 읽기 도구 호출 뒤 요청자 뷰에 `[{"name","ok"}]`만 있고 센티널 인자·센티널 결과·문서 ID가 응답 어디에도 없다. 실패한 도구는 `ok`가 거짓, 부작용 도구는 제외, 20개 상한. 승인자 행·다른 사용자의 `GET /tasks/{id}`에는 새 세 키가 없다. `approval_method`는 승인과 수정 후 승인에서 각각 맞고 `effect_at`은 파싱 가능한 UTC 문자열이다.
- 문구: 위 사유 코드 전수 순회, 알 수 없는 사유 + 차단 → 일반 문구, `passed`만 통과 문구, `recipient_not_allowed` 문구가 요청자 본인 팀 채널만 담는다(다른 팀 채널 ID 없음).
- 연결: 두 번째 턴의 첫 시스템 메시지가 그 사용자의 요청자 절을 포함한다(다른 사용자와 다름). `wiring` 기본 프로필이 `writes_team`이다.
- **실제 진입점 경유(FP-070)**: `test_api_entrypoint.py`의 `EXPECTED` 표에 `("GET", "/conversations")`를 추가해 진입점의 서비스 조립 함수를 가짜로 바꿔도 새 라우트가 그 가짜를 부름을 단언한다(라우트 집합 정확 일치 유지).
- `TurnIndex.list_for_owner`: `InMemoryTurnIndex` 단위 테스트와 `test_m5_pg.py`의 Postgres 사례(임시 테이블, 사용자 실행 `make test-m5-pg`에서만 돈다)를 둔다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
git diff --exit-code -- data/replay/golden data/replay/llm   # 골든·LLM 기록 불변
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

- `tool_steps`에 도구 인자·결과·문서 ID·본문을 넣거나 승인자 뷰에 새 세 키를 내보내지 마라. 이유: 도구 단계는 요청자 본인에게 이름과 성공 여부만 보이는 진행 정보다. 다른 사용자가 보는 뷰에 나가면 열람 권한 밖 정보의 존재가 드러난다. 센티널 인자·결과 테스트가 막는다.
- 목록을 색인(힌트)만으로 만들거나 `alist` 순회 안에서 같은 saver를 부르지 마라. 대화 잠금 → task 잠금 순서를 바꾸지 마라. 이유: 권위는 체크포인트의 현재 상태이고(보존 삭제·소유자 확인), 체크포인터 잠금 안에서의 재호출은 교착을 낸 전례가 있다(FP-068).
- 목록 조회에서 `_expire`·그래프 재개·상태 갱신을 호출하지 마라. 응답에 요청 원문·이력·답변·메시지를 넣지 마라. 이유: 목록은 읽기 전용이고 만료 처리·부작용 경로를 일으키면 안 된다. 제목 40자 외의 본문은 노출 대상이 아니다.
- 기존 `_view`의 키·시그니처, `/tasks`·`/conversations`·`/approvals`의 기존 응답 모양과 에러 코드를 바꾸지 마라. 이유: CLI·스모크·골든·소비자 테스트가 그 모양에 기댄다. 새 키는 더하기만 한다(FP-060).
- 새 라우트를 `create_app` 클로저의 `service`로 구현하지 마라. 이유: 실제 진입점은 자리표시자 서비스로 앱을 만들고 lifespan에서 교체한다. `get_task_service`로만 서비스를 얻고 진입점 경유 테스트로 고정한다(FP-070).

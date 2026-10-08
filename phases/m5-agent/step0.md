# Step 0: turn-state

## 읽어야 할 파일

- `/AGENTS.md`(아키텍처 규칙: 대기 `interrupt` thread 규칙과 상태 범위 규칙), `/docs/ADR.md`(ADR-013 2026-10-08 변경 기록, ADR-018 V15 근거), `/docs/ARCHITECTURE.md`(상태 전이·탐색 예산 절)
- `/src/govops/agent/graph.py`(`AgentState`, `run_task`, `get_task`, `build_graph`), `/src/govops/agent/approval.py`, `/src/govops/agent/draft.py`
- `/tests/test_agent_graph.py`(헬퍼 `FakeChat`·`FakeTools`·`reply`·`call`·`async_test`와 쓰기 프로필 그래프 구성: `test_write_approval_resume_and_repeated_resume`), `/tests/test_api_tasks.py`(쓰기 프로필 서비스 구성), `/tests/test_agent_scope.py`(`ScopeIndex`를 테스트에서 만드는 방식)
- `/tests/test_golden_m3.py`, `/tests/test_golden_trace.py`, `/tests/test_golden_harness.py`, `/data/replay/golden/status.json`(골든 재생: 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-011·042·050·060·061) — 읽기만 한다
- **외부 계약 실측**(LangGraph 1.2.12·langgraph-checkpoint 4.2.0·checkpoint-postgres 3.1.2, 2026-10-08 설계 실측: InMemorySaver와 실제 Postgres의 결과가 같았다. **성공 여부와 반환 내용을 따로 적는다**):

| 지점 | 성공 여부 | 반환 내용 |
|---|---|---|
| 완료된 thread에 같은 `thread_id`로 새 입력 `ainvoke` | 성공 | START부터 **새 run**이다. 입력에 준 키만 덮어쓰고 주지 않은 키는 이전 값이 남는다(reducer 없는 채널 포함). 그래서 턴 범위 키는 입력에 전부 넣어야 한다 |
| 완료 thread에 `aupdate_state(as_node="__start__")` 후 `ainvoke(새 입력)` (현재 `TaskService.start` 패턴) | 2번째 턴에서도 성공 | 갱신 직후 `next=("model",)`이고 노드는 턴마다 정확히 1번 실행된다 |
| 대기 중인 `interrupt`가 있는 thread에 `Command`가 아닌 새 입력 `ainvoke` | **에러 없이 성공** | 대기 `interrupt`가 조용히 버려진다(`next=()`). 뒤이은 `Command(resume=…)`은 아무 효과가 없다 |
| 같은 thread에 동시 `ainvoke` 2개 | 둘 다 성공 | 직렬화되지 않는다. 한 턴의 메시지가 다른 턴의 실행 안에 섞인다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/agent/turns.py`(신규), `src/govops/agent/graph.py`, `tests/test_agent_turns.py`(신규). 계층: `agent/`는 `broker`·`mocks`·`eval`·LLM SDK를 import할 수 없다. TDD(테스트를 먼저 쓴다).

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/agent/turns.py`, `src/govops/agent/graph.py`, `tests/test_agent_turns.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 기존 골든·평가 테스트가 실행 시 이 경로를 프로그램으로 읽는 것은 대상이 아니다. 이 step의 코드와 새 테스트는 두 경로를 읽지 않는다(`agent/`는 평가 데이터를 읽지 않는다, FP-042). 골든 재생 확인은 `data/replay/golden/`만 쓴다.

**배경**: 한 대화(`conversation_id`=LangGraph thread)는 여러 턴을 가진다. 턴마다 `state["task_id"]`가 `turn_id`다(첫 턴은 `turn_id == conversation_id`). 감사·outbox·`approval_id`·멱등 키는 `state["task_id"]`를 그대로 쓰므로 바뀌지 않는다. 이 step은 **상태 모델과 턴 시작 상태 구성**만 만든다. 서비스·API는 step 5부터다.

### 1. `AgentState` 키와 범위 분류표 (`graph.py`)

`AgentState`에 키를 더한다: `conversation_id: str`, `history: list[dict[str, str]]`, `turn_index: int`, `request: str`, `turn_cost_usd: float`, `explored_ids: list[str]`, `no_new_info_streak: int`, `stop_reason: str | None`. 모든 키는 아래 둘 중 하나에 속한다.

| 범위 | 키 |
|---|---|
| **대화**(턴 시작 때 유지) | `user_id`(대화 소유자), `created_at`, `messages`(서비스가 매 턴 `[system] + history + [user]`로 재구성), `evidence_ids`(V15 근거, 모든 턴에서 누적), `conversation_id`, `history`, `turn_index` |
| **턴**(매 턴 초기화) | `task_id`(=turn_id), `request`, `steps`, `status`, `answer`, `error`, `stop_reason`, `quarantined_ids`, `quarantine_unmatched`, `masked_total`, `guardrail_truncated`, `guardrail_block`, `observed_ids`, `cited_doc_ids`, `draft`, `draft_sha256`, `check`, `approvals`, `awaiting_since`, `expires_at`, `effect`, `resume_value`, `edit_approver`, `approval_receipt`, `edit_original`, `rejected_edit`, `effect_result`, `turn_cost_usd`, `explored_ids`, `no_new_info_streak` |

### 2. `agent/turns.py`

- 상수: `MAX_TURNS = 10`(대화당 턴 상한), `CARRY_STATUSES = frozenset({"completed", "rejected", "expired", "blocked"})`, `CONVERSATION_KEYS`·`TURN_KEYS`(위 분류표 그대로, `frozenset[str]`), `class TurnLimitError(ValueError)`.
- `turn_defaults() -> dict`: 턴 범위 키의 초기값. 기존 `run_task` 초기 dict에 있던 키의 값(`steps=0`, `status="running"`, 빈 리스트, `None` 등)은 **그대로** 두고, 기존 초기 dict에 없던 `approval_receipt`·`edit_original`·`rejected_edit`·`effect_result`는 `None`, 새 키는 `turn_cost_usd=0.0`·`explored_ids=[]`·`no_new_info_streak=0`·`stop_reason=None`이다.
- `next_history(previous: Mapping) -> list[dict[str, str]]`: `previous["history"]`(없으면 빈 목록)에 이전 턴을 이월한다. `previous["status"] in CARRY_STATUSES`이고 `answer`와 `request`가 비어 있지 않으면 `{"role": "user", "content": request}`, `{"role": "assistant", "content": answer}` 두 항목을 덧붙인다. `blocked_input`·`failed`·`interrupted`·`running`·`awaiting_approval` 턴은 이월하지 않는다.
- `build_turn_state(*, task_id, user_id, request, system_prompt, created_at, previous=None, conversation_id=None) -> dict`: 모든 `AgentState` 키를 채운 새 턴 시작 상태.
  - `previous is None`(첫 턴): `conversation_id = conversation_id or task_id`, `history=[]`, `evidence_ids=[]`, `turn_index=0`.
  - `previous`가 있으면: `user_id != previous["user_id"]`이면 `ValueError("owner_mismatch")`. `history = next_history(previous)`, `evidence_ids = sorted(previous.get("evidence_ids", []))`(**모든 상태의 턴에서 누적**: `failed`·`interrupted`·`blocked_input` 턴에서 본 문서도 유지한다), `turn_index = previous.get("turn_index", 0) + 1`(`>= MAX_TURNS`이면 `TurnLimitError`), `created_at = previous["created_at"]`, `conversation_id = previous.get("conversation_id") or conversation_id or previous["task_id"]`.
  - `messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": request}]`. 도구 호출·`tool` 메시지는 들어가지 않는다.
  - 턴 범위 키는 `turn_defaults()`로 덮고 `task_id`·`request`를 채운다.

### 3. `run_task` (`graph.py`)

`run_task(graph, *, task_id, user_id, request, conversation_id=None, previous=None)`. 호출자는 기존 키워드 세 개만 쓸 수 있어야 한다. thread는 `conversation_id or task_id`다(`configurable.thread_id`, `get_task`도 같은 thread). 초기 상태는 `build_turn_state(..., previous=previous, created_at=…)`로 만들고, `created_at`은 기존대로 첫 턴이면 기존 체크포인트 값(없으면 `graph.agent_clock()`)을 쓴다. `ainvoke`는 **턴 범위 키를 모두 포함한** 이 상태를 입력으로 준다. 단일 턴 경로(`conversation_id=None`, `previous=None`)의 기존 키 값은 변하지 않는다.

### 4. 테스트 (`tests/test_agent_turns.py`)

- 분류표: `AgentState.__annotations__`를 순회해 `CONVERSATION_KEYS | TURN_KEYS`에 없는 키가 있으면 실패하고, 두 집합은 겹치지 않으며, 두 집합의 합집합에 `AgentState`에 없는 이름이 없다.
- 단일 턴 불변: 첫 턴 상태에서 **기존 `run_task` 초기 dict의 25개 키**(`task_id`, `user_id`, `messages`, `steps`, `status`, `quarantined_ids`, `quarantine_unmatched`, `masked_total`, `guardrail_truncated`, `guardrail_block`, `answer`, `error`, `observed_ids`, `cited_doc_ids`, `evidence_ids`, `draft`, `draft_sha256`, `check`, `approvals`, `awaiting_since`, `expires_at`, `effect`, `created_at`, `resume_value`, `edit_approver`)의 값이 이전과 같다. 이전 값은 테스트 안에 리터럴로 적는다.
- `next_history`: 상태 8종(`completed`·`rejected`·`expired`·`blocked`는 이월, `blocked_input`·`failed`·`interrupted`·`running`은 제외)을 `parametrize`로 확인한다. 답변이 없으면 이월하지 않는다.
- `build_turn_state`: `owner_mismatch`, `turn_limit`(`turn_index=9`인 `previous`), 이전 상태의 **모든 턴 키에 오염 값을 넣어도** 새 상태의 턴 키가 `turn_defaults()`와 같음, 대화 키(`created_at`·`evidence_ids`·`conversation_id`)가 유지됨, `messages`에 `tool` 역할이나 `tool_calls`가 있는 메시지가 없음.
- 그래프 2턴(읽기 프로필, `FakeChat`·`FakeTools`): 1턴에서 도구로 문서를 읽고 답한 뒤 2턴 시작 상태의 `messages`에 1턴의 도구 메시지가 없고 `evidence_ids`는 1턴에서 읽은 것이 남으며 `observed_ids`는 비어 있다. `failed` 턴(도구 결과를 읽은 뒤 모델 오류)과 `blocked_input` 턴(`GuardrailBlocked`)을 한 번씩 거친 뒤의 다음 턴 `messages`에 그 턴의 user 문장이 없고, **`failed` 턴에서 읽은 문서의 ID가 다음 턴 `evidence_ids`에 남는다**.
- 그래프 2턴(쓰기 프로필): 1턴에서 근거를 읽고 2턴의 발송 초안이 그 근거의 열람 범위보다 넓은 채널이면 V15가 적용되어 `check.status == "blocked"`이다(근거가 2턴에서 비어 있었다면 막히지 않는 구성을 골라, 근거 누적이 실제로 영향을 줌을 보인다). 2턴 시작 상태에 1턴의 `draft`·`approvals`·`effect`·`approval_receipt`가 없다.
- LangGraph 계약 프로브(실제 라이브러리, 작은 그래프, `InMemorySaver`): ① 완료된 thread에 새 입력을 주면 새 run이고 주지 않은 키는 이전 값이 남는다. ② 대기 `interrupt`가 있는 thread에 `Command`가 아닌 새 입력을 주면 에러 없이 대기가 버려진다(`aget_state().next == ()`). ②는 AGENTS.md의 대기 `interrupt` CRITICAL 규칙이 왜 있는지를 코드로 남기는 테스트이며 이름에 `contract`를 넣는다.
- `uuid`·시간을 고정하는 테스트에는 사유 주석을 쓴다(FP-011).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
git diff --exit-code -- data/replay/golden   # 골든 재기록 없음, status.json 불변
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

- `api/` 아래 파일, `Makefile`, `docs/`를 고치지 마라. 이유: 서비스·API 변경은 step 5 이후다. 이 step은 상태 모델과 턴 시작 상태 구성만 다룬다.
- 기존 테스트의 단언을 바꾸거나 약화하지 마라(이 step의 수정 허용 경로에는 기존 테스트 파일이 없다). 이유: 단일 턴 경로가 불변이어야 골든 재생이 재기록 없이 통과한다(FP-060·061).
- `history`를 `messages`를 파싱해서 만들지 마라. 이유: 쓰기 턴은 최종 assistant 메시지가 `messages`에 없고 `finish`가 `answer`에만 쓴다. 이월 대상은 `request`와 `answer`로 구성한다.
- `evidence_ids`를 턴 시작에 비우거나 상태에 따라 버리지 마라. 이유: V15 근거는 대화 전체에서 본 문서로 누적한다. `failed`·`interrupted` 턴에서 본 문서도 유지해야 보수적이다.
- 대기 `interrupt`가 있는 thread에 새 입력을 `ainvoke`하는 코드를 `src/`에 만들지 마라(계약 프로브 테스트만 예외). 이유: 에러 없이 대기 승인이 사라진다. 서비스의 보호 장치는 step 5다.
- `data/replay/golden/`의 파일을 고치거나 재기록하지 마라. 이유: 골든은 단일 턴 불변의 증거다.

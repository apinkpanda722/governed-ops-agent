# Step 5: graph-write-path

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/src/govops/agent/graph.py`, `/src/govops/agent/prompts.py`, `/src/govops/agent/checkpointer.py`, `/src/govops/tools/registry.py`, `/src/govops/tools/effects.py`, `/src/govops/tools/outbox.py`, `/src/govops/tools/resilience.py`, `/src/govops/audit/log.py`, `/src/govops/replay/llm.py`(`prompt_hash` 입력), `/src/govops/golden.py`, `/src/govops/evalrun/dev.py`(프롬프트 해시 사용처)
- `/tests/test_agent_graph.py`, `/tests/test_e2e_isolation.py`, `/tests/test_golden_trace.py`, `/tests/test_golden_harness.py`
- 이전 step 산출물: `/src/govops/agent/draft.py`, `/src/govops/agent/checks.py`, `/src/govops/agent/scope.py`, `/src/govops/agent/approval.py`

## 작업

에이전트 그래프에 **쓰기 경로**(초안 제출 → 결정론 검사 → 승인 대기 → 결정 → 실행)를 만든다. 가장 큰 step이다. TDD로 테스트를 먼저 쓴다.

### 에이전트 프로필 (골든 해시 보존)

M2 골든 기록 7개는 프롬프트 해시(시스템 프롬프트·도구 정의·모델·파라미터)에 묶여 있어 지금의 프롬프트·도구 정의를 바꾸면 `test_golden_trace`가 "재기록 필요"로 실패한다. 그래서:

```python
@dataclass(frozen=True) class AgentProfile: name: str; system_prompt: str; tools: list[dict]; allow_writes: bool
READ_ONLY: AgentProfile      # 지금의 SYSTEM_PROMPT와 agent_tool_definitions()를 한 글자도 바꾸지 않은 값(기본값)
WITH_WRITES: AgentProfile    # 쓰기 안내가 추가된 프롬프트 + 읽기 6개 + 부작용 3개 도구 정의
def build_graph(chat, tools, checkpointer, *, profile: AgentProfile = READ_ONLY, effects: EffectDeps | None = None, audit: AuditPort | None = None, scope: ScopeIndex | None = None, policy: TeamPolicy | None = None, users: Users | None = None, clock=...) -> CompiledStateGraph
```

- `READ_ONLY`로 만든 그래프는 **이전과 완전히 같게 동작**한다(프롬프트 해시 동일). `test_golden_trace`·`test_golden_harness`·`test_agent_graph`가 수정 없이 통과해야 한다. 해시가 달라진 테스트를 고쳐서 통과시키지 마라.
- `WITH_WRITES`의 프롬프트는 새로 쓴다: 부작용 도구는 **초안 제출**이며 사용자 승인 뒤에 실행된다는 점, 수신처는 사용자가 요청한 곳만 쓸 것, task당 부작용 1건. `registry.py`에 `agent_tool_definitions(include_writes: bool = False)`를 두고 기본값은 이전과 같은 결과여야 한다. 부작용 도구 입력 스키마에 사용자·팀·직급 필드가 없다는 계약 테스트를 유지한다.
- 프로필 기본값은 `READ_ONLY`다(`WITH_WRITES` 전환과 골든 재기록은 후속 phase).

### AgentState 확장

기존 필드를 유지하고 추가한다: `status`를 `running | awaiting_approval | completed | failed | blocked | rejected | expired | interrupted`로 확장, `evidence_ids: list[str]`, `draft: dict | None`(tool·args), `draft_sha256`, `check: {status, reason} | None`, `approvals: list[{approval_id, approver, decision, draft_sha256}]`, `awaiting_since`/`expires_at`(ISO, 주입 시계), `effect: {state, mock_id, reason} | None`, `created_at`. `user_id`는 기존대로 소유자다. **토큰·Authorization은 state에 두지 않는다.**

### 노드 (그래프 모양)

`model → (read_tools | propose | END)`, `propose → check`, `check → (approval | finish[blocked])`, `approval → decide`, `decide → (check[수정] | execute[승인] | approval[보류] | finish[거부·만료])`, `execute → finish`, `read_tools → model`.

- `read_tools`: 기존 동작 + 결과에서 `scope.evidence_ids(...)`를 계산해 `evidence_ids`에 누적한다.
- `propose`: 모델의 부작용 도구 호출을 **실행하지 않고** 초안으로 만든다. 입력 스키마 검증 실패·신원 필드·부작용 도구가 둘 이상(task당 1건 초과)이면 도구 결과로 오류 payload를 돌려주고 모델 루프로 복귀한다(MAX_STEPS 적용). 감사 `side_effect.draft`를 남긴다.
- `check`: `scope.run_checks(...)`(수신처·PII·팀 정책·V15)를 돌린다. 결과 `masked`면 마스킹된 초안과 새 해시로 교체한다. 감사 `side_effect.draft`의 필드에 `check_result`(`passed`·`masked`·`blocked`)와 사유 코드를 남긴다(**새 이벤트 이름을 만들지 않는다**, 감사 허용 목록에 `check_result` 값 3종 추가). `blocked`면 모델을 부르지 않고 고정 문구 답변(`message`)으로 `finish`(status `blocked`).
- `approval`: **`interrupt()`만** 호출한다(승인 요청 payload: task_id·초안·해시·검사 결과·만료 시각). 부작용·감사 쓰기를 이 노드에 두지 마라(재개 시 이 노드가 처음부터 다시 실행된다). `approval.requested` 감사는 이 노드 **앞**의 `check` 노드(또는 별도 노드)에서 기록하고 재개로 중복되지 않게 한다. `awaiting_since`·`expires_at`(30분)은 `check` 통과 시점에 한 번 정한다.
- `decide`: 재개 값 `{approver, decision, draft_sha256, edited?}`를 검증한다. 승인자 신원은 재개 값에서만 오며(API가 세션에서 넣는다) **`approver_decision`으로 정책을 다시 확인**한다. `draft_sha256`이 현재 대기 중인 초안의 해시와 다르면 무시하고 승인 대기로 돌아간다(TOCTOU). `approve` → `execute`. `edit_approve` → `approval.edited` 감사(수정 전후 해시) → `apply_edit` → `check`(결정론 검사 **재실행**, PII 포함) → 통과하면 **새 해시에 묶인 승인**으로 `execute`, 실패하면 실행하지 않고 승인 대기로 돌아가며 검사 결과를 표시한다. `reject` → status `rejected`. 만료 시각이 지났으면 `expired`(`approval.expired` 감사). `approval.approved`·`approval.rejected` 감사는 해당 결정 직후 기록한다.
- `execute`: `tools.effects.execute_effect(EffectRequest(task_id, user_id=요청자, tool, args, approval_id), …)`만 호출한다(**발송 신원은 요청자**, `credential_for(요청자)`는 도구 클라이언트가 호출). 결과 매핑: `SUCCEEDED` → status `completed`(고정 문구 "승인된 내용대로 실행했습니다"), `FAILED` → `failed`(`error`에 사유 코드), `UNCERTAIN` → `interrupted`(`effect.state="uncertain"`). 감사 의도·결과 기록은 실행기가 한다. **실행기에는 `ToolRuntime`과 같은 `ToolPolicy` 인스턴스를 넘긴다**(`ToolRuntime`이 정책을 노출하는 속성을 둔다). 서비스별 서킷이 읽기와 발송·조회에서 공유되어야 한다.
- 감사 쓰기 실패 처리: `side_effect.draft`·`approval.*` 기록이 실패하면 **실행하지 않고** status `failed`(`audit_write_failed`)로 끝낸다.
- `finish`: 상태별 고정 문구 답변(승인 완료·거부·만료·차단), 모델 호출 없음.
- `recursion_limit`을 새 노드 수와 승인 대기 반복(수정·보류)을 허용하도록 재계산한다(`MAX_STEPS`는 모델 호출 상한이며 승인 대기 시간은 제외).
- `run_task`는 프로필에 따라 시스템 프롬프트를 쓴다. 기존 시그니처의 호환을 유지한다.

### 테스트 (가짜 chat·in-process mock·InMemory 저장소·InMemorySaver, 키·네트워크 불필요)

- 승인 흐름: 승인 → 효과 1(요청자 자격 증명으로 호출됨), 거부 → 효과 0·`rejected`, 만료 → `expired`·효과 0, 수정 후 승인 → 수정본이 나가고 새 해시의 승인 ID가 마커에 있음, 수정본에 PII → 승인 대기로 복귀하고 효과 0, 결정론 검사 차단(다른 팀 채널·V15·팀 정책) → 모델 추가 호출 없이 `blocked`·효과 0.
- **F8 재개 반복**: 같은 승인으로 `Command(resume=...)`를 N회(3~5) 호출해도 효과 1. 승인 노드에 부작용 호출·감사 쓰기가 없다는 구조 테스트. 실행 중간에 그래프를 버리고 같은 체크포인트로 재개해도 효과 1(outbox가 막는다).
- TOCTOU: 승인 대기 중 해시가 다른 승인은 실행되지 않는다.
- 근거 누적: `lookup_id` 결과도 `evidence_ids`에 들어간다. 읽기 전용 프로필에서는 부작용 도구가 정의에 없다.
- 기존 골든·그래프 테스트는 수정 없이 통과.
- 감사 이벤트 순서(draft → requested → approved/edited/rejected/expired → attempt → result)와 필드가 원문 없이 허용 형식이다.

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

- 기본 프로필(`READ_ONLY`)의 시스템 프롬프트·도구 정의를 바꾸지 마라. 이유: 골든 기록의 프롬프트 해시가 달라져 CI가 "재기록 필요"로 실패한다. 골든을 고쳐 통과시키지 마라.
- `approval` 노드에 `interrupt()` 외의 일(감사 쓰기·실행·상태 변경)을 두지 마라. 이유: 재개 시 노드가 처음부터 다시 실행되어 부작용·감사가 반복된다(ADR-011).
- 승인자 신원·승인 결정을 모델 출력이나 도구 인자에서 읽지 마라.
- 승인 없이 `execute`에 도달하는 경로를 만들지 마라(`check`·`approval`·`decide`를 모두 거쳐야 한다).
- 토큰·Authorization을 state·checkpoint·trace·로그에 두지 마라.
- API 코드(`src/govops/api/`)는 이 step에서 수정하지 마라(다음 step).
- `ToolRuntime.call`이 부작용 도구를 직접 실행하게 만들지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

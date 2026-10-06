# Step 0: observation-m3

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- 계층 규칙: `evalrun/`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import하지 않고 다른 계층은 `evalrun`을 import하지 않는다(`AGENTS.md` 계층 경계 표). 확인한 사실의 근거 파일: `src/govops/eval/run_grade.py`(`build_observation`은 상태가 `completed`·`failed`가 아니면 거부하고 `effect_bodies=[]`), `src/govops/eval/observation.py`(`Observation`·`ApprovalRecord`·`ObservedEffect`), `src/govops/mocks/jira.py`·`slack.py`(`report(bodies=)`), `src/govops/tools/markers.py`, `src/govops/api/tasks.py`(`_view`), `src/govops/api/app.py`(`ApprovalRequest`), `data/eval/tasks.json`(`approval_script`), `data/seed/users.json`.
- `/src/govops/eval/spec.py`(`Effect`·`ApprovalStep`), `/src/govops/eval/tcr.py`(`grade_tcr`, `_edit_reasons`), `/src/govops/eval/duplicates.py`, `/src/govops/evalrun/dev.py`(M2 러너, 수정 금지), `/tests/test_eval_tcr.py`, `/tests/test_duplicates.py`

## 작업

승인이 있는 task의 **관측(`Observation`) 어댑터**를 만든다. 기존 `eval/run_grade.build_observation`은 읽기 전용 task용이므로 바꾸지 않는다(M2 러너가 사용한다). TDD로 테스트를 먼저 쓴다.

### `src/govops/evalrun/observe.py`

```python
def build_observation_m3(*, spec: TaskSpec, run_index: int, view: Mapping, mock_state: Mapping[str, Mapping], approvals: Sequence[ApprovalRecord], tool_calls: Sequence[Mapping], plan_remaining: int, trace_id: str | None) -> Observation
STATUS_MAP: Mapping[str, str]
```

- 입력: API `GET /tasks/{id}` 뷰(`status`·`answer`·`cited_doc_ids`·`observed_doc_ids`·`draft`·`effect`·`error`…), mock `/state?bodies=true` 보고서(`{"jira": {...}, "slack": {...}}`), 승인 호출 기록, 호출한 도구 목록, 승인 계획에서 실행하지 못하고 남은 단계 수(`plan_remaining`).
- **상태 매핑(`STATUS_MAP`)**: `completed`→`completed`, `failed`→`failed`, `blocked`→`rejected`, `rejected`→`rejected`, `expired`→`expired`, `interrupted`→`interrupted`. **`awaiting_approval`으로 끝난 task는 모두 `failed`**로 기록한다(승인 계획이 아예 없었든, 계획이 남았든 끝내 결정을 받지 못한 것이므로). 종결 상태인데 `plan_remaining > 0`이면(계획의 승인 단계를 다 실행하지 못함) `failed`로 기록한다. 알 수 없는 상태는 `ValueError`.
- **효과 추출(mock 상태가 단일 근거)**: slack 메시지 → `kind="slack_message"`, `target`=채널 ID; jira 코멘트 → `jira_comment`, `target`=티켓 ID; jira 후속 티켓(`children`) → `jira_followup_ticket`, `target`=원 티켓 ID(`parent_id`), `structure={"parent_ticket_id": parent, "project_key": 새 티켓 ID의 접두어}`. 효과마다 `count=1`, `approval_id`=메타데이터 `govops_approval`(없으면 `None`), `body_sha256`=보고서의 해시. **본문은 `effect_bodies`에 넣는다**(slack `text`, 코멘트 `body`, 후속 티켓 `title` + 줄바꿈 + `description`). 마커가 없거나 다른 task의 마커인 효과도 **빠뜨리지 않고** 포함한다(보수적으로 센다).
- `approvals`는 인자로 받은 기록을 그대로 쓴다(`ApprovalRecord`의 `http_status` 포함). `policy_violations`는 빈 목록, `fault_injected=False`.
- `answer`·`observed_ids`·`cited_doc_ids`·`tool_calls`는 뷰와 인자에서 가져온다. 뷰·보고서에 토큰·Authorization이 있으면 `ValueError`(방어).

### 테스트

- **픽스처는 실제 mock 보고서 형태**로 만든다: `src/govops/mocks/jira.py`의 `report(bodies=True)`와 `slack.py`의 `report(bodies=True)`가 만드는 키 구조 그대로(코멘트 `comment_id`·`ticket_id`·`author`·`metadata`·`body`·`body_sha256`, 후속 티켓 `ticket_id`·`parent_id`·`title`·`description`·`metadata`…). 가짜 보고서를 손으로 단순화하지 않는다. 가능하면 실제 `JiraState`·`SlackState`(in-process)에서 효과를 만들어 `report(bodies=True)` 결과를 픽스처로 쓴다.
- **매핑 표 전체 테스트**(상태별 기대값)와 **"우연히 통과하는 판정" 테스트**: 결정론 검사에 막혀 `blocked`로 끝난 T6-01 관측은 `rejected`로 매핑되지만 승인 기록이 없으므로 `grade_tcr`가 **실패**(`reject_not_recorded`)해야 한다. 정상 거부(승인 기록 2xx reject)는 통과. T5-11(`blocked`, 승인 계획 없음)은 통과. 승인 대기로 끝난 task는 `failed`.
- 효과 추출: 세 도구별 효과, 마커 없는 효과 포함, 후속 티켓 `structure`, `effect_bodies`로 `_edit_reasons`(T6-03 `edit_append`)가 통과·실패하는 두 경우, `grade_tcr`가 T2 승인 실행·T3 코멘트·후속 티켓에서 통과.
- 토큰 필드가 들어 있으면 거부.

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

- 기존 `build_observation`·`Observation` 스키마를 바꾸지 마라. 이유: M2 러너와 평가 테스트가 의존한다.
- 상태 매핑 결과만으로 통과하게 만들지 마라(FP-041). 이유: `blocked`→`rejected` 매핑이 T6-01을 통과시킬 수 있다. 통과 조건은 TCR이 승인 기록으로 판정한다.
- 관측을 가공본(요약·집계)에서 만들지 마라(FP-022). 이유: 효과는 mock 상태 원자료에서만 얻는다.
- 가짜 보고서를 실제 형태와 다르게 단순화하지 마라(FP-020). 이유: 계약 오해가 테스트에서 가려진다.
- 중복 건수(0)만 보고 합격시키지 마라(FP-040). 이 step은 누락·미승인도 관측에서 보이게 한다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

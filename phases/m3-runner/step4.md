# Step 4: golden-m3

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/golden.py`(`GOLDEN_TASKS`, `golden_expectations`, `digest_for`, `record_status`, `compare`, `replay_task`, `manifest_tasks`), `/src/govops/replay/llm.py`(`ReplayChatClient`, 요청 키 계산 `_request`·`_order_free`, `RerecordRequired`), `/src/govops/replay/a_replay.py`, `/data/replay/golden/expectations.json`, `/data/replay/golden/status.json`, `/tests/test_golden_trace.py`, `/tests/test_golden_harness.py`, `/docs/EVAL.md`("golden trace"), `/docs/results/hypotheses.md`(§9)
- 이전 step 산출물: `/src/govops/evalrun/observe.py`, `/src/govops/evalrun/driver.py`, `/src/govops/evalrun/dev_m3.py`, `/src/govops/agent/graph.py`(`WITH_WRITES`·`READ_ONLY`)

## 작업

골든 trace를 **부작용 task까지 14개로 확장**한다. 기록은 live 러너(`make eval-run-dev`)가 만들므로 이 step에서는 **기대값 계산·재생 하네스·재생 결정성**만 만든다(`data/replay/llm/`·`a_run/`의 새 파일을 만들거나 커밋하지 않는다). TDD로 테스트를 먼저 쓴다.

### 골든 대상 확장

- `GOLDEN_TASKS`를 14개로 한다: 기존 7개(T1-01·T1-05·T1-08·T5-01·T5-02·T5-05·T5-08) + **T2-01·T3-01·T3-07·T4-01·T6-01·T6-03·T5-11**. test 문항은 넣지 않는다. 코드 상수 한 곳에 둔다.
- `golden_expectations(specs)`: 읽기 task(기존 7개)의 기대값 구조는 **바꾸지 않는다**(기존 `expectations.json` 항목과 같은 결과). 부작용·승인 task는 명세에서 결정론으로 계산한다: `effects`(종류·대상·건수·`structure`), `outcome`, `approval_plan`(행위자·결정 목록, 수정 문장 `edit_append`는 포함), `forbidden_tools`, `status`(기대 종료 상태: `approved_executed`→`completed`, `approval_rejected`→`rejected`, `blocked`→`rejected`). 기록 결과를 보고 기대값을 고치는 코드를 만들지 않는다.
- `data/replay/golden/expectations.json`은 이 step에서 **재생성하지 않는다**(다음 step의 `prereg-m3`가 `hypotheses-m3.md`와 같은 커밋으로 만든다). 이 step은 계산 함수와 "커밋된 파일이 함수의 재계산 결과와 같다"는 테스트(현재 7개 항목과의 호환)만 만든다. 14개 전체 일치 테스트는 파일이 갱신된 뒤(다음 step) 통과하도록 두되, 이 step에서는 14개 계산 결과의 형태·내용 테스트만 둔다.

### 상태·digest

- `status.json` 항목에 `profile`(`"read_only"` 또는 `"writes"`)을 추가한다. 기존 항목은 `"read_only"`로 읽는다(파일을 수정하지 않고 필드가 없으면 `read_only`). `record_status`는 `profile`을 받아 기록한다.
- `digest_for`: 기존 키(`tool_order`·`effect_count`·`status`·`refused`·`forbidden_tool_called`)는 그대로 두고 **부작용 task에 대해** `effects`(종류·대상·건수만), `approval_decisions`(행위자 순서의 결정 목록)를 추가한다. **승인 ID·mock ID·해시·본문은 digest에 넣지 않는다**(실행마다 달라진다).

### 재생 하네스 (`replay_task` 확장)

- 프로필에 따라 그래프를 만든다: `profile == "writes"`이면 `WITH_WRITES` 그래프 + in-process mock(`mocks`는 테스트 쪽에서만 import하거나 기존 `mock_executor` 주입 방식을 쓴다) + InMemory outbox + 메모리 감사 + 승인 스크립트 재생(`ApprovalDriver`를 재사용하되 `golden.py`가 `evalrun`을 import하지 않도록 계층을 지킨다: `golden.py`는 계층 사이 조립 모듈이므로 `evalrun`이 아니라 필요한 최소 로직만 `golden.py` 안 또는 `agent`·`api`의 공개 함수로 둔다. 계층 가드가 허용하는 import만 쓴다). `profile == "read_only"`는 이전 동작 그대로.
- 재생 비교: 도구 순서·효과 수·종료 상태·거절 여부·승인 결정 목록·금지 도구 미호출. **승인 ID·mock ID·task ID는 비교에서 제외**한다.

### 재생 결정성 (실행마다 달라지는 값이 LLM 요청에 들어가지 않음)

- **테스트**: 같은 기록(합성 기록)을 **서로 다른 task ID로 두 번 재생**해도 모든 LLM 요청 키가 같다. 요청 메시지(시스템 프롬프트·사용자 요청·도구 결과·assistant 메시지)에 task ID·승인 ID·만료 시각·mock ID·타임스탬프가 들어가지 않음을 확인하는 테스트(그래프가 만드는 메시지를 가짜 chat으로 가로채 정규식·값 비교로 검사)를 둔다. 값이 들어 있으면 **그래프·프롬프트 코드를 고치지 말고 최종 응답에 `error`로 보고한다**(프롬프트 해시가 바뀌면 기록이 무효가 된다).
- 기존 골든 7개 재생(`read_only`)과 `test_golden_trace`·`test_golden_harness`는 수정 없이 통과한다. 기록 파일이 없는 새 7개는 "미기록"으로 건너뛴다(자격 있는 항목의 파일 누락은 일관성 실패라는 기존 규칙 유지).

### 테스트 (합성 기록, 가짜 chat·가짜 A·in-process mock)

- 14개 기대값 계산(효과·승인 계획·종료 상태), 읽기 7개 호환, `profile` 필드(없으면 read_only), 부작용 task 합성 기록의 재생: 승인 실행·거부·수정 승인·차단·전사 공지 승인자, 비교 불일치 코드(효과 수·승인 결정·도구 순서), 승인 ID·mock ID 차이는 통과.
- 재생 결정성 테스트(위), `RerecordRequired`는 프롬프트 해시 불일치 때 유지.

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

- `data/replay/llm/`·`a_run/`·`status.json`·`expectations.json`을 이 step에서 만들거나 수정하지 마라. 이유: 기록은 live 러너가 만들고 기대값 파일은 다음 step이 사전 등록 파일과 같은 커밋으로 만든다(FP-043).
- 기록 결과를 보고 기대값을 고치거나 실패한 기록을 다시 기록해 통과시키는 코드를 만들지 마라(FP-043). 이유: 기대값은 기록 전에 명세에서 계산한다.
- digest·비교에 승인 ID·mock ID·task ID를 넣지 마라. 이유: 실행마다 달라져 재생이 항상 실패한다.
- 프롬프트·도구 정의·시스템 프롬프트를 고쳐서 결정성 테스트를 통과시키지 마라. 이유: 프롬프트 해시가 바뀌면 골든 기록 전체가 무효가 된다. 값이 들어 있다면 보고만 한다.
- 기존 읽기 7개 골든의 기대값 구조·`READ_ONLY` 재생 동작을 바꾸지 마라.
- `golden.py`·`agent/`가 `data/eval/`의 정답지를 에이전트 실행 경로에서 읽게 하지 마라(FP-042). 기대값 계산은 명세(`tasks.json`)에서 오프라인으로 한다.
- 재생 비교를 가공한 관측 파일로 판정하지 마라(FP-022).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

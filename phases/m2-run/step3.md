# Step 3: golden-trace

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/EVAL.md` ("golden trace", "TCR"), `/docs/ADR.md` (ADR-009), `/docs/ARCHITECTURE.md` ("golden trace 두 층")
- 이전 step 산출물: `/src/govops/replay/llm.py`, `/src/govops/replay/a_through.py`, `/src/govops/replay/a_replay.py`, `/src/govops/eval/run_grade.py`
- `/src/govops/agent/graph.py`·`/src/govops/agent/prompts.py`, `/src/govops/tools/registry.py`, `/src/govops/mocks/`(mock-jira·mock-slack 앱과 시드), `/tests/test_e2e_isolation.py`(실제 그래프·ToolRuntime·replay A·mock을 인프로세스로 잇는 방식), `/src/govops/eval/spec.py`, `/src/govops/eval/tcr.py`, `/data/eval/tasks.json`

## 작업

기록한 LLM 응답 + 기록한 A 응답 + 인프로세스 mock으로 에이전트 그래프를 **결정론으로 재생**하고, 기록 시점 결과와 비교하는 golden trace를 만든다. 기록 파일은 사용자가 이후에 `make eval-run-dev`로 만든다(이 step 시점에 `data/replay/llm/`·`data/replay/a_run/`에는 파일이 없다). TDD로 테스트를 먼저 쓴다.

### 골든 대상과 자격 (사전 고정)

- 골든 대상은 **T1-01, T1-05, T1-08, T5-01, T5-02, T5-05, T5-08**(M2 dev 7개)로 코드 상수 `GOLDEN_TASKS`에 고정한다. test 문항은 넣지 않는다.
- **자격 규칙**: 기록 실행 중 A 호출이 하나라도 기록 불가(비합성 문서로 건너뜀)이면 그 task는 골든에서 뺀다(`golden_eligible=False`). 제외 task와 사유 코드는 `data/replay/golden/status.json`에 남고 결과 문서(이후 사용자 작업)에 적는다.
- **기대값은 기록 전에 명세에서 계산해 고정한다**: `data/replay/golden/expectations.json`을 `data/eval/tasks.json`에서 결정론으로 계산해 커밋한다. 항목: 부작용 건수(0), 기대 결과 종류(`answer`·`not_found`·`not_found_or_partial`), 거절 여부(`not_found*`·`blocked`면 true), 금지 도구 목록, 정상 종료 상태. 함수 `golden_expectations(specs) -> dict`와, 커밋된 파일이 그 함수의 재계산 결과와 같다는 테스트를 둔다. 기록 결과를 보고 기대값을 고치지 않는다.
- **`recorded_fail`**: 기록 실행이 명세 기대(TCR)에 실패했으면 다시 기록해 통과시키지 않는다. 그 task는 `status.json`에 `recorded_fail`로 표시하고, CI는 **재생 결과가 기록과 같은지만** 비교한다(명세 통과를 요구하지 않는다).

### 만들 것 — `src/govops/replay/golden.py` (또는 적절한 위치. 계층 경계 준수)

```python
GOLDEN_TASKS: tuple[str, ...]
def golden_expectations(specs) -> dict
@dataclass(frozen=True) class GoldenRecord: task_id; observation_digest: dict   # 기록 시점 Observation에서 뽑은 비교 항목
def digest(obs: Observation) -> dict   # 도구 호출 이름 순서, 부작용 건수, 최종 상태, 거절 여부, 금지 도구 호출 여부. 답변 본문은 담지 않는다
async def replay_task(task_id, *, llm_dir, a_dir, mock_executor, graph_factory) -> Observation
def compare(expected: dict, recorded: dict, replayed: dict) -> list[str]   # 위반 코드 목록(값 없음)
```

- `replay_task`: `ReplayChatClient`·`ReplayToolExecutor`(안쪽에 인프로세스 mock 읽기 실행기)로 `build_graph`를 돌려 `AgentState` → `build_observation`으로 만든다. 요청 문장·사용자는 `tasks.json`의 명세에서 온다. 재생은 어떤 네트워크도 쓰지 않는다.
- 비교 항목: **도구 호출 순서 제약**(기록한 순서와 같음), **부작용 수**, **최종 상태**, **거절 여부**, **금지 도구 미호출**. Guardrail 판정은 M4 구현 전이라 비교하지 않는다(문서에 명시).
- 비교 규칙: 재생 digest가 기록 digest와 같아야 한다. 기록이 `pass`면 추가로 `expectations.json`에도 맞아야 한다. `recorded_fail`이면 재생=기록만 본다.
- 기록이 없는 골든 task는 **"미기록"으로 건너뛰며 실패가 아니다**(사용자 live 실행 전). `eligible`로 표시됐는데 파일이 없거나, 파일은 있는데 `status.json`에 없으면 일관성 실패다.
- 프롬프트·모델·파라미터 해시가 기록과 다르면 `RerecordRequired`("재기록 필요")로 CI가 실패한다.

### 테스트

- `tests/test_golden_trace.py`: (a) `golden_expectations`가 `tasks.json`과 같다(7개 고정, test 문항 없음), (b) `data/replay/llm/`·`a_run/`·`status.json`이 있으면 모든 `eligible` 골든을 재생해 비교한다(없으면 "미기록"이고 통과), (c) 모든 기록 파일의 문서가 `syn-*`이고 토큰이 없다.
- `tests/test_golden_harness.py`: **합성 기록**(테스트가 가짜 chat·가짜 A로 직접 만든 기록, 커밋 파일 아님)으로 하네스를 검증한다 — 같은 도구 순서·상태면 통과, 도구 순서가 다르면·부작용이 생기면·금지 도구가 호출되면·상태가 다르면 각각 위반, `recorded_fail` 기록은 재생=기록이면 통과, 프롬프트 해시 불일치는 "재기록 필요", 자격 없는 task(건너뛴 A 호출)는 골든에서 빠진다, 가짜 A 서버 대신 기록한 A 응답이 그대로 쓰인다.
- 키·네트워크 없이 통과한다. 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙(토큰 비노출, 실제 API 호출 금지, 합성 문서 `syn-*`만 기록)을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 기록 결과를 보고 `expectations.json`을 고치거나 실패한 기록을 다시 기록하게 만들지 마라. 이유: 기대값은 기록 전에 명세에서 고정한다(사전 등록).
- 골든에 test 문항을 넣지 마라. 이유: test 문항을 dev 전에 실행·기록하면 평가 오염이다.
- 골든 재생이 `replay/fake_a.py`의 자체 데이터로 응답하게 만들지 마라. 이유: 모델이 실제로 보낸 질의의 기록한 응답만 돌려줘야 한다.
- `data/replay/llm/`·`a_run/`에 파일을 만들거나 커밋하지 마라. 이유: 사용자 live 실행(`make eval-run-dev`)이 만든다.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(AGENTS.md CRITICAL).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/tasks.json`, `data/eval/meta.json`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

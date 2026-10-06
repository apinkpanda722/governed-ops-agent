# Step 1: tool-trace-def

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/EVAL.md`("TCR", "golden trace", "재실행 규칙"), `/docs/ADR.md`(ADR-010·020), `/docs/results/hypotheses.md`(§5 금지 목록 자동 계산, §9 골든), `/docs/results/hypotheses-m3.md`, `/.claude/skills/failure-patterns/SKILL.md`
- 근거 원자료(판단은 이 파일들로만 한다, FP-022): `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`(`manifest.json`·`grades.json`·`observations.jsonl`·`traces/<trace_id>.jsonl`), `data/replay/a_run/`, `data/replay/golden/status.json`
- `/src/govops/evalrun/dev_m3.py`(`trace_mismatch` 399행 부근, `_EffectCapture`·`_TaskTools`, 관측 구성 690행 부근, `n_registration_*` 805행 부근), `/src/govops/evalrun/tool_trace.py`(`extract_tool_trace`), `/src/govops/evalrun/observe.py`, `/src/govops/golden.py`(`_write_observation`, `digest_for`), `/src/govops/agent/graph.py`(`propose` 노드와 메시지 형식), `/src/govops/tools/registry.py`(읽기·부작용 도구 이름), `/src/govops/eval/tcr.py`(`forbidden_tool_called`), `/tests/test_evalrun_dev_m3.py`, `/tests/test_evalrun_tool_trace.py`, `/tests/test_golden_m3.py`
- 저장된 증거: `manifest.json`의 `tool_trace_mismatch`(T2-09·T5-11·T6-01)와 그 task들의 `observations.jsonl`·`traces/`

## 작업

**`tool_trace_mismatch` 정의를 고친다.** 저장된 dev 실행(`da337bb7…`)에서 `tool_trace_mismatch`가 T2-09·T5-11·T6-01 3건 났고 이 때문에 `n_registration_blocked`가 켜졌다. 세 task는 모두 **초안을 제출했지만 실행되지 않은** task다(T2-09는 결정론 검사의 V15 차단, T5-11은 수신처 차단, T6-01은 승인 거부). 캡처는 실행된 도구만 기록하는데 `messages`의 assistant 호출에는 초안 제출(부작용 도구)이 들어 있어 개수가 다르게 나왔다. 결함은 비교 대상의 정의가 서로 다른 두 기록을 비교한 것이다. TDD로 테스트를 먼저 쓴다.

### 1. 비교는 읽기 도구만

- `trace_mismatch(state, captured)`는 **읽기 도구 호출만** 센다: `messages`의 assistant 호출 중 이름이 **부작용 도구 3종**(`slack_send_message`·`jira_add_comment`·`jira_create_followup_ticket`, `tools/registry.py`의 상수를 쓰고 이름을 복사하지 않는다)이 아닌 것의 수 ↔ 캡처 기록 중 읽기 도구 호출 수(`_EffectCapture`가 만드는 부작용 실행 기록은 제외). 개수가 다르면 불일치다. 부작용 도구 호출은 이 비교에서 양쪽 모두 뺀다.

### 2. `Observation.tool_calls`에 초안 제출을 넣는다

- 새 함수 `proposal_calls(state) -> list[dict]`(`evalrun/tool_trace.py`): `messages`의 assistant 호출 중 부작용 도구 이름인 것을 호출 순서대로 `{"name": <도구 이름>, "ok": <bool>}`로 돌려준다. `ok`는 그 호출에 대한 `role == "tool"` 메시지(같은 `tool_call_id`)가 오류 payload(`"error"` 키)이면 `False`, 응답 메시지가 없거나 오류가 아니면 `True`다(초안이 접수된 경우).
- 러너(`dev_m3.py`)가 만드는 `Observation.tool_calls`는 **읽기 호출(캡처)** + **`proposal_calls`**다. 실행된 부작용이 `_EffectCapture`로 따로 기록되더라도 **`tool_calls`에 중복해서 넣지 않는다**(실행된 부작용의 도구 이름은 같은 호출의 초안 제출 항목 하나로 표현된다). 예: T2-01의 `tool_calls`는 지금처럼 `[lookup_id, get_document, slack_send_message]`이어야 한다. 읽기 호출과 초안 제출의 상대 순서는 `messages`의 호출 순서를 따른다.
- `golden._write_observation`(writes 재생)도 같은 함수로 `tool_calls`를 만든다(라이브와 재생이 같은 구성).
- **`golden.digest_for`의 `tool_order`는 의미를 바꾸지 않는다**: 저장된 골든 기록(14개)의 digest는 초안만 제출하고 실행되지 않은 부작용 도구를 포함하지 않는다. 그래서 `tool_order`는 **읽기 도구 + 실제로 실행된 부작용 도구**만 담는다(부작용 도구 항목은 해당 종류의 효과가 `obs.effects`에 있을 때만 포함: `slack_send_message`↔`slack_message`, `jira_add_comment`↔`jira_comment`, `jira_create_followup_ticket`↔`jira_followup_ticket`). 이 규칙으로 현재 커밋된 14개 `observation_digest.tool_order`가 그대로 유효해야 한다(재기록 없이 `test_golden_trace`가 통과).
- TCR의 `forbidden_tool_called`는 `tool_calls`를 그대로 보므로 모델이 금지된 부작용 도구를 **제출만 해도** 걸린다(실행 여부와 무관). 이것이 의도한 변화다.

### 3. N 등록 보류 규칙은 유지, 정의만 바뀐다

- 기존 규칙(불일치 task는 N 입력에서 빼고 하나라도 있으면 `n_registration_blocked`)은 그대로다. 정의가 고쳐졌으므로 위 3건은 더 이상 불일치가 아니다.
- N 후보 입력(`tool_results`)은 읽기 도구 결과(구조 ID)이고 부작용 초안 제출은 `tool_results`에 들어가지 않는다. 이 점을 테스트로 고정한다.

### 4. 테스트 (합성 상태 + 저장된 증거)

- **정의 테스트**: T2-09형 합성 상태(읽기 호출 2개 + 부작용 초안 제출 1개, 실행 없음)에서 새 `trace_mismatch`는 False(이전 정의라면 True였다는 주석), 읽기 호출 하나가 캡처에서 빠진 상태는 True. T5-11형(수신처 차단)·T6-01형(승인 거부)도 같다.
- **`tool_calls` 구성**: 읽기 호출 + 초안 제출, 실행된 부작용이 중복되지 않음, 오류 payload를 받은 초안은 `ok=False`, 금지 도구를 제출만 한 가짜 task가 `forbidden_tool_called`로 TCR 실패한다.
- **digest 불변**: 합성 상태로 (a) 읽기만, (b) 초안 제출 후 거부·차단(실행 없음), (c) 초안 제출 후 실행 각각의 `digest_for(...)["tool_order"]`가 위 규칙대로이고 (b)는 부작용 도구 이름이 없다.
- **저장된 증거 확인**(읽기 전용): 저장된 dev 실행의 `manifest.json`이 `tool_trace_mismatch`를 정확히 T2-09·T5-11·T6-01로 기록하고, 세 task의 `observations.jsonl` 행이 모두 `effects == []`임을 단언한다(원인이 "초안은 냈지만 실행되지 않은 task"라는 사실을 테스트로 남긴다).
- N: 기존 `explore` 테스트와 러너 테스트(`n_registration_blocked` 표시, 불일치 task 제외)는 수정 없이 통과한다.

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

- 읽기 도구와 부작용 도구의 호출을 한 비교에 섞지 마라(FP: 세는 대상 정의가 다른 두 기록을 비교하면 차이가 난다). 이유: 캡처는 실행된 호출을, `messages`는 제출된 호출을 담는다.
- 불일치를 없애려고 비교를 끄거나 허용 오차를 두지 마라. 이유: 읽기 도구 호출의 불일치는 여전히 기록이 신뢰할 수 없다는 신호다.
- `digest_for`의 `tool_order`에 실행되지 않은 부작용 도구를 넣지 마라. 이유: 커밋된 14개 골든 digest가 무효가 된다.
- 저장된 `observations.jsonl`·`manifest.json`을 고쳐서 테스트를 통과시키지 마라(FP-011). 저장된 증거는 읽기만 한다.
- 이 step에서 N 값을 산출·등록하지 마라. 이유: N 산출은 결과 문서에서 한다(등록 파일은 만들지 않는다).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`의 파일(실행 원본 증거)을 수정하지 마라. 이 step의 테스트는 그 디렉토리를 읽기만 한다.
- `docs/results/hypotheses.md`·`docs/results/hypotheses-m3.md`·`data/replay/golden/expectations.json`·`docs/results/m3-dev-supersedes.json`(사전 등록 파일)을 수정하지 마라. 이유: 바꾸면 새 등록이다(FP-043).
- 기존 테스트를 약화하지 마라. 시그니처 변경 때문에 필요한 호출 수정만 허용한다. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

# Step 6: trace-isolation

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "탐색 예산 (M5, ADR-014)", "재실행 규칙"), `/docs/results/hypotheses.md`(§4 탐색 예산 N 규칙), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/dev_m3.py`, `/src/govops/evalrun/tool_trace.py`(`extract_tool_trace`), `/src/govops/evalrun/driver.py`(`ApprovalDriver`, 타임아웃 처리), `/src/govops/evalrun/explore.py`(`longest_streak`·`candidate`가 `row["tool_results"]`를 읽는다), `/src/govops/api/tasks.py`(`TaskService`는 그래프를 백그라운드 태스크로 실행하고 `aclose()`가 취소한다), `/tests/test_evalrun_dev_m3.py`(러너 테스트), `/tests/test_evalrun_driver.py`

## 작업

**도구 호출 캡처를 task별로 분리**하고 **`tool_trace_mismatch`가 있는 task를 N 계산에서 제외**한다. TDD로 테스트를 먼저 쓴다.

### 1. 캡처를 task ID별로 분리

`TaskService`는 그래프를 백그라운드 `asyncio` 태스크로 돌린다. 앞 task가 타임아웃이거나 취소 도중이면 그 task의 늦은 도구 호출이 이어서 도착할 수 있다. 캡처 기록이 task 사이에 섞이면 관측·N 계산이 틀린다.

- 캡처 래퍼(`_Capture` 또는 공용 래퍼)를 **task 시도마다 새로 만들고 그 task ID(러너가 만든 시도 식별자)에 묶는다**. 한 시도의 래퍼는 한 그래프의 실행기로만 쓰인다(서비스·러너 수준에서 공유하는 캡처 객체를 두지 않는다).
- task가 끝난 뒤(종결·타임아웃·취소) 그 래퍼를 **닫는다**. 닫힌 뒤에 도착한 호출은 기록에 추가하지 않고 `late_calls`로만 센다(호출 수만, 내용 없음). 러너는 다음 task를 시작하기 전에 이전 task의 `TaskService`를 `aclose()`로 정리해(백그라운드 태스크 취소·대기) 늦은 호출이 가능한 한 남지 않게 한다.
- manifest에 task별 `late_tool_calls`(정수, 0이면 생략 가능)를 기록한다.
- 테스트: **두 task를 연달아 실행해도 각 캡처에는 자기 task의 호출만 있다**. 구체적으로 첫 task의 그래프가 두 번째 task 시작 뒤에 도구를 호출하도록 가짜 도구가 지연되는 시나리오를 만들어, 두 번째 task의 `tool_calls`·`tool_results`에 첫 task의 호출이 없고 첫 task의 `late_tool_calls`가 센다. 래퍼가 닫힌 뒤 기록이 늘지 않는다. 래퍼 객체가 task 사이에서 재사용되지 않는다(식별 테스트).

### 2. `tool_trace_mismatch` 처리 → N 등록 보류

이전 step에서 만든 교차 확인(캡처한 도구 호출 수와 `get_task` checkpoint의 도구 호출 수 비교)에서 불일치한 task는 신뢰할 수 있는 호출 기록이 없는 task다.

- **불일치한 task는 `explore.candidate`(N 후보) 입력에서 뺀다**(관측·채점·manifest의 다른 항목에는 그대로 남긴다. TCR 판정에서도 빼지 않는다).
- **하나라도 있으면** manifest에 `n_registration_blocked: true`와 불일치 task 목록을 기록하고, N 후보 출력은 "N 등록 보류: tool_trace_mismatch N건(<task 목록>) — 원인을 확인하기 전에는 N을 등록하지 않는다"로 바꾼다(숫자 N을 출력하지 않는다). 콘솔 요약 맨 앞에도 같은 경고를 낸다. 불일치가 없으면 이전과 같이 N 후보를 출력한다(`n_registration_blocked: false`).
- 결과 문서(사용자가 쓴다)에는 첫머리에 이 사실을 적는다는 점을 콘솔 출력 문구로 안내한다("결과 문서 첫머리에 적을 것").
- 테스트: 불일치 task가 `explore.candidate` 입력에서 빠지고(N 후보가 그 task의 호출 기록을 쓰지 않음), `n_registration_blocked`가 true로 기록되고 N 숫자가 출력되지 않는다. 불일치가 없으면 false이고 N 후보가 출력된다. 불일치가 있어도 TCR 판정·manifest의 나머지는 유지된다.

### 3. 문서 반영은 마지막 step이 한다. 이 step은 코드·테스트만 수정한다.

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

- 캡처 객체를 서비스·러너 수준에서 여러 task가 공유하게 만들지 마라. 이유: 앞 task의 늦은 호출이 다음 task의 기록에 섞인다.
- 불일치 task를 조용히 포함해 N을 계산하거나, 불일치가 있는데 N 숫자를 출력하지 마라. 이유: 신뢰할 수 없는 호출 기록이 N을 틀리게 만든다. 원인 확인 전에는 등록하지 않는다.
- 불일치를 이유로 task를 재실행하거나 TCR 판정에서 빼지 마라(FP-043). 이유: 결과를 보고 선택적으로 재실행하는 것은 금지다. 제외는 N 계산에만 적용한다.
- 테스트에서 호출 순서·도착 순서를 고정값으로 단언하지 마라(FP-012). 지연 시나리오는 가짜 sleep·이벤트로 결정론으로 만들되 결과는 "자기 task의 호출만 있음"이라는 불변식으로 단언한다.
- `docs/`를 수정하지 마라(문서 step이 한다).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

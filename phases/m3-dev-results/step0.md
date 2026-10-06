# Step 0: golden-entry

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/EVAL.md`("TCR", "golden trace", "재실행 규칙"), `/docs/ADR.md`(ADR-010·020), `/docs/results/hypotheses.md`(§5 금지 목록 자동 계산, §9 골든), `/docs/results/hypotheses-m3.md`, `/.claude/skills/failure-patterns/SKILL.md`
- 근거 원자료(판단은 이 파일들로만 한다, FP-022): `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`(`manifest.json`·`grades.json`·`observations.jsonl`·`traces/<trace_id>.jsonl`), `data/replay/a_run/`, `data/replay/golden/status.json`
- `/src/govops/golden.py`(`tcr_status`, `record_status`, `digest_for`, `compare`), `/src/govops/eval/run_grade.py`(`grade_run`: 등록 규칙의 단일 진입점), `/src/govops/eval/forbidden_auto.py`, `/src/govops/evalrun/dev.py`(`tcr_status` 호출 지점 288행 부근, M2 러너), `/src/govops/evalrun/dev_m3.py`(호출 지점 754행 부근), `/tests/test_golden_harness.py`, `/tests/test_evalrun_dev.py`, `/tests/test_golden_trace.py`, `/tests/test_eval_run_grade.py`

## 작업

**골든 판정 진입점 결함**을 고친다. `golden.tcr_status`(`golden.py`)가 `grade_tcr`를 직접 불러 `forbidden_auto`(채점 시점 금지 목록 자동 계산, hypotheses §5)를 거치지 않는다. 그래서 dev 채점(`grade_run`, 등록 규칙의 단일 진입점)에서는 `forbidden_leak`로 실패한 T2-01이 골든 `status.json`에는 `pass`로 기록됐다(두 판정이 갈린다). TDD로 테스트를 먼저 쓴다.

### 1. `tcr_status`를 `grade_run`으로

- `tcr_status(spec, obs, *, meta, tickets, users) -> "pass" | "recorded_fail"`로 바꾼다. 내부는 `grade_run(spec=spec, obs=obs, meta=meta, tickets=tickets, users=users).tcr.passed` 하나로 판정한다(등록 규칙, 현재 `grade_run` 동작 그대로. 이 step에서 규칙을 바꾸지 않는다). `grade_tcr`를 직접 부르는 경로는 `golden.py`에 남기지 않는다.
- 호출 지점을 고친다: `golden.record_status`, `evalrun/dev_m3.py`, **`evalrun/dev.py`(M2 러너)**. M2 러너는 이미 `meta`·`tickets`·`users`를 로드하므로 그것을 넘기는 **최소 변경만** 허용한다(M2의 동작·출력·결과는 그대로여야 하고 `tests/test_evalrun_dev.py`는 기대 의미를 바꾸지 않고 통과해야 한다). `golden.record_status`도 필요한 데이터(`meta`·`tickets`·`users`)를 인자로 받게 하고 호출자가 넘긴다.
- 재생 하네스(`golden.replay_task`·`compare`)는 `status.json`의 기록된 상태와 digest를 비교할 뿐이므로 판정 로직을 바꾸지 않는다.

### 2. 저장된 관측으로 `status.json` 다시 계산

- `src/govops/evalrun/regolden.py`: `python -m govops.evalrun.regolden --run <결과 디렉토리> [--write]`. 결과 디렉토리의 `observations.jsonl`(`Observation`)과 명세(`data/eval/tasks.json`), `data/eval/meta.json`·`data/seed/tickets.json`·`data/seed/users.json`으로 **골든 14개**의 `tcr_status`를 다시 계산해 `data/replay/golden/status.json`의 `status`와 `recorded_fail`만 갱신한다(`profile`·`eligible`·`golden_eligible`·`excluded_reason`·`reason_codes`·`attempts`·`observation_digest`는 **그대로 둔다**). `--write`가 없으면 변경 내용만 출력한다(드라이런). 새 파일을 만들거나 기록 파일(`data/replay/llm/`·`a_run/`)을 건드리지 않는다.
- **이 step에서 한 번 실행해 결과를 반영한다**: `python -m govops.evalrun.regolden --run data/results/dev/da337bb7471c4bceae14a2e9b3ce5884 --write`. 예상 변경은 T2-01의 `status` `pass`→`recorded_fail`(`recorded_fail: true`)뿐이다. **다른 항목이 바뀌면** 변경하지 말고(`--write`를 하지 않고) 어느 task가 왜 바뀌는지를 최종 응답 `reason`(status `blocked`)에 적고 멈춘다.

### 3. 테스트

- **판정 일치**: 저장된 dev 실행(`da337bb7…`)의 `grades.json`과 갱신된 `status.json`이 골든 14개 모두에서 같은 판정이다(`grades[task].tcr.passed` ⇔ `status == "pass"`, `eligible`인 task만).
- **T2-01 회귀**: 저장된 T2-01 관측을 `tcr_status`에 넣으면 `recorded_fail`이다(명세에 없던 읽을 수 없는 ID의 노출 = `forbidden_leak`, `forbidden_auto` 경유). 같은 관측을 `grade_tcr` 직접 호출로 판정하면 통과하던 것이 이 결함이었음을 테스트 주석에 쓴다(테스트에서 `grade_tcr` 직접 호출 결과를 단언해 두 경로의 차이를 고정한다).
- `regolden`: 드라이런이 파일을 쓰지 않음, `--write`가 `status`·`recorded_fail`만 바꿈(다른 필드·다른 task 불변), 관측이 없는 task는 건너뜀.
- `tests/test_golden_trace.py`·`test_golden_harness.py`·`test_golden_m3.py`는 기대 의미를 바꾸지 않고 통과한다(필요한 호출 수정만).

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

- `grade_tcr`를 `golden.py`·러너에서 직접 호출해 판정하지 마라(FP-040 계열: 같은 판정에 진입점이 둘이면 서로 다른 결과를 낸다). 판정은 `grade_run` 하나다.
- 채점 규칙(금지 목록 계산 등)을 이 step에서 바꾸지 마라. 이유: 규칙 보완은 별도 등록 파일과 규칙 버전으로 이후 step에서 한다.
- `status.json`의 `observation_digest`·`profile`·기록 파일·`expectations.json`을 바꾸지 마라. 이유: 재생 비교와 사전 등록이 의존한다.
- 재계산 결과를 보고 판정을 조정하지 마라(FP-043). 예상 밖 변경은 보고하고 멈춘다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`의 파일(실행 원본 증거)을 수정하지 마라. 이 step의 테스트는 그 디렉토리를 읽기만 한다.
- `docs/results/hypotheses.md`·`docs/results/hypotheses-m3.md`·`data/replay/golden/expectations.json`·`docs/results/m3-dev-supersedes.json`(사전 등록 파일)을 수정하지 마라. 이유: 바꾸면 새 등록이다(FP-043).
- 기존 테스트를 약화하지 마라. 시그니처 변경 때문에 필요한 호출 수정만 허용한다. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

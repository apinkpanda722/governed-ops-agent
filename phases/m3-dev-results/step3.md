# Step 3: regrade-dev

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/EVAL.md`("TCR", "golden trace", "재실행 규칙"), `/docs/ADR.md`(ADR-010·020), `/docs/results/hypotheses.md`(§5 금지 목록 자동 계산, §9 골든), `/docs/results/hypotheses-m3.md`, `/.claude/skills/failure-patterns/SKILL.md`
- 근거 원자료(판단은 이 파일들로만 한다, FP-022): `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`(`manifest.json`·`grades.json`·`observations.jsonl`·`traces/<trace_id>.jsonl`), `data/replay/a_run/`, `data/replay/golden/status.json`
- `/src/govops/eval/run_grade.py`(`grade_run`, `REGISTERED`, `M3_GRADING`), `/src/govops/evalrun/regolden.py`(이전 step 산출물, 저장된 관측을 읽는 방식 참고), `/src/govops/evalrun/explore.py`(`candidate`, `longest_streak`), `/src/govops/eval/observation.py`(`Observation`, `dump`/`load`), `/tests/test_evalrun_regolden.py`
- 근거(원자료): `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`(`observations.jsonl`·`grades.json`·`manifest.json`)

## 작업

저장된 dev 실행 관측을 **다른 규칙 버전으로 다시 채점**하는 읽기 전용 도구를 만든다. 결과 문서의 "참고값"과 N 산출에 쓴다. 이 도구는 결과를 **저장하지 않고** 출력만 한다(공식 결과 파일을 바꾸지 않는다). TDD로 테스트를 먼저 쓴다.

### `src/govops/evalrun/regrade.py` (`python -m govops.evalrun.regrade`)

- 인자: `--run <결과 디렉토리>`(필수), `--rules registered|m3-grading`(필수, 기본값 없음), `--compare registered`(선택: 기준 규칙과 나란히 비교).
- 동작: `observations.jsonl`의 `Observation`들을 읽어 명세(`data/eval/tasks.json`)·`data/eval/meta.json`·`data/seed/tickets.json`·`data/seed/users.json`으로 `grade_run(..., grading_rules=<규칙>)`을 실행한다. 출력(표준 출력만, 파일 쓰기 없음): task별 `TCR 통과/실패(사유)`와 노출 후보 수, 합계 `TCR n/N`, `--compare`가 있으면 두 규칙의 판정이 **달라진 task 목록**(`task: registered 실패(사유) → m3-grading 통과`). `m3-grading`을 선택하면 실행 시작 시 `hypotheses-m3-grading.md`의 sha256과 등록 커밋(`git log -1 --format=%H -- <파일>`)을 출력하되 **가드(`guard_grading_rules`)는 이 읽기 전용 도구에서는 적용하지 않는다**(참고값 산출용이다. 출력 첫 줄에 "참고값: 공식 결과 아님"을 쓴다).
- 같은 모듈에 `n_candidate(run_dir) -> dict`: 저장된 관측과 `manifest.json`의 `tool_trace_mismatch`로 `explore.candidate`를 호출해 N 후보 입력(성공한 task별 최대 연속 무정보 호출 수의 목록)과 N 후보를 돌려주고 `--n`일 때 출력한다. 입력은 `tool_results`(읽기 도구 결과 구조 ID)이고 **이 관측 파일들은 수정하지 않는다**. 어떤 값이 나오든 이 도구는 "잠정치이며 등록값이 아님"을 함께 출력한다(ADR-014).

### 테스트 (저장된 증거, 읽기 전용)

- `--rules registered`: 20개 중 **17개 통과**, 실패는 T2-01·T2-09·T4-06이고 사유가 저장된 `grades.json`과 같다.
- `--rules m3-grading --compare registered`: **판정이 달라지는 task는 T2-01 하나**이고(실패 `forbidden_leak` → 통과) 나머지 19개는 그대로다. 합계가 18/20이다. (예상과 다르면 테스트가 실패해야 한다. 이를 보고 규칙·기대를 맞추지 말고 최종 응답 `reason`에 어떤 task가 달라졌는지 적고 멈춘다.)
- 도구가 `data/` 아래 어떤 파일도 만들거나 바꾸지 않음(실행 전후 디렉토리 해시 비교).
- `n_candidate`: 저장된 실행의 입력과 후보가 계산되고 불일치 task(`manifest.tool_trace_mismatch`)가 제외된다. 이 step 시점의 저장된 manifest는 불일치 3건을 기록하고 있으므로 입력에서 그 3건이 빠진 상태의 값과, 빠지지 않은 상태의 값을 둘 다 계산할 수 있어야 한다(`--include-mismatch` 옵션: 이전 step의 정의 수정으로 그 3건이 더 이상 불일치가 아님을 반영한 값을 얻기 위한 것이다).

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

- 결과를 파일에 저장하거나 `manifest.json`·`grades.json`·`observations.jsonl`·`status.json`을 바꾸지 마라. 이유: 공식 결과는 등록 규칙 기준 실행 원본이다. 이 도구의 출력은 참고값이다.
- 이 도구의 출력(참고값)을 공식 TCR로 쓰거나 가드를 우회해 `m3-grading` 실행 결과로 기록하지 마라.
- N을 등록하는 파일을 만들거나 N을 코드에 고정하지 마라(ADR-014: 등록값은 dev 20 실행 뒤 별도 등록).
- 예상과 다른 재채점 결과를 보고 규칙을 조정하지 마라(FP-043). 보고하고 멈춘다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`의 파일(실행 원본 증거)을 수정하지 마라. 이 step의 테스트는 그 디렉토리를 읽기만 한다.
- `docs/results/hypotheses.md`·`docs/results/hypotheses-m3.md`·`data/replay/golden/expectations.json`·`docs/results/m3-dev-supersedes.json`(사전 등록 파일)을 수정하지 마라. 이유: 바꾸면 새 등록이다(FP-043).
- 기존 테스트를 약화하지 마라. 시그니처 변경 때문에 필요한 호출 수정만 허용한다. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

# Step 1: run-guards

## 읽어야 할 파일

- `/AGENTS.md`(개발 프로세스의 "커밋 전 `make verify`" 줄, 명령어), `/scripts/execute.py`(`_check_clean_worktree`, 실행 잠금), `/scripts/test_execute.py`, `/scripts/hooks/verify-before-stop.sh`, `/scripts/test_hooks.py`
- `/src/govops/evalrun/dev.py`(`guard_live`, `git_metadata`, `_git`), `/src/govops/evalrun/dev_m3.py`(`git_metadata_m3`, `guard_live_m3`, attempts 기록 `attempt = {...}` 부근, 안전 오류 코드 목록), `/src/govops/evalrun/faults.py`(`guard_fault_registration`, 시작 시 청결 검사, `record["attempts"]`), `/src/govops/agent/checks.py`(`CheckResult.reason`), `/src/govops/api/tasks.py`(`_view`의 `check`·`rejected_edit`)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-031·054) — 읽기만 한다
- **외부 계약 실측**(원자료: 위 파일들). 성공 여부와 반환 내용을 따로 적는다:

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| `scripts/execute.py::_check_clean_worktree` | `git status --porcelain` exit 0 | `phases/` 접두 경로만 건너뛰고 나머지 줄이 있으면 중단한다(rename은 `old -> new`의 new 사용) |
| `evalrun/dev.py:144`, `dev_m3.py:404`(`git_metadata_m3`), `faults.py:866` 부근 | `git status --porcelain` | 출력이 비어 있어야 `worktree_clean`이 true. 원장 변경도 더러운 것으로 본다(FP-031) |
| `scripts/hooks/verify-before-stop.sh` | `git status --porcelain` | 출력이 비어 있으면 통과, 아니면 `make verify`를 돌린다 |
| `evalrun/dev_m3.py`의 attempt 기록 | — | 키: `attempt`·`trace_id`·`infrastructure_failure`·`budget_exceeded`·`llm_recorded`·`a_calls`·`a_skipped`·`tool_trace_mismatch`·`models_seen`·`unexpected_http`·`timed_out`·`late_tool_calls`. 결정론 검사 차단 사유는 없다 |

## 작업

**수정할 파일**(이 목록이 전부다): `scripts/exempt_paths.json`(신규, 단일 출처), `scripts/execute.py`, `scripts/test_execute.py`, `scripts/hooks/verify-before-stop.sh`, `scripts/test_hooks.py`, `src/govops/evalrun/worktree.py`(신규), `src/govops/evalrun/dev.py`, `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `tests/test_evalrun_worktree.py`(신규), `tests/test_evalrun_dev.py`·`tests/test_evalrun_dev_m3.py`·`tests/test_evalrun_faults.py`(위 변경으로 깨지는 기대값 갱신과 신규 테스트 추가에 한함), `AGENTS.md`. 계층: `evalrun`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import할 수 없다. `scripts/`는 `src/govops`를 import하지 않는다(독립 실행).

열람 금지 경로: (없음)
수정 허용 경로: `scripts/exempt_paths.json`, `scripts/execute.py`, `scripts/test_execute.py`, `scripts/hooks/verify-before-stop.sh`, `scripts/test_hooks.py`, `src/govops/evalrun/worktree.py`, `src/govops/evalrun/dev.py`, `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `tests/test_evalrun_worktree.py`, `tests/test_evalrun_dev.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults.py`, `AGENTS.md`

**배경**: 사용자 실행 명령이 비용 원장(`data/manifest/costs.jsonl`)에 행을 추가하면 청결 검사(`dirty_worktree`)가 phase 실행·live 실행을 막아, 원장만 올리는 PR이 M3에서 반복됐다(FP-031). 원장을 제외하고, 시작 시 원장 상태를 manifest에 남겨 쌓인 변경이 다음 결과 커밋에 함께 들어가게 한다. **예외는 원장 `data/manifest/costs.jsonl` 하나뿐이다**(`costs_grading.jsonl`·`docs/`·`data/results/`·`data/replay/`는 제외하지 않는다: 테스트가 읽는다). TDD.

### 1. 단일 출처 (`scripts/exempt_paths.json`)

`{"ledger_paths": ["data/manifest/costs.jsonl"]}` 한 파일을 예외 목록의 **유일한 정의**로 둔다. 아래 다섯 곳이 이 파일을 읽는다(경로 문자열을 코드·문서에 복사하지 않는다. 단, AGENTS.md 문장에는 사람이 읽도록 경로를 한 번 적되 테스트로 파일과의 일치를 확인한다).

1. `scripts/execute.py::_check_clean_worktree`: `phases/` 접두에 더해, 변경 경로가 **정확히** 예외 목록의 경로와 일치하는 줄은 건너뛴다(접두·부분 일치 금지, `data/manifest/costs.jsonl.bak` 같은 이름은 예외가 아니다).
2. `src/govops/evalrun/worktree.py`(신규): `dirty_entries(porcelain: str, root: Path) -> list[str]`(예외 경로를 뺀 더러운 항목), `ledger_state(root: Path) -> dict`(`{"lines": int, "sha256": str}`, 파일이 없으면 `{"lines": 0, "sha256": None}`). 예외 파일은 `root / "scripts/exempt_paths.json"`에서 읽고, 파일이 없거나 형식이 틀리면 **예외 없음으로 간주하지 않고** `ValueError("exempt_paths_invalid")`로 중단한다(청결 검사가 조용히 느슨해지거나 조용히 막히는 것을 구별한다).
3. `evalrun/dev.py`·`dev_m3.py`·`faults.py`의 청결 검사 세 곳: `worktree_clean`/`dirty_worktree` 판정이 `worktree.dirty_entries`를 쓴다. 기존 `dirty_worktree` 오류 코드는 그대로다.
4. `scripts/hooks/verify-before-stop.sh`: `git status --porcelain`의 모든 경로가 예외 목록에 있으면 `make verify` 없이 `exit 0`한다(`jq`로 `scripts/exempt_paths.json`을 읽는다. `jq`가 없거나 파일이 깨졌으면 예외 없음으로 보고 기존처럼 verify를 돈다). 예외 밖 경로가 하나라도 있으면 기존 동작이다.
5. `AGENTS.md`: 개발 프로세스의 "커밋 전에 `make verify`를 실행한다" 줄(CRITICAL 목록과 개발 프로세스 양쪽에 있으면 둘 다)에 예외 한 문장을 덧붙인다: "예외: 바뀐 파일이 비용 원장 `data/manifest/costs.jsonl` 하나뿐이면 로컬 검증을 생략한다. 원장은 다음 결과 커밋에 함께 넣고 원장만 올리는 PR은 만들지 않는다. 예외 목록의 단일 출처는 `scripts/exempt_paths.json`이다." `docs/`·`data/results/`·`data/replay/` 변경은 예외가 아니다.

### 2. 시작 시 원장 상태 기록

`dev_m3.py`와 `faults.py`의 manifest에 `ledger_at_start: {"lines": n, "sha256": "..."}`를 기록한다(`worktree.ledger_state`). 기록은 실행 **시작 시** 한 번이다(실행 중 원장은 계속 커진다). 두 값은 식별자·해시이며 PII 검사 대상이 아니다.

### 3. attempts에 `check_reason`

- `dev_m3.py`의 attempt 기록(`attempt = {...}`)에 `check_reason`(문자열 또는 `None`)을 추가한다. 값은 그 시도의 최종 API 뷰에서: `check.status == "blocked"`이면 `check.reason`, 아니면 `rejected_edit.reason`이 있으면 그것, 없으면 `None`. 값은 `agent/checks.py`의 고정 사유 코드 문자열이며 초안 원문이 아니다. 사유 코드 목록에 없는 값이 오면 `None`으로 기록하고 `unknown_check_reason` 플래그를 남기지 말고 그냥 `None`이다.
- `faults.py`의 `record["attempts"]` 항목에도 같은 키를 같은 규칙으로 추가한다.
- 기존 키와 값은 바꾸지 않는다(추가만). 기존 manifest를 읽는 도구(`evalrun/regrade.py` 등)가 새 키 없이도 동작해야 한다(키가 없으면 `None`).

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

- 예외 경로를 접두·부분·글롭으로 일치시키지 마라. 이유: `data/manifest/` 전체나 `costs_grading.jsonl`까지 예외가 되면 더러운 작업 트리로 live 실행이 시작된다(FP-031). 정확한 경로 문자열 일치만 허용한다.
- 예외 목록을 코드·셸 스크립트·문서에 복사하지 마라(AGENTS.md의 사람용 문장 한 곳만 예외, 일치 테스트 필수). 이유: 다섯 곳이 따로 정의하면 한 곳만 고쳐져 판정이 갈린다(FP-044).
- `scripts/test_execute.py`·`scripts/test_hooks.py`의 기존 단언을 약화하지 마라(`phases/` 예외·잠금·PID 판정 테스트 포함). 이유: Stop hook의 실행 잠금 판정(FP-057)을 깨면 phase 실행 중 세션이 막힌다.
- `check_reason`에 초안 원문·ID·메시지 본문을 넣지 마라. 이유: manifest는 커밋 대상이고 고정 사유 코드만 허용된다(AGENTS.md 감사·PII 규칙).
- 실행 중인 `execute.py`가 자기 자신(`scripts/execute.py`)을 고치는 step이다. 이 step 안에서 `python3 scripts/execute.py`를 실행하거나 `.harness/executing` 잠금 파일을 만들거나 지우지 마라. 이유: 실행 중 잠금을 건드리면 Stop hook 판정이 어긋난다(FP-057). 테스트는 임시 저장소(`tmp_path`)에서 한다.

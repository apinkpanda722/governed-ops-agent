# Step 0: golden-switch

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/agent/graph.py`(`AgentProfile`·`READ_ONLY`·`WITH_WRITES`·`WITH_WRITES_ORG`·`PROFILES`·`profile_for`), `/src/govops/golden.py`, `/src/govops/evalrun/regolden.py`, `/src/govops/evalrun/dev.py`(M2 러너), `/src/govops/evalrun/dev_m3.py`, `/src/govops/api/wiring.py`, `/.env.example`
- `/tests/test_golden_trace.py`, `/tests/test_golden_harness.py`, `/tests/test_golden_m3.py`, `/tests/test_evalrun_regolden.py`, `/tests/test_evalrun_dev.py`, `/tests/test_evalrun_dev_m3.py`, `/tests/test_evalrun_tool_trace.py`, `/tests/test_prereg_m3.py`, `/tests/test_agent_graph.py`, `/tests/test_api_wiring_build.py`, `/tests/test_docs_consistency.py`
- 근거(원자료): `data/replay/golden/status.json`(14개 모두 `profile: writes_org`, T3-01만 `recorded_fail`), `data/replay/llm/*.jsonl` 헤더 `prompt_hash`(14개 모두 `writes_org` 프롬프트 해시 `0a82447c…`), `data/results/dev/26c8553905bb4396b20ef2b2ff6399f1/manifest.json`(두 번째 dev 실행)

## 작업

**배경**: 두 번째 dev 실행(`writes_org`, `26c8553905bb4396b20ef2b2ff6399f1`) 뒤에 골든 14개를 `writes_org` 프로필로 다시 기록해 이미 커밋했다(`data/replay/` 21개 파일, 이 step 직전 커밋). 그 커밋은 아래 테스트 4건이 **첫 실행 기준(`writes` 프로필·T3-01 통과 가정) 그대로여서 실패하는 상태**다. 이 step은 그 테스트를 새 상태에 맞게 고치고, 더는 쓰지 않는 프로필·M2 실행 경로를 정리한다. `data/replay/` 기록 파일은 이미 새 상태이므로 **수정하지 않는다**.

시작하면 먼저 `make verify`(또는 해당 pytest)를 돌려 **실패하는 테스트 목록**을 기록해 두고(최종 보고에 그대로 적는다), 아래 순서로 처리한다. TDD: 기대 상태(새 프로필 하나만 존재, 골든은 `writes_org`)를 테스트로 먼저 쓴다.

### 1. 프로필 정리 (`agent/graph.py`, `golden.py`, `api/wiring.py`)

- 레지스트리 `PROFILES`에는 **`writes_org` 하나만** 남긴다(`read_only`·`writes` 키 삭제). `profile_for("read_only")`·`profile_for("writes")`는 `ValueError("invalid_profile")`.
- 기본 프로필(`golden.py`의 `profile` 기본값, `GOVOPS_AGENT_PROFILE`의 `env_or` 기본값, `record_status`·`replay_task`·`manifest_tasks`)을 `writes_org`로 바꾼다. `status.json`의 `profile` 값이 `writes_org`가 아니면 `invalid_profile`이다.
- `READ_ONLY`·`WITH_WRITES` 객체는 **`WITH_WRITES_ORG`의 구성 요소이거나 그래프 단위 테스트(`tests/test_agent_graph.py` 등)가 쓰는 동안에는 유지**한다. 레지스트리·기본값·API·골든에서만 빼는 것이 목적이다. 어디에서도 쓰지 않게 된 객체만 삭제하고, 무엇을 남기고 지웠는지와 이유를 최종 보고에 적는다.
- `.env.example`의 `GOVOPS_AGENT_PROFILE` 설명을 새 값 목록(`writes_org`)에 맞게 고친다(이름은 그대로).

### 2. 실패 테스트 4건을 새 상태 기준으로 교체

- 프로필 해시 테스트: `writes` 해시 14개 비교를 **`writes_org` 해시가 커밋된 `data/replay/llm/*.jsonl` 14개 전부의 헤더 `prompt_hash`와 같음**으로 교체한다(손으로 적은 해시 금지, 파일을 읽는다). `READ_ONLY` 해시 고정 테스트는 그 해시를 가진 프로필이 레지스트리에서 빠지므로 삭제를 허용한다(삭제 사실을 보고).
- 재골든 테스트 3건: 첫 실행 상태(T3-01 통과 등)를 가정한 기대값을 **두 번째 실행 기준**(`status.json` 그대로: T3-01 `recorded_fail`, 13개 pass)으로 바꾼다. 기대 상태는 `status.json`에서 읽거나 `expectations.json`(명세에서 계산)에서 가져오고 손으로 적은 합격 목록을 두지 않는다. `recorded_fail`은 "기록된 실패가 재생에서도 같은 실패로 재현됨"이 통과 조건임을 테스트가 보여 줘야 한다.
- `test_golden_trace`·`test_golden_harness`·`test_golden_m3`(14개 재생)는 통과해야 한다. **골든 재생이 하나라도 실패하면 테스트를 고치지 말고 `blocked`로 보고한다**(어느 task가 어떤 차이로 실패했는지 적는다).

### 3. M2 러너 정리 (`evalrun/dev.py`)

M2 러너는 읽기 전용 프로필 전제라 더 쓰지 않는다. **공용 헬퍼는 유지하고 실행 경로만 삭제**한다.

- **유지**(`evalrun/dev_m3.py`가 import한다): `select_tasks`·`guard_live`·`infrastructure_failure`·`resolve_index_name`·`git_metadata`·`mock_seed_check`·`read_seed_matches`·`_git`·`_write` 등 `dev_m3.py`가 import하는 모든 이름과 그 의존.
- **삭제**: `run_dev`·`_live`·`main`과 그 전용 보조(`_Capture` 등 dev_m3가 쓰지 않는 것). `Makefile`에 M2 러너를 부르는 타깃이 있으면 `Makefile`은 **수정하지 말고** 보고만 한다.
- **테스트**: `tests/test_evalrun_dev.py`에서 삭제한 실행 경로 전용 테스트만 삭제한다. 유지한 헬퍼의 테스트는 그대로 둔다. `tests/test_evalrun_tool_trace.py`·`tests/test_prereg_m3.py`가 `evalrun.dev`에서 import하는 이름이 남아 있는지 확인한다.
- **보존**: `docs/results/2026-10-05-m2-dev-live.md`와 `data/results/dev/`의 M2 기록은 수정·삭제하지 않는다.

### 4. 확인

- 레지스트리·기본값 테스트: `PROFILES`의 키가 `{"writes_org"}`, `profile_for` 오류, `GOVOPS_AGENT_PROFILE`의 값(`writes_org`·잘못된 값·빈 값=기본값).
- `grep -rn "read_only\|\"writes\"" src tests`로 남은 참조를 점검하고, 남긴 것은 이유를 보고한다.

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

## 금지사항

- `data/replay/` 아래 파일(`status.json`·`expectations.json`·기록)을 수정하지 마라. 이유: 이미 커밋된 두 번째 실행 기준 증거다. 테스트가 실패하면 기록을 바꾸지 말고 테스트를 새 상태에 맞게 고친다(골든 재생 자체가 실패하면 `blocked`).
- 골든 테스트를 통과시키려고 기대값을 완화하거나 `status.json`을 읽지 않고 손으로 적은 목록으로 바꾸지 마라(FP-056·FP-020).
- 무작위·시간·ID를 `monkeypatch`로 고정해 간헐적 실패를 덮지 마라(FP-011). 이유: 무작위 입력에서만 나타나는 실패는 실제 결함이다. 고정이 필요하면 사유 주석을 달고 시드 고정 대량 테스트를 함께 둔다.
- 기본값이 있는 환경 변수를 `os.environ.get(이름, 기본값)`으로 읽지 마라(FP-030). 이유: 빈 값이 기본값을 덮는다. `govops.env.env_or`를 쓴다.
- git 이력·시간·I/O 지연에 의존하는 테스트를 로컬 통과만으로 끝내지 마라(FP-032). 이유: CI(얕은 복제·느린 디스크)에서 달라진다. 가상 시간 폴링은 `real_tick`처럼 실제로 조금 기다리고, 느린 I/O를 주입해 재현한 뒤 고친다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- 이 step에서 `data/results/dev/` 아래 디렉토리와 `docs/results/`의 결과 문서를 수정·삭제하지 마라. 이유: 첫·두 번째 dev 실행은 보존할 증거다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

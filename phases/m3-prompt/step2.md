# Step 2: dev-run-org

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-011·019·020), `/docs/EVAL.md`("golden trace", "탐색 예산 (M5, ADR-014)"), `/docs/results/hypotheses.md`(§9·§10), `/docs/results/hypotheses-m3.md`, `/docs/results/hypotheses-m3-grading.md`, `/docs/results/2026-10-06-m3-dev-live.md`(T4-06 원인), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/dev_m3.py`(`PROFILE`, `REGISTRATION_FILES`, `guard_live_m3`, `git_metadata_m3`, `DEV_GRADING_RULES`, 프로필 확인 `profile_mismatch`, 골든 `status`의 `profile`, manifest 필드), `/src/govops/eval/run_grade.py`(`guard_grading_rules`, `REGISTERED`, `M3_GRADING`), `/src/govops/golden.py`(`record_status`), `/src/govops/agent/graph.py`(`PROFILES`·`profile_for`, 이전 step), `/tests/test_evalrun_dev_m3.py`, `/tests/test_prereg_m3.py`(첫 등록 파일 묶음의 계약), `/tests/test_evalrun_preflight.py`
- 근거(원자료): `docs/results/hypotheses-m3.md`·`data/replay/golden/expectations.json`·`docs/results/m3-dev-supersedes.json`(첫 등록, 등록 커밋 `e95f39430deb2b54a37079a31d6d711b06fa1558`, 읽기만), `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/manifest.json`(첫 실행 manifest 필드)

## 작업

**배경**: 첫 dev 실행(`da337bb7471c4bceae14a2e9b3ce5884`)에서 T4-06이 실패했다. 모델이 `run_sql`을 `SELECT status, COUNT(*) AS ticket_count FROM tickets WHERE owner_team = '플랫폼개발팀' GROUP BY status`로 실행해 0행이 나왔다(근거: `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/traces/c34f0ece3d9e4fc081b01a2339d6adda.jsonl`의 `run_sql` 인자와 결과). 원장의 `owner_team`은 영문 코드(`data/seed/tickets.json`)이고 `data/contract/run_sql.v1.json` 설명에는 값 목록이 없다. 그래서 시스템 프롬프트에 조직 스키마 정보를 보완하는 새 프로필 `writes_org`를 **기존 `writes` 프로필 옆에** 둔다. 기존 `writes`의 프롬프트·도구 정의는 한 글자도 바꾸지 않는다(커밋된 골든 14개가 그 프롬프트 해시로 기록되어 있어, 바꾸면 CI가 "재기록 필요"로 실패한다).

dev 러너를 `writes_org` 프로필·`m3-grading` 공식 채점으로 전환하고, 등록 가드를 **등록 묶음 두 개**로 나눈다(첫 등록은 그대로 유지, 두 번째 등록은 새 파일). 사전 등록 파일 자체는 다음 step이 만든다. TDD로 테스트를 먼저 쓴다.

### 1. 프로필·채점 규칙

- `PROFILE = PROFILES["writes_org"]`(이전 step의 레지스트리). 러너가 만든 그래프의 `agent_profile.name`이 `"writes_org"`가 아니면 `profile_mismatch`로 중단한다(두 곳의 `!= "writes"` 확인을 `PROFILE.name`과 비교로). manifest `profile`과 골든 `status.json`에 쓰는 `profile` 값은 `"writes_org"`다. `prompt_hash`·`RecordingChatClient`의 `system_prompt`·`tools`는 `PROFILE`에서 가져온다(이미 그렇다).
- `DEV_GRADING_RULES = M3_GRADING`. 러너 manifest 작성은 이미 `guard_grading_rules(root, DEV_GRADING_RULES)`의 결과(`grading_rules_sha256`·`grading_rules_registration_commit`)를 기록하고 `golden.record_status`·관측 채점에 같은 규칙 버전을 넘긴다(dev 채점과 골든 판정이 같은 규칙 버전, FP-044). 첫 실행의 공식값(`registered`)과 첫 실행 결과는 바꾸지 않는다.

### 2. 등록 묶음 두 개와 가드

- 첫 등록 묶음 `FIRST_REGISTRATION_FILES = ("docs/results/hypotheses-m3.md", "data/replay/golden/expectations.json", "docs/results/m3-dev-supersedes.json")`(현재 `REGISTRATION_FILES`와 같은 값, 이름만 바꾼다). 두 번째 등록 묶음 `PROMPT_REGISTRATION_FILES = ("docs/results/hypotheses-m3-prompt.md", "docs/results/m3-dev2-supersedes.json")`.
- 각 묶음은 **자기 등록 커밋**(묶음의 첫 파일을 마지막으로 바꾼 커밋: `git log -1 --format=%H -- <첫 파일>`)에서 모든 파일이 추적·커밋되어 있고, 그 커밋 이후 `git diff <등록 커밋>..HEAD -- <묶음 파일들>`이 비어 있어야 한다. 하나라도 어긋나면 `guard_live_m3`가 중단한다: 첫 묶음 `hypotheses_m3_not_committed`(기존 코드), 두 번째 묶음 `hypotheses_m3_prompt_not_committed`. 작업 트리가 깨끗하지 않으면 `dirty_worktree`(기존).
- `git_metadata_m3`의 manifest 필드: 기존 `hypotheses_m3_committed`·`hypotheses_m3_unchanged`·`hypotheses_m3_registration_commit`·`hypotheses_m3_sha256`는 **첫 묶음 값 그대로**, 새 필드 `hypotheses_m3_prompt_committed`·`hypotheses_m3_prompt_unchanged`·`hypotheses_m3_prompt_registration_commit`·`hypotheses_m3_prompt_sha256`(`hypotheses-m3-prompt.md`의 sha256)를 추가한다.
- **supersedes 사슬**: manifest `supersedes`는 `docs/results/m3-dev2-supersedes.json`의 내용, 새 필드 `previous_supersedes`는 `docs/results/m3-dev-supersedes.json`의 내용이다(두 번째 실행이 첫 실행을, 첫 실행이 M2 실행을 대체한 사슬).
- `tests/test_prereg_m3.py`가 검사하는 첫 등록 파일 묶음의 계약은 그대로 유지한다(첫 묶음 상수 이름만 맞춘다). 이 step에서는 두 번째 묶음의 **파일을 만들지 않는다**. 러너 테스트는 임시 git 저장소에 가짜 파일을 두어 가드를 검증한다.

### 3. 테스트 (가짜 환경·임시 git 저장소, 키·네트워크 불필요)

- `profile_mismatch`: 그래프가 `writes`(옛 프로필)나 `read_only`면 중단, `writes_org`면 통과.
- manifest·`status.json`의 `profile`이 `writes_org`, `grading_rules`가 `m3-grading`이고 `grading_rules_sha256`·`grading_rules_registration_commit`이 기록된다. 골든 `record_status`의 판정이 같은 규칙 버전으로 계산된다(T2-01형 관측으로 `m3-grading`이면 dev 채점과 골든이 둘 다 통과).
- 가드: 두 묶음 각각 (a) 미커밋, (b) 등록 이후 변경, (c) 작업 트리가 깨끗하지 않음, (d) 정상 통과. 두 번째 묶음이 없을 때 `hypotheses_m3_prompt_not_committed`. 첫 묶음의 기존 가드 테스트는 수정 없이 통과한다.
- `supersedes`·`previous_supersedes` 포함, 기존 20개 전 경로 테스트·`preflight`·`tool_trace`·`trace-isolation` 테스트는 프로필 이름 변경 외에는 수정 없이 통과한다.

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

- 첫 등록 묶음 파일(`hypotheses-m3.md`·`expectations.json`·`m3-dev-supersedes.json`)과 첫 실행 결과(`data/results/dev/da337bb7…`)를 수정하지 마라(FP-043). 가드는 읽기만 한다.
- `DEV_GRADING_RULES`나 `profile`을 환경 변수·기본값으로 선택하게 만들지 마라(FP-030·FP-044). 상수로 명시하고 manifest에 기록한다.
- 두 번째 등록 파일(`hypotheses-m3-prompt.md`·`m3-dev2-supersedes.json`)을 이 step에서 만들지 마라. 이유: 다음 step이 같은 커밋으로 만든다(등록 파일은 실행 전·같은 커밋, FP-043).
- 가드를 건너뛰는 옵션이나 `--smoke` 경로 밖의 우회를 만들지 마라. `--smoke`는 기록·manifest·가드를 요구하지 않는 기존 동작 그대로다.
- 중복 건수 0만 보고 합격시키지 마라(FP-040). 기존 절대 규칙 판정을 그대로 둔다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`m3-dev-supersedes.json`·`2026-10-06-m3-dev-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/` 아래의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 첫 dev 실행 기록과 골든 기록은 증거이며 두 번째 실행 직후 live 러너가 다시 만든다.
- 기존 테스트를 약화하지 마라. 이름·시그니처·프로필 이름 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

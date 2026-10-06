# Step 3: prereg-prompt

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-011·019·020), `/docs/EVAL.md`("golden trace", "탐색 예산 (M5, ADR-014)"), `/docs/results/hypotheses.md`(§9·§10), `/docs/results/hypotheses-m3.md`, `/docs/results/hypotheses-m3-grading.md`, `/docs/results/2026-10-06-m3-dev-live.md`(T4-06 원인), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/dev_m3.py`(`FIRST_REGISTRATION_FILES`·`PROMPT_REGISTRATION_FILES`·가드, 이전 step), `/src/govops/agent/org_info.py`·`/src/govops/agent/graph.py`(`PROFILES`, 해시 계산에 쓰는 프로필), `/src/govops/replay/llm.py`(`prompt_hash`), `/tests/test_prereg_m3.py`(첫 등록 테스트의 형식 참고), `/docs/results/hypotheses-m3-grading.md`(규칙 버전 `m3-grading`)
- 근거(원자료, 읽기만): `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/`(`manifest.json`·`grades.json`·`observations.jsonl`), `docs/results/2026-10-06-m3-dev-live.md`, `docs/results/m3-dev-supersedes.json`(첫 supersedes 형식), `data/seed/tickets.json`

## 작업

**배경**: 첫 dev 실행(`da337bb7471c4bceae14a2e9b3ce5884`)에서 T4-06이 실패했다. 모델이 `run_sql`을 `SELECT status, COUNT(*) AS ticket_count FROM tickets WHERE owner_team = '플랫폼개발팀' GROUP BY status`로 실행해 0행이 나왔다(근거: `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/traces/c34f0ece3d9e4fc081b01a2339d6adda.jsonl`의 `run_sql` 인자와 결과). 원장의 `owner_team`은 영문 코드(`data/seed/tickets.json`)이고 `data/contract/run_sql.v1.json` 설명에는 값 목록이 없다. 그래서 시스템 프롬프트에 조직 스키마 정보를 보완하는 새 프로필 `writes_org`를 **기존 `writes` 프로필 옆에** 둔다. 기존 `writes`의 프롬프트·도구 정의는 한 글자도 바꾸지 않는다(커밋된 골든 14개가 그 프롬프트 해시로 기록되어 있어, 바꾸면 CI가 "재기록 필요"로 실패한다).

**두 번째 사전 등록**을 만든다: `docs/results/hypotheses-m3-prompt.md`와 `docs/results/m3-dev2-supersedes.json`을 **이 step에서 같은 커밋으로** 만든다(등록 파일은 실행 전·같은 커밋, FP-043). 기존 `hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`expectations.json`·`m3-dev-supersedes.json`은 수정하지 않는다. TDD로 테스트를 먼저 쓴다.

### 1. `docs/results/m3-dev2-supersedes.json`

`m3-dev-supersedes.json`과 같은 형식: `{"reason": "...", "previous_run_id": "da337bb7471c4bceae14a2e9b3ce5884", "previous_files": {"manifest.json": "<sha256>", "grades.json": "<sha256>", "observations.jsonl": "<sha256>", "docs/results/2026-10-06-m3-dev-live.md": "<sha256>"}, "previous_commit": "<첫 실행 결과 디렉토리를 커밋에 추가한 커밋 해시>", "previous_official": {"grading_rules": "registered", "tcr": "17/20"}}`. `reason`: "시스템 프롬프트에 조직 스키마 정보(팀 한글 이름 ↔ owner_team 코드 ↔ project_key, 티켓 status·priority·ticket_type 값)를 보완해 dev 20을 처음부터 다시 실행한다(`hypotheses.md` §10). 첫 실행의 T4-06은 `run_sql`에 한글 팀 이름으로 `owner_team`을 걸러 0행이 나왔다." sha256은 **현재 파일에서 계산**한다(손으로 적지 않는다). `previous_commit`은 `git log --diff-filter=A -1 --format=%H -- data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/manifest.json`로 찾는다.

### 2. `docs/results/hypotheses-m3-prompt.md`

머리말: 작성 시점은 dev 두 번째 실행 전, 결과를 본 뒤 바꾸지 않는다, 바꾸면 새 등록이다(`hypotheses-m3.md` 형식). 항목:

1. **변경 내용과 이유**: 시스템 프롬프트에 "조직 정보" 절을 추가한다(새 프로필 `writes_org`, 기존 `writes`·`read_only`는 불변). 이유: 첫 실행 T4-06(한글 팀 이름으로 `owner_team`을 걸러 0행, 근거 경로 `data/results/dev/da337bb7…/traces/c34f0ece3d9e4fc081b01a2339d6adda.jsonl`, `data/contract/run_sql.v1.json`에 값 목록 없음, `data/seed/tickets.json`의 `owner_team`은 영문 코드).
2. **표의 출처와 범위**: 한글 팀 이름은 A `src/kodocs/synth/templates/system.md`(읽기 전용), 코드·값은 `data/seed/tickets.json`. 팀 ↔ `owner_team` ↔ `project_key` 8행과 `status`·`priority`·`ticket_type` 값 목록뿐이다. `data/eval/`·평가 정답·건수·티켓 ID·예시 SQL은 넣지 않는다. 표는 코드 상수 한 곳이며 시드의 값 집합과 일치하는지 테스트한다.
3. **평가 오염 방지**: 이 변경은 dev 결과를 근거로 한 조정이다. **test 문항을 실행하거나 결과를 보지 않았다.** test 결과를 보고 프롬프트·도구 설명을 조정하지 않는다. dev 결과를 근거로 한 조정은 허용하되 **등록 파일을 먼저 커밋하고 dev 전체를 다시 실행**한다.
4. **추가 변경 절차**: test 전까지 프롬프트·도구 설명을 더 바꾸면 같은 절차(새 등록 파일 + dev 전체 재실행 + 이전 결과 보존)를 따르고 **바꾼 횟수와 이유를 결과 문서에 모두 적는다**. 지금까지 바꾼 횟수: 1회(이 등록).
5. **dev 재실행**: dev 20을 처음부터 다시 실행한다(`hypotheses.md` §10). 첫 실행 결과(`da337bb7…`)는 공식 기록으로 보존한다(`m3-dev2-supersedes.json`에 사유·sha256·커밋). 첫 실행의 공식값은 등록 규칙(`registered`) 기준 TCR 17/20이다.
6. **두 번째 실행의 공식 채점**은 `hypotheses-m3-grading.md` 규칙 버전(`m3-grading`)이다(manifest에 `grading_rules`·`grading_rules_sha256`·등록 커밋). 참고로 등록 규칙(`registered`) 재채점 값을 나란히 낸다. 골든 14개는 두 번째 실행 직후 `writes_org` 프로필로 재기록하고 `status.json`은 `m3-grading` 기준이다. 골든 `expectations.json`은 명세에서 계산한 값이라 그대로 둔다.
7. **N 등록값**은 두 번째 실행(`writes_org`)의 `tool_results`로 산출한다(ADR-014). 첫 실행 N 후보(2)는 참고값이다.
8. 프로필 이름: 러너 레이블 `writes_org`, `GOVOPS_AGENT_PROFILE=writes_org`. 골든 `status.json`의 `profile` 값은 두 번째 실행 뒤 모두 `writes_org`가 된다.
9. 두 번째 실행 결과 문서와 목표 수치(TCR·정확도·지연)는 실행 뒤, test 전에 별도로 등록한다(이 파일에 없다).

### 3. 테스트

- `m3-dev2-supersedes.json`의 sha256이 현재 첫 실행 결과 파일·결과 문서와 일치하고 `previous_commit`이 실제로 그 디렉토리를 추가한 커밋이다(**git 이력 의존 테스트이므로 CI의 전체 체크아웃을 전제**한다. CI 설정은 이미 `fetch-depth: 0`이다). `previous_official`이 첫 실행 manifest·`grades.json`과 모순되지 않는다(`grading_rules` 필드가 manifest에 없는 첫 실행은 `registered`로 간주).
- `hypotheses-m3-prompt.md`가 필수 항목(위 1~9의 핵심 키워드: `writes_org`, `test 문항을 실행하거나 결과를 보지 않았다`, `바꾼 횟수`, `§10`, `m3-grading`, `N 등록값`)을 포함한다. 문구 일치가 아니라 키워드 확인이다.
- 두 파일의 경로가 러너의 `PROMPT_REGISTRATION_FILES`와 일치한다. 이 step의 커밋에 두 파일이 함께 들어 있어야 하고(같은 커밋은 하네스가 step 단위로 커밋하므로 두 파일을 이 step에서 만든다), 러너 가드 테스트(이전 step)가 이 실제 파일로도 통과 경로를 갖는다(임시 저장소가 아니라 현재 저장소의 정상 통과 케이스는 **작업 트리가 커밋된 뒤**에만 의미가 있으므로 이 step의 테스트는 파일 존재·내용·sha256 검증으로 한정한다).
- 골든 기록 파일·`expectations.json`·`status.json`은 만들거나 바꾸지 않는다.

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

- 기존 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`m3-dev-supersedes.json`·`expectations.json`)과 첫 실행 결과·결과 문서를 수정하지 마라(FP-043). 새 등록은 새 파일로 쌓는다.
- sha256·커밋 해시를 손으로 적지 마라(FP-022). 계산값만 증거가 된다.
- 등록 파일에 두 번째 실행의 결과·목표 수치(TCR·정확도·지연)를 넣지 마라. 이유: 결과를 보기 전에 등록한다.
- test 문항의 내용·결과를 근거로 쓰지 마라(FP-043). 이 변경의 근거는 dev T4-06뿐이다.
- 두 등록 파일을 서로 다른 step에서 나누어 만들지 마라(같은 커밋 규칙).
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`m3-dev-supersedes.json`·`2026-10-06-m3-dev-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/` 아래의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 첫 dev 실행 기록과 골든 기록은 증거이며 두 번째 실행 직후 live 러너가 다시 만든다.
- 기존 테스트를 약화하지 마라. 이름·시그니처·프로필 이름 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

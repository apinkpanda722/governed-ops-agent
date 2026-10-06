# Step 0: org-info

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-011·019·020), `/docs/EVAL.md`("golden trace", "탐색 예산 (M5, ADR-014)"), `/docs/results/hypotheses.md`(§9·§10), `/docs/results/hypotheses-m3.md`, `/docs/results/hypotheses-m3-grading.md`, `/docs/results/2026-10-06-m3-dev-live.md`(T4-06 원인), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/agent/prompts.py`, `/src/govops/agent/graph.py`(`AgentProfile`·`READ_ONLY`·`WITH_WRITES`), `/src/govops/tools/registry.py`(`jira_search_tickets`의 `project`·`status` 입력), `/tests/test_agent_data_boundary.py`(`agent/`의 `govops.eval`·`data/eval` 금지)
- 근거(원자료, 판단은 이 파일들로만): `data/seed/tickets.json`(`owner_team`·`project_key`·`status`·`priority`·`ticket_type` 값), `data/seed/users.json`(`team` 값), A 저장소 `~/PycharmProjects/ko-docs-agentic-rag/src/kodocs/synth/templates/system.md`(읽기 전용: 한글 팀 이름 8개가 첫 줄에 있다), `data/contract/run_sql.v1.json`

## 작업

**배경**: 첫 dev 실행(`da337bb7471c4bceae14a2e9b3ce5884`)에서 T4-06이 실패했다. 모델이 `run_sql`을 `SELECT status, COUNT(*) AS ticket_count FROM tickets WHERE owner_team = '플랫폼개발팀' GROUP BY status`로 실행해 0행이 나왔다(근거: `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/traces/c34f0ece3d9e4fc081b01a2339d6adda.jsonl`의 `run_sql` 인자와 결과). 원장의 `owner_team`은 영문 코드(`data/seed/tickets.json`)이고 `data/contract/run_sql.v1.json` 설명에는 값 목록이 없다. 그래서 시스템 프롬프트에 조직 스키마 정보를 보완하는 새 프로필 `writes_org`를 **기존 `writes` 프로필 옆에** 둔다. 기존 `writes`의 프롬프트·도구 정의는 한 글자도 바꾸지 않는다(커밋된 골든 14개가 그 프롬프트 해시로 기록되어 있어, 바꾸면 CI가 "재기록 필요"로 실패한다).

**조직 정보 표**와 프롬프트 절을 만든다. 프로필 연결은 다음 step이다. TDD로 테스트를 먼저 쓴다.

### `src/govops/agent/org_info.py`

```python
@dataclass(frozen=True) class TeamInfo: name_ko: str; owner_team: str; project_key: str
TEAMS: tuple[TeamInfo, ...]            # 코드 상수 한 곳(8팀)
TICKET_STATUS: tuple[str, ...]         # open, in_progress, resolved, closed
TICKET_PRIORITY: tuple[str, ...]       # critical, high, medium, low
TICKET_TYPE: tuple[str, ...]           # incident, task, request, improvement
def org_section() -> str               # 시스템 프롬프트에 붙일 "조직 정보" 절
```

- 팀 표(한글 이름 = `owner_team` 코드 = `project_key`): 플랫폼개발팀=platform=PLT, 결제서비스팀=payments=PAY, 데이터AI팀=data-ai=DAI, SRE인프라팀=sre=SRE, 정보보안팀=security=SEC, 인사팀=hr=HRM, 재무팀=finance=FIN, 법무컴플라이언스팀=legal=LGL. 티켓 `status`는 `open`, `in_progress`(진행 중), `resolved`, `closed`, `priority`는 `critical`·`high`·`medium`·`low`, `ticket_type`은 `incident`·`task`·`request`·`improvement`.
- `org_section()`은 **짧은 한국어 절**이다(표를 한 줄씩 나열하는 정도, 장황한 설명 없음). 내용: 팀 한글 이름과 `owner_team` 코드·`project_key`의 대응, 티켓 `status`·`priority`·`ticket_type`의 값 목록(`in_progress`는 "진행 중" 뜻이라고 표기), 그리고 "`run_sql`의 `tickets`·`approvals` 조건과 `jira_search_tickets`의 `project`·`status`에는 한글 팀 이름이 아니라 영문 코드 값을 쓴다"는 한 문장. **건수·티켓 ID·문서 내용·예시 SQL·질문별 정답 값은 넣지 않는다.**
- 이 모듈은 상수와 순수 함수만 둔다. 파일을 읽거나 `data/`를 열지 않고 `govops.eval`·`data/eval`을 참조하지 않는다(`tests/test_agent_data_boundary.py`가 계속 통과).

### 테스트

- **시드와의 일치**(테스트 코드는 `data/seed/`를 읽어도 된다): `TEAMS`의 `owner_team` 집합이 `data/seed/tickets.json`의 `owner_team` 집합과 같고, 각 티켓의 (`owner_team`, `project_key`) 쌍이 표와 정확히 일치한다. `TICKET_STATUS`·`TICKET_PRIORITY`·`TICKET_TYPE`이 시드의 값 집합과 같다. `owner_team` 코드 집합이 `data/seed/users.json`의 `team` 집합과 같다.
- **정답 값 비포함**: `org_section()`에 숫자(`0-9`)가 하나도 없다(건수·ID·날짜가 섞이지 않았음을 막는다). 한글 팀 이름 8개와 영문 코드 8개, `project_key` 8개가 모두 들어 있고 `SELECT`·`COUNT` 같은 예시 SQL이 없다. 값은 코드 상수에서 렌더되며 같은 입력에 같은 문자열이다(결정론).
- **한글 팀 이름의 출처 확인**(선택, A 저장소가 있을 때만): 환경 변수 `A_ROOT`(`env_or`, 기본 `~/PycharmProjects/ko-docs-agentic-rag`)에 `system.md`가 있으면 8개 이름이 모두 그 파일에 있음을 확인하고, 없으면 `pytest.skip`한다(CI에서는 건너뜀). 이 테스트는 A 저장소를 읽기만 한다.
- 계층 가드·`test_agent_data_boundary` 통과.

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

- 표를 `data/eval/`(평가 메타·정답 명세)나 평가 task에서 가져오지 마라(FP-042). 이유: 에이전트 실행 경로가 평가 정답을 보게 된다. 값의 출처는 `data/seed/`와 A의 `system.md`(읽기 전용)뿐이다.
- 프롬프트 절에 건수·특정 티켓·질문 유형별 힌트·예시 SQL을 넣지 마라(FP-043). 이유: dev 실패(T4-06)를 근거로 한 변경이지만 내용은 스키마 사실이어야 하고 어떤 task의 정답도 담으면 안 된다.
- 표의 값을 추측해 적지 마라(FP-022). 이유: 시드·계약 원자료와 일치 테스트로 검증한다.
- 이 step에서 `agent/graph.py`의 프로필·`prompts.py`의 기존 프롬프트를 수정하지 마라. 이유: 기존 프롬프트 해시가 바뀌면 골든 14개가 "재기록 필요"가 된다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`m3-dev-supersedes.json`·`2026-10-06-m3-dev-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/` 아래의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 첫 dev 실행 기록과 골든 기록은 증거이며 두 번째 실행 직후 live 러너가 다시 만든다.
- 기존 테스트를 약화하지 마라. 이름·시그니처·프로필 이름 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

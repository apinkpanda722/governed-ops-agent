# Step 4: prompt-docs

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-011·019·020), `/docs/EVAL.md`("golden trace", "탐색 예산 (M5, ADR-014)"), `/docs/results/hypotheses.md`(§9·§10), `/docs/results/hypotheses-m3.md`, `/docs/results/hypotheses-m3-grading.md`, `/docs/results/2026-10-06-m3-dev-live.md`(T4-06 원인), `/.claude/skills/failure-patterns/SKILL.md`
- `/docs/PRD.md`, `/AGENTS.md`(기술 스택·명령어 블록), `/.env.example`, `/tests/test_docs_consistency.py`, `/tests/test_makefile.py`
- 이전 step 산출물 전부: `/src/govops/agent/org_info.py`, `/src/govops/agent/graph.py`(`PROFILES`), `/src/govops/golden.py`, `/src/govops/api/wiring.py`, `/src/govops/evalrun/dev_m3.py`(등록 묶음·가드), `/docs/results/hypotheses-m3-prompt.md`, `/docs/results/m3-dev2-supersedes.json`

## 작업

**배경**: 첫 dev 실행(`da337bb7471c4bceae14a2e9b3ce5884`)에서 T4-06이 실패했다. 모델이 `run_sql`을 `SELECT status, COUNT(*) AS ticket_count FROM tickets WHERE owner_team = '플랫폼개발팀' GROUP BY status`로 실행해 0행이 나왔다(근거: `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/traces/c34f0ece3d9e4fc081b01a2339d6adda.jsonl`의 `run_sql` 인자와 결과). 원장의 `owner_team`은 영문 코드(`data/seed/tickets.json`)이고 `data/contract/run_sql.v1.json` 설명에는 값 목록이 없다. 그래서 시스템 프롬프트에 조직 스키마 정보를 보완하는 새 프로필 `writes_org`를 **기존 `writes` 프로필 옆에** 둔다. 기존 `writes`의 프롬프트·도구 정의는 한 글자도 바꾸지 않는다(커밋된 골든 14개가 그 프롬프트 해시로 기록되어 있어, 바꾸면 CI가 "재기록 필요"로 실패한다).

이 phase에서 확정된 결정을 문서에 반영한다. 코드는 수정하지 않는다(문서 일관성 테스트의 기대 문구 갱신이 필요하면 허용). 문서에는 **결정 내용만** 쓴다(누가 정했는지·변경 경위는 쓰지 않는다. "설계 검토"라고 쓰고 "조언 세션"은 쓰지 않는다).

- **ADR-021 추가** (`docs/ADR.md` 끝, 기존 서식, 상태 "유효", 날짜는 오늘): 제목 "M3 결정 — 시스템 프롬프트 조직 정보와 `writes_org` 프로필". 항목: ① 시스템 프롬프트에 조직 정보 절을 추가한다(팀 한글 이름 ↔ `owner_team` ↔ `project_key`, 티켓 `status`·`priority`·`ticket_type` 값, 출처: 한글 이름은 A `system.md`, 코드·값은 `data/seed/tickets.json`, 정답 값·예시 SQL 없음, `data/eval` 미사용) ② 새 프로필 `writes_org`를 `writes` 옆에 두고(레지스트리 `PROFILES`, 레이블 `read_only`·`writes`·`writes_org`) 기존 프로필의 프롬프트 해시를 보존한다(커밋된 골든 14개가 `writes` 해시로 기록되어 있어 CI가 깨지지 않음). `GOVOPS_AGENT_PROFILE`은 세 값을 받는다 ③ dev 20은 `writes_org`로 처음부터 다시 실행하고 공식 채점은 `hypotheses-m3-grading.md` 규칙 버전(`m3-grading`), 첫 실행 결과는 공식 기록으로 보존(supersedes 사슬: `m3-dev-supersedes.json` ← `m3-dev2-supersedes.json`) ④ 등록 묶음 두 개와 가드(`hypotheses_m3_not_committed`·`hypotheses_m3_prompt_not_committed`) ⑤ 프롬프트·도구 설명을 test 전에 더 바꾸면 새 등록 파일 + dev 전체 재실행 + 이전 결과 보존 절차를 따르고 바꾼 횟수와 이유를 결과 문서에 적는다, test 결과를 보고 조정하지 않는다 ⑥ N 등록값은 두 번째 실행의 `tool_results`로 산출 ⑦ 골든은 두 번째 실행 직후 `writes_org`로 재기록하고 `expectations.json`은 그대로 ⑧ 기본 프로필 전환과 옛 프로필(`read_only`·`writes`) 삭제는 다음 phase(`m3-faults`의 첫 step)에서 한다.
- `docs/ARCHITECTURE.md`: 디렉토리 구조에 `agent/org_info.py`를 추가하고 프로필 설명(`PROFILES` 레지스트리, `writes_org`)과 `GOVOPS_AGENT_PROFILE`의 값 목록, dev 러너의 등록 묶음·supersedes 사슬을 실제 구현에 맞게 고친다.
- `docs/EVAL.md`: golden trace 절의 `profile` 값(`read_only`·`writes`·`writes_org`)과 두 번째 실행 후 골든 재기록, 탐색 예산 절에 "N 등록값은 두 번째 실행(`writes_org`)의 `tool_results`로 산출한다"를 추가한다. 사전 등록 문장은 수정하지 않는다.
- `docs/PRD.md`: M3 행은 이미 있는 내용과 모순되지 않는지 확인하고 필요한 경우에만 고친다.
- `AGENTS.md`: **명령어 블록과 `LLM:` 줄 외에는 수정하지 마라.** 이 phase는 새 명령어가 없다(`make eval-run-dev`의 설명이 "dev 20 `WITH_WRITES`"로 되어 있으면 `writes_org`로 바꾼다).
- `.env.example`: 이전 step이 이미 고친 `GOVOPS_AGENT_PROFILE` 설명이 맞는지 확인만 한다.
- `tests/test_docs_consistency.py`·`tests/test_makefile.py`가 통과해야 한다(테스트를 약화하지 말고 문서를 맞춘다. 문구 단언이 구현을 따라 바뀌어야 하면 이유를 최종 응답 summary에 적는다).

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

- 결정 주체나 변경 경위를 문서에 쓰지 마라. 이유: 문서에는 결정 내용만 쓴다. "설계 검토"로 쓰고 "조언 세션"은 쓰지 않는다.
- `docs/results/`의 어떤 파일도 수정하지 마라(이 step은 `docs/ADR.md`·`docs/ARCHITECTURE.md`·`docs/EVAL.md`·`docs/PRD.md`·`AGENTS.md`(허용 부분)·`.env.example`(확인)만 다룬다).
- 장애 주입(F1~F11)의 표본 수·기대 결과를 이 step에서 정하지 마라. 이유: `m3-faults`에서 사전 등록한다.
- 코드·테스트를 수정하지 마라(문서 일관성 테스트 기대 문구 갱신만 예외).
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`m3-dev-supersedes.json`·`2026-10-06-m3-dev-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/` 아래의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 첫 dev 실행 기록과 골든 기록은 증거이며 두 번째 실행 직후 live 러너가 다시 만든다.
- 기존 테스트를 약화하지 마라. 이름·시그니처·프로필 이름 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

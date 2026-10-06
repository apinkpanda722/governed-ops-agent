# Step 1: profile-org

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-011·019·020), `/docs/EVAL.md`("golden trace", "탐색 예산 (M5, ADR-014)"), `/docs/results/hypotheses.md`(§9·§10), `/docs/results/hypotheses-m3.md`, `/docs/results/hypotheses-m3-grading.md`, `/docs/results/2026-10-06-m3-dev-live.md`(T4-06 원인), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/agent/graph.py`(`AgentProfile`, `READ_ONLY`, `WITH_WRITES`, `build_graph(profile=)`), `/src/govops/agent/prompts.py`, `/src/govops/agent/org_info.py`(이전 step), `/src/govops/golden.py`(`profile` 검증·`replay_task`·`record_status`·`manifest_tasks`: 현재 `profile == "writes"`와 `WITH_WRITES`를 직접 쓴다), `/src/govops/api/wiring.py`(`GOVOPS_AGENT_PROFILE` 처리), `/src/govops/evalrun/dev_m3.py`(`PROFILE = replace(WITH_WRITES, name="writes")`), `/src/govops/replay/llm.py`(`prompt_hash`), `/tests/test_golden_trace.py`, `/tests/test_golden_m3.py`, `/tests/test_agent_graph.py`, `/tests/test_api_wiring_build.py`, `/tests/test_docs_consistency.py`(프로필 문구 확인)
- 근거(원자료): `data/replay/llm/*.jsonl`(헤더 `prompt_hash`: 현재 14개 골든 기록이 모두 `writes` 프로필 해시), `data/results/dev/8c18df863b2949f58d9c3550fec3b8cf/manifest.json`(`prompt_hash` `e80d8393fde29e9a19affbfab533f9c049f40995508fa46086b411c91ed84a90` = `READ_ONLY` 프로필 해시), `data/replay/golden/status.json`(모든 항목 `profile: writes`)

## 작업

**배경**: 첫 dev 실행(`da337bb7471c4bceae14a2e9b3ce5884`)에서 T4-06이 실패했다. 모델이 `run_sql`을 `SELECT status, COUNT(*) AS ticket_count FROM tickets WHERE owner_team = '플랫폼개발팀' GROUP BY status`로 실행해 0행이 나왔다(근거: `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/traces/c34f0ece3d9e4fc081b01a2339d6adda.jsonl`의 `run_sql` 인자와 결과). 원장의 `owner_team`은 영문 코드(`data/seed/tickets.json`)이고 `data/contract/run_sql.v1.json` 설명에는 값 목록이 없다. 그래서 시스템 프롬프트에 조직 스키마 정보를 보완하는 새 프로필 `writes_org`를 **기존 `writes` 프로필 옆에** 둔다. 기존 `writes`의 프롬프트·도구 정의는 한 글자도 바꾸지 않는다(커밋된 골든 14개가 그 프롬프트 해시로 기록되어 있어, 바꾸면 CI가 "재기록 필요"로 실패한다).

새 프로필 `WITH_WRITES_ORG`와 **프로필 레지스트리**를 만든다. 골든 재생·API 조립이 이름으로 프로필을 해석하게 한다. 이 step에서는 dev 러너를 바꾸지 않는다. TDD로 테스트를 먼저 쓴다.

### 1. 프로필 (`agent/graph.py`)

- `WITH_WRITES_ORG = AgentProfile("with_writes_org", WITH_WRITES.system_prompt + <줄바꿈> + org_section(), WITH_WRITES.tools, True)`. 도구 정의는 `WITH_WRITES`와 같다. `READ_ONLY`·`WITH_WRITES`는 **한 글자도 바꾸지 않는다**.
- 레지스트리 `PROFILES: Mapping[str, AgentProfile]`(읽기 전용 매핑): 키는 **레이블**이다 — `"read_only"` → `READ_ONLY`, `"writes"` → `replace(WITH_WRITES, name="writes")`, `"writes_org"` → `replace(WITH_WRITES_ORG, name="writes_org")`. 각 항목의 `AgentProfile.name`은 키와 같다(러너가 `graph.agent_profile.name`으로 레이블을 확인한다). `replace`로 이름만 바꾸므로 프롬프트·도구 정의는 원본과 같다.
- `profile_for(label: str) -> AgentProfile`(알 수 없는 레이블은 `ValueError("invalid_profile")`).

### 2. 해석 지점 정리

- **`golden.py`**: `profile`을 `{"read_only", "writes", "writes_org"}` 중 하나로 검증하고(`record_status`·`replay_task`·`manifest_tasks`), 프롬프트·도구 정의·그래프 프로필을 `PROFILES[profile]`에서 가져온다. 코드의 `profile == "writes"`(쓰기 경로 여부) 조건은 `PROFILES[profile].allow_writes`로 바꾼다. `WITH_WRITES`를 직접 import해 쓰는 부분은 레지스트리로 대체한다. 기본 `profile`(필드가 없는 기존 항목)은 `read_only` 그대로다.
- **`api/wiring.py`**: `GOVOPS_AGENT_PROFILE`(`env_or`, 기본 `read_only`)이 레지스트리의 키(`read_only`·`writes`·`writes_org`)를 받는다. 알 수 없는 값은 `StartupError("GOVOPS_AGENT_PROFILE")`(기존 동작). `profile=profile_for(name)`.
- `.env.example`의 `GOVOPS_AGENT_PROFILE` 설명을 값 목록(`read_only`·`writes`·`writes_org`)에 맞게 고친다(이름은 그대로).

### 3. 테스트 (원자료로 해시를 고정한다)

- **`READ_ONLY` 해시 불변**: `replay.llm.prompt_hash(system_prompt=READ_ONLY.system_prompt, tools=READ_ONLY.tools, model="gpt-6-luna", params={})`가 **`e80d8393fde29e9a19affbfab533f9c049f40995508fa46086b411c91ed84a90`**(M2 첫 dev 결과 `manifest.json`의 값)과 같다. 파라미터는 M2·m3 러너가 쓴 모델·파라미터를 그대로 확인해 쓴다(`dev.py`·`dev_m3.py`의 `_MODEL`·`PARAMS`).
- **`writes` 해시 불변**: 커밋된 `data/replay/llm/*.jsonl` **14개 전부**의 헤더 `prompt_hash`가 `PROFILES["writes"]`의 `system_prompt`·`tools`로 계산한 해시와 같다(손으로 적은 해시를 쓰지 말고 파일을 읽는다).
- **`writes_org` 해시**: 위 두 해시와 다르고, 프롬프트가 `WITH_WRITES` 프롬프트로 시작해 `org_section()`으로 끝나며 도구 정의가 `WITH_WRITES`와 같다.
- 레지스트리: 키와 `AgentProfile.name` 일치, 알 수 없는 레이블 `ValueError`, 모든 프로필의 `allow_writes` 값(`read_only`는 False, 나머지는 True).
- **골든**: `profile: writes_org`로 기록한 합성 기록을 재생할 수 있고(합성 기록 테스트), `profile: writes`·`read_only` 재생은 이전과 같다. `test_golden_trace`·`test_golden_harness`·`test_golden_m3`는 **수정 없이** 통과한다(기록 14개가 모두 재생된다). `profile` 값이 레지스트리 밖이면 `invalid_profile`.
- `wiring`: `GOVOPS_AGENT_PROFILE`의 세 값과 잘못된 값.

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

- `READ_ONLY`·`WITH_WRITES`(`writes`)의 프롬프트·도구 정의·`prompts.py`의 기존 문자열을 바꾸지 마라. 이유: 커밋된 골든 14개의 프롬프트 해시가 달라져 CI가 "재기록 필요"로 실패한다. 해시를 직접 비교하는 테스트가 통과해야 한다. 골든 테스트를 고쳐서 통과시키지 마라(FP-056).
- 해시 테스트에서 손으로 적은 해시 문자열을 쓰지 마라(FP-020). 이유: 커밋된 실제 기록 파일(원자료)에서 읽는다. 예외는 M2 manifest의 `READ_ONLY` 해시 상수 하나이며 그 출처 경로를 주석에 적는다.
- `GOVOPS_AGENT_PROFILE`을 `os.environ.get(이름, 비어 있지 않은 기본값)`으로 읽지 마라(FP-030). `env_or`를 쓴다.
- 이 step에서 `evalrun/dev_m3.py`를 수정하지 마라. 이유: 러너 전환과 가드 정리는 다음 step이다(이 step은 CI가 계속 통과하는 상태로 끝나야 한다).
- `status.json`·`expectations.json`·기록 파일을 수정하지 마라.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`m3-dev-supersedes.json`·`2026-10-06-m3-dev-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/dev/` 아래의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 첫 dev 실행 기록과 골든 기록은 증거이며 두 번째 실행 직후 live 러너가 다시 만든다.
- 기존 테스트를 약화하지 마라. 이름·시그니처·프로필 이름 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

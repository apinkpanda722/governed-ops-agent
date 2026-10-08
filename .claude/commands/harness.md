이 프로젝트는 Harness 프레임워크를 사용한다. 아래 워크플로우에 따라 작업을 진행하라.

---

## 워크플로우

### 0. 프로젝트 셋업 (최초 1회)

하네스는 작업 방식(실행기, hook, 커맨드, 빈 골격 문서)만 제공하고 스택·도메인에 따라 달라지는 것은 제공하지 않는다. 첫 phase를 설계하기 전에 사용자와 함께 아래를 정하고 채운다:

1. **스택 골격** — 스택의 공식 도구(`uv init`, `create-next-app` 등)로 만든 골격이 있는지 확인한다. 없으면 골격 생성을 첫 phase의 step 0으로 둔다.
2. **`make verify`** — `Makefile`의 `verify` 타깃을 스택에 맞는 lint / typecheck / test로 채운다. Stop hook과 execute.py가 이 명령으로 완료를 판정한다. 골격 생성이 step 0이면 그 step에서 채운다.
3. **`AGENTS.md`** — 기술 스택, CRITICAL 규칙, 명령어를 채운다.
4. **`/docs/`** — PRD, ARCHITECTURE, ADR을 채우고 프로젝트 성격에 맞는 기준 문서를 추가한다. 예: UI가 있으면 UI 가이드, AI·검색 기능이 있으면 평가 문서(지표, golden set, 결과 기록 규칙).
5. **프로젝트 전용 hook (선택)** — 언어별 TDD guard처럼 스택에 묶인 hook은 프로젝트의 `scripts/hooks/`에 추가하고 `.claude/settings.json`, `.codex/config.toml`에 등록한다.

셋업 결과는 execute.py 실행 전에 커밋한다. 작업 트리가 깨끗하지 않으면 execute.py가 시작하지 않는다.

### A. 탐색

`/docs/` 하위 문서(PRD, ARCHITECTURE, ADR 등)를 읽고 프로젝트의 기획·아키텍처·설계 의도를 파악한다. 필요시 Explore 에이전트를 병렬로 사용한다.

### B. 논의

구현을 위해 구체화하거나 기술적으로 결정해야 할 사항이 있으면 사용자에게 제시하고 논의한다.

### C. Step 설계

사용자가 구현 계획 작성을 지시하면 여러 step으로 나뉜 초안을 작성해 피드백을 요청한다.

설계 원칙:

0. **실패 패턴 반영** — `.claude/skills/failure-patterns/SKILL.md`를 먼저 읽는다. step의 "수정할 파일"과 작업에 **실제로 해당하는** 항목(FP-NNN)만 골라 해당 step의 `## 금지사항`(공통 블록 아래의 step별 금지사항)에 **이유와 FP 번호를 함께** 옮겨 적는다. 관련 있을 법한 FP를 넓게 옮기지 않는다. 공통 블록에 이미 있는 내용은 step별로 반복하지 않는다. step별 금지사항은 5개 안팎으로 유지한다. Codex는 이 스킬을 읽지 않으므로 step 파일에 적지 않은 패턴은 전달되지 않는다.
1. **Scope 최소화** — 하나의 step에서 하나의 레이어 또는 모듈만 다룬다. 여러 모듈을 동시에 수정해야 하면 step을 쪼갠다.
2. **자기완결성** — 각 step 파일은 독립된 Codex 세션(`codex exec`)에서 실행된다. "이전 대화에서 논의한 바와 같이" 같은 외부 참조는 금지한다. 필요한 정보는 전부 파일 안에 적는다.
3. **사전 준비 강제** — 관련 문서 경로와 이전 step에서 생성/수정된 파일 경로를 명시한다. 세션이 코드를 읽고 맥락을 파악한 뒤 작업하도록 유도한다.
4. **시그니처 수준 지시** — 함수/클래스의 인터페이스만 제시하고 내부 구현은 에이전트 재량에 맡긴다. 단, 설계 의도에서 벗어나면 안 되는 핵심 규칙(멱등성, 보안, 데이터 무결성 등)은 반드시 명시한다.
5. **AC는 실행 가능한 커맨드** — "~가 동작해야 한다" 같은 추상적 서술이 아닌 `make verify` 같은 실제 실행 가능한 검증 커맨드를 포함한다. execute.py는 에이전트가 완료를 보고해도 `make verify`를 직접 다시 돌려 통과해야만 완료로 기록한다.
6. **주의사항은 구체적으로** — "조심해라" 대신 "X를 하지 마라. 이유: Y" 형식으로 적는다.
7. **네이밍** — step name은 kebab-case slug로, 해당 step의 핵심 모듈/작업을 한두 단어로 표현한다 (예: `project-setup`, `api-layer`, `auth-flow`).
8. **수정은 다음 계획된 phase의 step으로 넣는다** — 설계 검토나 live 결과에서 나온 수정마다 새 phase를 만들지 않는다. 코드는 계속 Codex 하네스에 일임한다(FP-053).
9. **step 파일 작성 뒤 자체 점검** — step마다 작업 서두에 **"수정할 파일" 목록**을 적는다(이전 step의 산출물을 고쳐야 하면 그 파일도 적는다). 그 목록과 작업·금지사항을 (1) 같은 파일의 금지사항과 작업의 모순(특히 "작성 요구"와 "실행·갱신 금지"를 구분했는가), (2) `AGENTS.md` 계층 경계 표와 `tests/test_architecture.py`의 `FORBIDDEN_IMPORTS`, (3) 읽기 전용 목록(`.claude/`·사전 등록 파일·`data/replay` 기록), (4) **외부 계약**(명령이 성공하는지(exit 0)와 기대한 값을 돌려주는지를 따로 실측해 적었는가)과 대조해 충돌을 고친 뒤 커밋한다. 추가로 확인한다: **공통 금지사항 블록을 수정 없이 복사했고 맨 앞 항목이 선점검인가**, 블록 내용을 step별 금지사항에 반복하지 않았는가, 옮긴 FP가 수정 파일·작업과 직접 관련되는가, step별 금지사항이 5개 안팎인가. 점검 결과(step별 수정 파일, 발견한 충돌과 고친 내용)를 사용자 보고에 넣는다(FP-054). **형식 v2(`m4-guardrail` phase부터 필수)**: 작업 절에 `열람 금지 경로:`·`수정 허용 경로:` 두 줄을 둔다. `scripts/test_step_files.py`가 기계로 검사한다 — ① `## 읽어야 할 파일`이 열람 금지 경로를 가리키면 실패, ② 금지사항의 실행 금지 문장은 `make 타깃 이름`·`python -m …` 같은 **명령을 하나씩 명시**해야 하고(범주 표현 "make 타깃"·"live 타깃"·글롭 `make smoke-*`은 실패) AC·검증 절차의 명령과 겹치면 실패, ③ `**수정할 파일**` 문단의 경로는 `수정 허용 경로` 줄에 모두 있어야 한다, ④ 수정 허용 경로가 바꾸는 모듈·파일을 쓰는 소비자 테스트(`tests/`·`scripts/`) 중 허용 목록에 없는 것은 **경고**로 출력된다(실패 아님). 설계 검토 때 `uv run python scripts/step_file_checks.py phases/<phase>/step*.md`로 경고 목록을 확인하고 필요한 테스트를 "~에 한함, 단언 약화 금지" 형식으로 허용 목록에 넣는다(FP-059~062). ⑤ **열람 금지의 범위**: `열람 금지 경로`가 `(없음)`이 아니면 `## 작업`에 `열람 금지의 범위:` 줄(본문 포함)이 있어야 한다. `m4-audit` 뒤의 phase와 새 phase는 누락이 실패이고 이전 완료 phase는 경고다(`scope_required`).
10. **소비자·정확 일치 단언 점검** — 설계 때 각 step의 변경이 (a) 같은 phase 앞 step이 만든 테스트의 가정(첫 반환값, 파일 이름, 레코드 형태)과 (b) 기존 테스트의 정확 일치 단언(`==`, `[-1]`, index 순서, 개수, `splitlines()`)을 깨는지 `rg -n "== \d+|\[-1\]|\[0\]|splitlines\(\)" tests/`와 `step_file_checks`의 소비자 경고로 모아 수정 허용 목록("~에 한함, 단언 약화 금지")과 설계에 반영한다. 타이밍을 바꾸는 변경(잠금·`fsync`·서명) 뒤에는 `asyncio.gather` 인덱스 단언을 **실제 보증과 같은 강도**의 불변식으로 쓴다(FP-012). 앞 step 단언이 뒤 step 계획과 충돌하지 않는지, 읽기 목록이 열람 금지와 충돌하지 않는지 확인한다(FP-054·059·060·061·063~066). 점검 결과를 사용자 보고에 step별로 넣는다.

### D. 파일 생성

사용자가 승인하면 아래 파일들을 생성한다.

#### D-1. `phases/index.json` (전체 현황)

여러 task를 관리하는 top-level 인덱스. 이미 존재하면 `phases` 배열에 새 항목을 추가한다.

```json
{
  "phases": [
    {
      "dir": "0-mvp",
      "status": "pending"
    }
  ]
}
```

- `dir`: task 디렉토리명.
- `status`: `"pending"` | `"completed"` | `"error"` | `"blocked"`. execute.py가 실행 중 자동으로 업데이트한다.
- 타임스탬프(`completed_at`, `failed_at`, `blocked_at`)는 execute.py가 상태 변경 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-2. `phases/{task-name}/index.json` (task 상세)

```json
{
  "project": "<프로젝트명>",
  "phase": "<task-name>",
  "steps": [
    { "step": 0, "name": "project-setup", "status": "pending" },
    { "step": 1, "name": "core-types", "status": "pending" },
    { "step": 2, "name": "api-layer", "status": "pending" }
  ]
}
```

필드 규칙:

- `project`: 프로젝트명 (AGENTS.md 참조).
- `phase`: task 이름. 디렉토리명과 일치시킨다.
- `steps[].step`: 0부터 시작하는 순번.
- `steps[].name`: kebab-case slug.
- `steps[].status`: 초기값은 모두 `"pending"`.

상태 전이와 자동 기록 필드:

| 전이 | 기록되는 필드 | 기록 주체 |
|------|-------------|----------|
| → `completed` | `completed_at`, `summary`, `usage` | Codex 최종 응답 (summary), execute.py (나머지) |
| → `error` | `failed_at`, `error_message`, `usage` | Codex 최종 응답 또는 `make verify` 출력 (message), execute.py (나머지) |
| → `blocked` | `blocked_at`, `blocked_reason`, `usage` | Codex 최종 응답 (reason), execute.py (나머지) |

index.json은 execute.py만 수정한다. Codex는 `scripts/step-result.schema.json` 형식(`status`, `summary`, `reason`)의 최종 응답만 돌려준다. `usage`는 해당 step의 누적 토큰 사용량과 시도 횟수다.

`summary`는 step 완료 시 산출물을 한 줄로 요약한 것으로, execute.py가 다음 step 프롬프트에 컨텍스트로 누적 전달한다. 따라서 다음 step에 유용한 정보(생성된 파일, 핵심 결정 등)를 담아야 한다.

`created_at`은 execute.py가 최초 실행 시 task 레벨에 한 번만 기록한다. step 레벨의 `started_at`도 execute.py가 각 step 시작 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-3. `phases/{task-name}/step{N}.md` (각 step마다 1개)

```markdown
# Step {N}: {이름}

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`
- {이전 step에서 생성/수정된 파일 경로}

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**: {이 step이 만들거나 고치는 파일 전체 목록}

열람 금지 경로: {Codex가 직접 열람·출력·인용하면 안 되는 경로를 백틱으로 감싸 쉼표로 나열. 없으면 `(없음)`}
수정 허용 경로: {위 "수정할 파일"의 경로를 백틱으로 감싸 쉼표로 나열(글롭 `tests/test_*.py` 가능). 이 줄과 문단이 일치해야 한다}
열람 금지의 범위: {열람 금지 경로가 `(없음)`이 아니면 필수(없으면 이 줄을 지운다). 금지는 Codex 세션의 직접 열람·출력·인용에 대한 것이고, 코드·테스트의 런타임 접근은 대상이 아님을 쓴다. 테스트가 실제 파일 내용을 읽는 것은 여전히 금지이며 예외(이름·크기·`mtime_ns` 등)를 여기 열거한다. 이 줄 뒤에 이어 쓸 수 있다}

{구체적인 구현 지시. 파일 경로, 클래스/함수 시그니처, 로직 설명을 포함.
코드 스니펫은 인터페이스/시그니처 수준만 제시하고, 구현체는 에이전트에게 맡겨라.
단, 설계 의도에서 벗어나면 안 되는 핵심 규칙은 명확히 박아넣어라.}

## Acceptance Criteria

```bash
make verify   # lint + test 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 (API 키, 외부 인증, 수동 설정 등) → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->

## 이 step의 금지사항

- {이 step의 수정 파일과 작업에 실제로 해당하는 금지사항만. "X를 하지 마라. 이유: Y(FP-NNN)" 형식, 5개 안팎. 위 공통 블록 내용은 반복하지 않는다.}
```

### E. 실행

실행 전에 phase 파일(`phases/`) 외의 변경사항은 모두 커밋해 둔다. 작업 트리가 깨끗하지 않으면 execute.py가 시작하지 않는다.

```bash
python3 scripts/execute.py {task-name}            # 순차 실행
python3 scripts/execute.py {task-name} --steps 2  # 2개 step만 실행하고 멈춤 (리뷰 단위로 끊어 진행)
python3 scripts/execute.py {task-name} --push     # 실행 후 push
```

execute.py가 자동으로 처리하는 것:

- `feature/{task-name}` 브랜치 생성/checkout
- Codex 실행 — `codex exec --sandbox workspace-write`로 step마다 새 세션을 띄운다. 워크스페이스 밖 쓰기는 OS 샌드박스가 막고, 의존성 설치를 위해 네트워크만 연다. 프로젝트 규칙(AGENTS.md)은 Codex가 자동으로 읽는다.
- 컨텍스트 누적 — 완료된 step의 summary를 다음 step 프롬프트에 전달
- 독립 검증 — 에이전트가 완료를 보고하면 `make verify`를 직접 실행하고, 실패하면 그 출력을 에러로 삼아 재시도
- 자가 교정 — 실패 시 최대 3회 재시도하며, 이전 에러 메시지를 프롬프트에 피드백
- 커밋 — 코드 변경(`feat`)과 메타데이터(`chore`)를 분리 커밋. 실패/차단된 step의 코드는 커밋하지 않고 작업 트리에 남긴다
- 기록 — started_at, completed_at, failed_at, blocked_at 타임스탬프와 step별 토큰 사용량(`usage`)

실행 방법과 대기(FP-057·058):

- `execute.py`는 Bash `run_in_background: true`로 실행한다. 종료하면 자동 알림이 오므로 그때까지 상태를 반복 조회하지 않는다. `(nohup … &)`처럼 분리된 프로세스는 종료 알림이 없다.
- 이미 분리돼 돌고 있을 때만 Monitor를 쓴다. 주기 출력 없이 프로세스 종료나 step 상태 변경 때 한 번만 알리는 until 루프로 쓴다.
- 실행 중에는 `.harness/executing`(PID, gitignore)이 있고 Stop hook은 검증을 건너뛴다. 중간 Stop hook 실패에는 대응하지 않는다(Codex가 편집 중인 파일을 고치지 않는다). `scripts/`·`.claude/`·`phases/` 수정은 실행이 끝난 뒤에 한다.

에러 복구:

- **error 발생 시**: 작업 트리에 남은 코드를 검토해 커밋하거나 stash한다. `phases/{task-name}/index.json`에서 해당 step의 `status`를 `"pending"`으로 바꾸고 `error_message`를 삭제한 뒤 재실행한다.
- **blocked 발생 시**: `blocked_reason`에 적힌 사유를 해결하고 작업 트리를 정리한 뒤, `status`를 `"pending"`으로 바꾸고 `blocked_reason`을 삭제한 뒤 재실행한다.

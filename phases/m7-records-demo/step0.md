# Step 0: freeze-pin

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M7_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m7-records-authz.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/docs/results/hypotheses-test-targets.md`(동결 규칙, 읽기만 한다)
- `/tests/test_makefile.py`, `/tests/test_repo_rules.py`(새 파일이 영향을 주지 않는지 확인용)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `scripts/freeze_digest.py`, `tests/test_m7_freeze.py`. TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`
수정 허용 경로: `scripts/freeze_digest.py`, `tests/test_m7_freeze.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`·`data/eval/`·`data/redteam/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. `scripts/freeze_digest.py`가 `git ls-files`로 얻은 추적 파일의 **바이트를 해시하는 것**(경로별 digest 계산)은 코드의 런타임 접근이므로 대상이 아니다. 단 그 파일의 내용을 출력·인용하거나 해시 계산 외의 목적으로 읽는 것은 금지다. 테스트는 임시 git 저장소와 경로별 digest 비교만 한다.

**배경**: 공식 test 동결 뒤 그래프·프롬프트·도구 설명·Guardrail 규칙·평가 러너·골든을 바꾸지 않는다. M7 phase가 이를 바꾸지 않음을 digest로 기계 확인한다. 이 step이 **가장 먼저** 실행된다.

### 1. `scripts/freeze_digest.py`

- 상수 `FREEZE_PATHS: tuple[str, ...]`: 아래 목록을 그대로 둔다(`5d4302b`에 존재하는 경로다).
  - 디렉터리: `src/govops/agent`, `src/govops/tools`, `src/govops/guardrails`, `src/govops/gateway`, `src/govops/broker`, `src/govops/eval`, `src/govops/evalrun`, `src/govops/replay`, `src/govops/mocks`, `data/contract`, `data/policy`, `data/replay`, `data/seed`, `data/eval`, `data/redteam`, `docs/results/m6-redteam-run`
  - 파일: `src/govops/costs.py`, `src/govops/golden.py`, `infra/litellm/config.yaml`, `src/govops/audit/__init__.py`, `src/govops/audit/log.py`, `src/govops/audit/crypto.py`, `src/govops/audit/anchor.py`, `src/govops/audit/verify.py`, `src/govops/audit/smoke.py`
  - 사전 등록 파일 17개(이름 명시, 글롭 금지, 모두 `docs/results/` 아래): `hypotheses.md`, `hypotheses-m3.md`, `hypotheses-m3-faults.md`, `hypotheses-m3-grading.md`, `hypotheses-m3-prompt.md`, `hypotheses-m4-guardrail.md`, `hypotheses-m4-redteam.md`, `hypotheses-m5-budget.md`, `hypotheses-m5-prompt.md`, `hypotheses-m6-api-authz.md`, `hypotheses-m6-finalize.md`, `hypotheses-m6-grading-r2.md`, `hypotheses-m6-guardrail-v2.md`, `hypotheses-m6-guardrail-v2-r2.md`, `hypotheses-m6-redteam-addendum.md`, `hypotheses-m6-redteam-run.md`, `hypotheses-test-targets.md`
- 상수 `EXCLUDED: frozenset[str]`: `docs/results/audit-anchors.jsonl`, `data/manifest/costs.jsonl`, `data/manifest/costs_grading.jsonl`(추가 전용 파일). `FREEZE_PATHS`의 어느 경로도 이 파일들을 포함하지 않음을 `digests`가 단언한다.
- `def digest_for(spec: str, root: Path) -> str`: `git ls-files -z -- <spec>`(`subprocess.run(..., cwd=root, timeout=30)`)로 **추적 파일**만 얻어 정렬하고, 각 파일의 `(경로, sha256)`를 `"<경로>\0<sha256>\n"` 형식으로 이어 붙인 문자열의 sha256 hex를 돌려준다. 추적 파일이 0개이면 `ValueError`(공허한 통과 방지, FP-069).
- `def digests(root: Path) -> dict[str, str]`: `FREEZE_PATHS`의 경로별 digest.
- `main()`: 경로별로 `"<경로> <digest>"` 한 줄만 출력한다. **파일 내용·파일별 해시를 출력하지 않는다.**

### 2. 상수 만들기

코드를 쓰고 나서 `python scripts/freeze_digest.py`를 한 번 실행해 경로별 digest를 얻고 `tests/test_m7_freeze.py`의 `EXPECTED: dict[str, str]`에 옮긴다. 실행 전에 `git diff --quiet 5d4302b -- <FREEZE_PATHS의 경로들>`로 동결 경로가 `5d4302b`와 같음을 확인하고, 다르면 상수를 만들지 않고 `blocked`로 보고한다(reason에는 경로 이름만 적는다).

### 3. `tests/test_m7_freeze.py`

- `test_expected_covers_exactly_the_freeze_paths`: `set(EXPECTED) == set(FREEZE_PATHS)`.
- `test_digests_match_expected`: `digests(repo_root) == EXPECTED`. 실패 메시지에는 **경로 이름만** 담는다.
- `digest_for` 단위 테스트(임시 git 저장소: `tmp_path`에서 `git init`·`git add`, 서브프로세스 timeout 명시): 파일 내용이 바뀌면 digest가 바뀌고 이름이 바뀌면 바뀌며 추적되지 않는 파일은 무시한다. 추적 파일 0개는 `ValueError`.
- `test_output_has_only_path_and_digest`: `main()` 출력의 모든 줄이 `^[^ ]+ [0-9a-f]{64}$`.
- `test_excluded_files_are_not_in_the_freeze_paths`.

**사용자 실행(이 step에서 실행하지 않는다)**: 없음.

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드)를 바꾸지 않았는가?
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

- `FREEZE_PATHS` 아래 파일을 수정하지 마라. 이유: 공식 test 동결 규칙(M6 7절 3항)이며 이 digest 테스트가 실패한다.
- `FREEZE_PATHS`에 글롭이나 `docs/results/hypotheses-*.md` 패턴을 쓰지 마라. 이유: 이 phase가 새 사전 등록 파일(`hypotheses-m7-records-authz.md`)을 추가하므로 글롭은 digest를 바꾼다.
- 스크립트·테스트가 `data/eval/`·`data/redteam/` 파일 내용을 출력하거나 해시 계산 외의 목적으로 읽지 마라. 이유: 정답지·레드팀 원문 비열람 규칙(FP-059, FP-042).
- digest 계산에 추적되지 않는 파일(`__pycache__` 등)을 포함하지 마라. 이유: 실행 환경에 따라 digest가 달라진다. `git ls-files` 기준만 쓴다.
- `5d4302b`와 다른 상태에서 상수를 만들지 마라. 이유: 상수가 동결 기준이 아니라 현재 상태가 된다.

## 소비자 점검(시뮬레이션 통과)

새 파일(`scripts/freeze_digest.py`, `tests/test_m7_freeze.py`)만 추가한다. `git clone --local` 복사본(`.env`·`data/interim`·`data/raw` 없음)에 스텁 파일을 넣고 전체 pytest(4256건)를 돌렸을 때 `scripts/test_step_files.py`·`tests/test_makefile.py`·`tests/test_repo_rules.py`가 새 파일 때문에 실패하지 않았다.

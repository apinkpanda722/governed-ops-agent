# Step 0: run-guards

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_REDTEAM_RUN_DESIGN_DRAFT.md`(이 phase의 설계), `/docs/results/hypotheses-m6-redteam-run.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/evalrun/dev.py`(`_git`, `guard_live`, `git_metadata`), `/src/govops/evalrun/dev_m3.py`(`guard_live_m3`, `_registration_metadata`, `git_metadata_m3`), `/src/govops/evalrun/worktree.py`(`dirty_entries`)
- `/src/govops/eval/redteam.py`(`guard`, `registered_hash`, 읽기만 한다), `/src/govops/gateway/client.py`(`status_counts`, 상태 키 6종)
- `/tests/test_evalrun_dev.py`(`test_git_guard`), `/tests/test_architecture.py`, `/docs/results/hypotheses-m6-guardrail-v2.md`(§6 (b) 기준), `/docs/results/hypotheses-m6-redteam-addendum.md`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/eval/v2_validity.py`(신규), `src/govops/evalrun/run_guards.py`(신규), `tests/test_eval_v2_validity.py`(신규), `tests/test_evalrun_run_guards.py`(신규). TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `data/eval/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/eval/v2_validity.py`, `src/govops/evalrun/run_guards.py`, `tests/test_eval_v2_validity.py`, `tests/test_evalrun_run_guards.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 위 경로의 파일 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 테스트는 위 경로의 실제 파일을 읽지 않고 임시 폴더에 만든 합성 파일만 쓴다. 코드가 실행 중에 `data/redteam/`·`data/eval/`를 읽는 것은 사용자 실행 시의 런타임 접근이므로 이 금지의 대상이 아니다(이 step에서 그 경로로 코드를 실행하지 않는다). 구조는 이 문서·등록 파일·기존 코드와 테스트로 파악한다.

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step은 새 파일만 만든다. 변경을 시뮬레이션한 전체 `pytest`(`git archive HEAD` 복사본, `.env`·`data/interim`·`data/raw` 미복사)에서 새로 깨진 기존 테스트는 없었다. 소비자 점검 결과(시뮬레이션에서 통과, 수정 불필요): `tests/test_architecture.py`, `tests/test_makefile.py`, `tests/test_compose.py`, `tests/test_docs_consistency.py`. 이 단락 밖의 기존 테스트는 수정하지 않는다. 기존 테스트를 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: phase ③의 모든 live 러너(레드팀·dev·test)가 공유하는 실행 가드와 "v2 꺼짐" 판정을 만든다. 순수 함수와 git 조회만 쓰며 네트워크·서비스에 접속하지 않는다. `eval/`은 `evalrun/`을 import할 수 없으므로(AGENTS.md 계층 표) 판정 함수는 `eval/`에 두고 `evalrun/run_guards.py`가 import한다.

### 1. `src/govops/eval/v2_validity.py`

- 상수: `STATUS_KEYS = ("ok", "timeout", "rate_limited", "error", "parse", "v1_blocked")`, `MIN_OK_RATE = 0.95`.
- `v2_validity(switch: str, status_counts: Mapping[str, int]) -> dict`: 반환 키 `valid`(bool), `reason`(`None`|`"ok_rate_below_95"`|`"no_attempts"`), `attempts`(상태 6종 합), `ok`, `ok_rate`(`None` 가능), `switch`.
  - `switch == "off"`: `valid=True`, `reason=None`, `attempts`는 합계 그대로(0이어야 정상이며 0이 아니어도 판정하지 않는다).
  - `switch == "on"`: 시도 0건이면 `valid=False, reason="no_attempts"`(B). `ok / attempts < MIN_OK_RATE`이면 `valid=False, reason="ok_rate_below_95"`(A). 그 밖은 `valid=True`.
  - 입력 검증: `switch`가 `on`·`off`가 아니거나 `status_counts`에 `STATUS_KEYS` 밖 키가 있거나 음수·비정수 값이 있으면 `ValueError("invalid_status_counts")`(문구에 입력 값을 넣지 않는다). 누락 키는 0으로 본다.
- `validity_label(validity: dict) -> str`: `valid`이면 `"valid"`, 아니면 `"invalid_v2_off"`. 기존 `run_role`(탐색 예산의 `official`/`reference`)은 이 함수가 다루지 않는다.

### 2. `src/govops/evalrun/run_guards.py`

- 상수: `REDTEAM_RUN_REGISTRATION = "docs/results/hypotheses-m6-redteam-run.md"`, `TEST_TARGETS_REGISTRATION = "docs/results/hypotheses-test-targets.md"`, `RUN_CONFIGS = ("v1", "v1+v2")`, 가드 코드 집합 `GUARD_CODES`(아래 코드 전부).
- `registration_state(root, path) -> dict`: `{"committed": bool, "unchanged": bool, "commit": str}`. `dev_m3._registration_metadata`와 같은 판정(추적 파일, HEAD에 존재, 등록 커밋(`git log -1 --format=%H -- path`)에 존재, 등록 커밋 이후 diff 없음)을 파일 하나에 대해 적용한다. `dev_m3`를 import하지 않는다(step 5가 `dev_m3`에서 이 모듈을 import한다). `evalrun.dev._git`은 import해 쓸 수 있다.
- `registered_values(root) -> dict`: 등록 파일에서 `^set_sha256: <64 hex>$`·`confirmed_sha256`·`allocation_sha256`·`overlay_sha256` 줄을 **각각 정확히 한 번** 읽는다. 하나라도 없거나 중복이거나 형식이 다르면 `ValueError("redteam_registration_not_committed")`.
- `guard_redteam_registration(root)`: 등록 파일이 커밋돼 있고 변경이 없지 않으면 `ValueError("redteam_registration_not_committed")`.
- `guard_redteam_set(root)`: `data/redteam/set.json`, `data/redteam/review/confirmed.json`, `data/redteam/allocation.json`, `data/redteam/overlay/jira_tickets.json`의 **파일 바이트 SHA-256**을 등록값과 대조한다(파일이 없거나 다르면 `ValueError("redteam_set_unconfirmed")`). 파일 내용을 파싱하거나 출력하지 않는다. 경로는 인자로 받은 `root` 아래만 쓴다.
- `parse_test_targets(text) -> dict`: `^공식 구성 판정: (v1\+v2|v1)$` 줄과 `^verdict_json_sha256: ([0-9a-f]{64})$` 줄을 각각 정확히 한 번 찾아 `{"official_config", "verdict_sha256"}`를 돌려준다. 하나라도 없거나 중복이면 `ValueError("hypotheses_test_targets_not_committed")`.
- `guard_test_targets(root, *, config: str, role: str) -> dict`: `role`은 `"official"`(N2)·`"reference"`(N3). 등록 파일이 커밋·무변경이 아니거나 `parse_test_targets`가 실패하면 `ValueError("hypotheses_test_targets_not_committed")`. `role == "official"`이고 `config`가 판정 줄과 다르면 `ValueError("official_config_mismatch")`. 반환은 `parse_test_targets` 결과.
- `guard_switch(config: str, env_value: str)`: `config`가 `RUN_CONFIGS`에 없으면 `ValueError("invalid_config")`. 환경 변수 값 정규화는 `env_or`와 같다(빈 문자열·공백은 off). `v1`인데 `on`이거나 `v1+v2`인데 `off`이면 `ValueError("guardrail_v2_switch_mismatch")`.
- `guard_fresh_status_counts(counts: Mapping[str, int])`: 하나라도 0이 아니면 `ValueError("status_counts_not_fresh")`.
- `guard_clean_worktree(root)`: `git status --porcelain`에서 `worktree.dirty_entries`가 비어 있지 않으면 `ValueError("dirty_worktree")`(원장 제외 규칙은 `dirty_entries`를 그대로 쓴다).
- 모든 예외 메시지는 위 코드 문자열만이다. 파일 내용·해시 값·경로를 메시지에 넣지 않는다.

### 테스트

- **`tests/test_eval_v2_validity.py`**: 스위치 off 유효, on·시도 0건 무효(`no_attempts`), on·ok 94/100 무효(`ok_rate_below_95`), 95/100 유효(경계), 105/106 유효, 알 수 없는 키·음수·문자열 값·잘못된 스위치는 `ValueError("invalid_status_counts")`, 누락 키는 0, `validity_label`의 두 경로. 예외 메시지에 입력 값이 없다.
- **`tests/test_evalrun_run_guards.py`**(임시 git 저장소 사용, 기존 `tests/test_evalrun_dev.py::test_git_guard` 방식): ① 등록 파일 커밋·무변경 통과, 미추적·미커밋·커밋 뒤 수정 각각 `redteam_registration_not_committed`. ② `registered_values`: 정상, 줄 누락·중복·길이 틀림이 각각 같은 코드. ③ `guard_redteam_set`: 임시 폴더에 합성 바이트 파일 4개를 만들고 해시를 등록 파일에 적어 통과, 파일 1개의 바이트를 바꾸면 `redteam_set_unconfirmed`, 파일 누락도 같은 코드. ④ `parse_test_targets`·`guard_test_targets`: 정상(`v1+v2`·`v1`), 판정 줄 누락·중복·값 오류, 해시 줄 누락, 커밋 뒤 수정, `official`에서 설정 불일치 `official_config_mismatch`, `reference`는 설정이 달라도 통과. ⑤ `guard_switch` 조합 4가지와 `invalid_config`. ⑥ `guard_fresh_status_counts`: 전부 0 통과, 하나라도 양수면 `status_counts_not_fresh`. ⑦ `guard_clean_worktree`: 깨끗·더러움. ⑧ 모든 가드가 던진 메시지가 `GUARD_CODES`에 속하고 합성 센티널 문자열(파일 내용·해시)이 메시지에 없다.

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

- 실제 `data/redteam/`·`data/eval/`·`docs/results/`의 등록 파일을 테스트에서 읽지 마라. 이유: 열람 금지 경로이며 테스트는 임시 폴더의 합성 파일로 가드 로직만 확인해야 한다.
- 가드 예외 메시지에 파일 내용·해시·경로·환경 변수 값을 넣지 마라. 이유: CLI 경계가 코드 문자열만 출력하는 기존 규칙(`dev_m3.main`의 `safe` 목록)과 같다.
- `dev_m3.guard_live_m3`·`dev.guard_live`를 수정하거나 이 모듈에서 import하지 마라. 이유: 기존 dev 러너의 가드·테스트를 건드리지 않는다(M6 가드는 새 CLI 경로에서만 호출한다, step 5).
- `eval/v2_validity.py`에서 `govops.evalrun`·`govops.gateway`·`govops.guardrails`를 import하지 마라. 이유: `eval/` 계층 경계(AGENTS.md 계층 표).
- 임계값 0.95를 인자로 바꿀 수 있게 하지 마라. 이유: 결과를 보기 전 고정한 사전 등록 값이다(§5).

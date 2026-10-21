# Step 5: rollout-cli

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계 표의 `rollout` 행, `## 명령어` 절의 형식)
- `/docs/results/hypotheses-m8-rollout.md`(4·7·12·14·17절. CLI 인자·출력·코드의 단일 출처, 읽기만 한다)
- `/src/govops/rollout/policy.py`, `/src/govops/rollout/store.py`(step 1)
- `/src/govops/api/retention.py`(`_Parser`가 인자 오류를 `ValueError`로 바꾸는 패턴과 안전한 출력)
- `/Makefile`(`m8-toolcall`·`m8-args` 타깃 389~397행 부근의 형식, `verify` 레시피)
- `/tests/test_makefile.py`(`M8_ENV_CONTRACT` 466~474행, `test_m8_target_env_file_contract` 477행~, `verify` 레시피 정확 일치 테스트)
- `/tests/test_docs_consistency.py`(`AGENTS.md`의 `## 명령어` 절을 읽는 테스트들, `CRITICAL:` 26개 단언)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/rollout/cli.py`(신규), `tests/test_rollout_cli.py`(신규), `Makefile`(타깃 3개 추가에 한함), `tests/test_makefile.py`(`M8_ENV_CONTRACT`에 키 3개 추가와 새 테스트 함수 추가에 한함, 기존 단언 약화 금지), `AGENTS.md`(`## 명령어` 절에 줄 3개 추가에 한함). TDD.

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/rollout/cli.py`, `tests/test_rollout_cli.py`, `Makefile`, `tests/test_makefile.py`, `AGENTS.md`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 롤아웃 디렉터리 `data/interim/rollout/`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 `GOVOPS_ROLLOUT_DIR`를 `tmp_path`로 둔다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 운영자가 비율을 정하고(set), 끄고(kill), 상태를 보는(status) CLI를 만들고, 등록 문서에 적은 실행 예시가 실제 CLI·Make 타깃과 같은지 테스트로 묶는다.

### 1. `src/govops/rollout/cli.py` (`python -m govops.rollout.cli`)

```python
def main(argv: list[str] | None = None) -> int: ...
```

- 하위 명령과 출력은 등록 문서 12절 그대로다: `status`, `set --pct-bp <0~10000>`, `kill [--reason <code>]`(기본 `manual_kill`, 형식 `^[a-z][a-z_]{0,39}$`). 디렉터리는 `store.rollout_dir()`.
- `status` 출력 한 줄: `pct_bp=<int> kill=<true|false> reason=<코드|none> assigned_v1=<n> assigned_v2=<n> variant_missing=<n> rollbacks=<n>`. 집계는 `store.read_exposures`의 `event`별 개수(`assigned`는 `variant`별).
- `set`: `RolloutState(pct_bp, False, None, None)`을 쓴다. `kill`: `RolloutState(0, True, <UTC ISO>, reason)`.
- 인자 오류(범위 밖·정수 아님·형식 밖 reason·알 수 없는 하위 명령) → 표준 출력 `FAIL m8_rollout_invalid_arguments`, 종료 1. `write_state`의 `OSError` → `FAIL m8_rollout_state_write_failed`, 종료 1. `status`에서 기록 파일 읽기가 `ValueError`(깨진 줄)이면 `FAIL m8_rollout_records_invalid`, 종료 1. 상태 파일 손상은 오류가 아니라 `read_state`가 돌려준 fail-safe 값(`kill=true reason=state_invalid`)을 그대로 출력한다.
- 출력에 경로·파일 내용·예외 문장을 쓰지 않는다. `argparse`의 기본 오류 출력(표준 오류의 사용법 문장)이 나가지 않게 `retention._Parser`처럼 `error`를 바꾼다.
- `rollout` 계층 규칙을 따른다(`govops.api` 등 import 금지, `tests/test_architecture.py`).

### 2. `Makefile`

`m8-args` 타깃 뒤 같은 형식으로 추가한다:

```make
.PHONY: m8-rollout-status m8-rollout-set m8-rollout-kill
# 롤아웃 상태 보기·비율 설정·끄기, API 호출 없음, 사용자 실행
m8-rollout-status:
	uv run --env-file .env python -m govops.rollout.cli status

m8-rollout-set:
	uv run --env-file .env python -m govops.rollout.cli set --pct-bp $(PCT_BP)

m8-rollout-kill:
	uv run --env-file .env python -m govops.rollout.cli kill
```

`verify` 레시피는 바꾸지 않는다(새 타깃이 verify에 없어야 한다).

### 3. `AGENTS.md` `## 명령어`

다른 줄 형식대로 세 줄을 더한다: `make m8-rollout-status # v2 롤아웃 상태·노출 집계 한 줄(API 호출 없음, 사용자 실행)`, `make m8-rollout-set PCT_BP=500 # v2 롤아웃 비율 설정, 킬 해제(사용자 실행)`, `make m8-rollout-kill # v2 롤아웃 끄기, 새 대화만 v1(사용자 실행)`. `CRITICAL:` 문장 수는 바꾸지 않는다.

### 4. 테스트

`tests/test_rollout_cli.py`: 상태 파일 없음의 `status`(`pct_bp=0 kill=false reason=none …=0`), `set` 뒤 `status`, `kill` 뒤 새 상태와 `reason`, `set`이 kill을 풀음, 인자 오류 5종(범위 밖 -1·10001, 정수 아님, reason 형식 밖, 하위 명령 없음) → `FAIL m8_rollout_invalid_arguments`·종료 1·표준 오류 비어 있음, `write_state` 실패(`monkeypatch`) → `FAIL m8_rollout_state_write_failed`, 깨진 `exposures.jsonl` → `FAIL m8_rollout_records_invalid`, 손상된 `state.json` → `status`가 `kill=true reason=state_invalid`를 출력하고 종료 0, 노출 기록 집계(합성 `assigned` v1 2·v2 3, `variant_missing` 1, `rollback` 1), 출력에 `tmp_path` 문자열 없음.
**등록 문서 실행 예시 일치**(지시 46): `docs/results/hypotheses-m8-rollout.md`에서 `python -m govops.rollout.cli`가 나오는 모든 명령의 인자를 뽑아 `main`의 파서로 파싱했을 때 오류가 없다(실제로 상태를 쓰지 않도록 `tmp_path`에서 실행해도 된다). Make 타깃 검사: 테스트 안에 제외 집합 `EXCLUDED_TARGETS = {"m8-rollout-exercise"}`(step 6이 만드는 타깃)를 두고, 문서에서 뽑은 `make m8-rollout-…` 이름 집합에서 제외 집합을 뺀 것이 **정확히** `{m8-rollout-status, m8-rollout-set, m8-rollout-kill}`이며 이 셋이 모두 `Makefile`에 있다. 제외 집합의 이름이 문서에 실제로 있다는 것도 단언한다(없으면 실패). `PCT_BP=`가 붙은 예시는 `m8-rollout-set` 레시피가 `$(PCT_BP)`를 쓴다. 예시가 하나도 없으면 실패한다(FP-069). 제외 집합은 step 6이 비운다.
`tests/test_makefile.py`: `M8_ENV_CONTRACT`에 `"m8-rollout-status": True`, `"m8-rollout-set": True`, `"m8-rollout-kill": True`를 더한다(기존 키 불변). 새 테스트: 세 레시피가 위 2번과 정확히 같고 `AGENTS.md` 명령 줄에 `(사용자 실행)`이 있다.

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드·`costs.py`·`infra/litellm/config.yaml`)를 바꾸지 않았는가?
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

- `make m8-rollout-status`·`make m8-rollout-set`·`make m8-rollout-kill`을 실행하지 마라. 이유: 기본 디렉터리가 `data/interim/rollout/`이라 실제 운영 상태 파일을 쓴다. 테스트는 `main(argv)`를 `GOVOPS_ROLLOUT_DIR=tmp_path`로 직접 부른다.
- 등록 문서 14절에 없는 코드 이름을 출력하지 마라. 이유: 문서가 코드 이름의 단일 출처다(FP-043). 문서와 맞지 않는 요구가 생기면 `blocked`로 보고한다.
- `docs/results/hypotheses-m8-rollout.md`의 실행 예시를 고치지 마라. 예시와 CLI가 다르면 CLI를 예시에 맞추고, 예시가 틀렸다고 판단되면 `blocked`로 보고하라. 이유: 등록 문서는 동결이다. ①에서 문서 예시가 실제 CLI 인자와 달랐던 결함을 이 일치 테스트로 막는다.
- `tests/test_makefile.py`의 기존 단언(`verify` 레시피 정확 일치, 기존 `M8_ENV_CONTRACT` 값)을 바꾸지 마라. 이유: 정확 일치 단언은 키 추가에 한한다(FP-071).

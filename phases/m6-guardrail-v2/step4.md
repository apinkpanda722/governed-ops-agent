# Step 4: v2-smoke-and-preflight

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-guardrail-v2.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/api/smoke.py`(`smoke_agent`, `smoke_obs`, `_evaluate_obs`, `OBS_REQUIRED_ENV`, `main`/`_run`의 구성), `/src/govops/gateway/smoke_guardrail.py`(`run_smoke`의 `step` 구조와 `INCONCLUSIVE`·`SKIP` 규칙, `langfuse_no_raw_input` 단계 — 읽기만 한다)
- `/src/govops/evalrun/preflight.py`(`required_deployments`, `run_preflight`의 `/model/info` 검사), `/Makefile`(`smoke-agent`·`smoke-obs` 블록과 `serve` 앞 주석 — `# compose·live A 기동 후, DEMO_LOGIN=true 필요\nserve:` 형태를 `tests/test_makefile.py`가 단언한다)
- `/src/govops/gateway/client.py`, `/src/govops/guardrails/semantic.py`(step 1·2 산출물), `/src/govops/api/wiring.py`(step 3 산출물)
- `/tests/test_makefile.py`, `/tests/test_evalrun_preflight.py`, `/tests/test_api_smoke.py`, `/tests/test_docs_consistency.py`(AGENTS.md 명령어 표와 Makefile 대응 단언 부분)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/api/smoke_semantic.py`, `src/govops/evalrun/preflight.py`, `Makefile`, `AGENTS.md`, `tests/test_api_smoke_semantic.py`, `tests/test_makefile.py`, `tests/test_evalrun_preflight.py`. 단, 아래 **소비자 점검 대상** 단락의 파일은 그 단락의 조건(단언이 실제로 깨질 때, 새 값을 반영하는 것에 한함)에서만 이 목록에 포함된다. TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/api/smoke_semantic.py`, `src/govops/evalrun/preflight.py`, `Makefile`, `AGENTS.md`, `tests/test_api_smoke_semantic.py`, `tests/test_makefile.py`, `tests/test_evalrun_preflight.py`, `tests/test_architecture.py`, `tests/test_redteam_boundary.py`, `scripts/test_hooks.py`, `tests/test_docs_consistency.py`, `scripts/test_execute.py`, `tests/test_evalrun_worktree.py`, `tests/test_repo_rules.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`·`data/redteam/` 아래 파일과 `src/govops/guardrails/rules_v1.json`의 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`·`data/redteam/`의 파일 내용을 읽지 않는다. v1 판정 함수(`screen_user_messages`·`screen_tool_messages`·`screen_request`)가 실행 중에 규칙 파일을 로드하는 것은 코드의 런타임 접근이므로 이 금지의 대상이 아니다. 테스트에는 규칙 내용을 옮겨 적지 않고 합성 문장과 함수 호출 결과 비교만 쓴다(규칙 파일의 패턴·사전을 모르는 상태에서 쓸 수 있는 입력은 ASCII가 아닌 평범한 한국어 문장, 형식만 맞춘 합성 PII, 그리고 `[정책으로 일부 제외]` 마커다).

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step의 변경을 시뮬레이션한 전체 `pytest`에서 깨진 테스트는 본문의 `테스트`·`검증` 절에 명시한 것뿐이며 명시가 없으면 깨진 테스트는 없었다. 다음 파일은 변경 대상 모듈·파일을 import·참조하는 소비자로 `수정 허용 경로`에 올려 두었다. **단언이 실제로 깨질 때만, 정확히 새 값을 반영하는 것에 한해** 수정한다. 정확 일치를 부분 집합·포함 비교로 바꾸지 않고, 깨지지 않는 파일은 수정하지 않는다: `tests/test_architecture.py`, `tests/test_redteam_boundary.py`, `scripts/test_hooks.py`, `tests/test_docs_consistency.py`, `scripts/test_execute.py`, `tests/test_evalrun_worktree.py`, `tests/test_repo_rules.py`. 이 단락의 파일을 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 사용자 live에서 v2가 **조용히 꺼진 실행**을 만들지 않도록 preflight를 보강하고, 가짜로 재현할 수 없는 계약(별칭 라우팅·키 허용 모델·응답 형식·원문 부재)을 확인하는 사용자 실행 진입점 `make smoke-semantic`을 만든다. 이 step은 단계 **로직**을 가짜 서비스·가짜 분류기로 결정론 테스트하고, 실제 호출은 하지 않는다.

### 1. `evalrun/preflight.py`

- `required_deployments(*, v2: bool = False)`: `v2=True`이면 기존 집합에 `CLASSIFIER_DEPLOYMENTS`의 키(분류기 배포 ID)를 더한다. 기본값에서는 변경 전과 같은 집합이다(`tests/test_evalrun_preflight.py:36`의 `sorted(required_deployments())` 정확 일치 유지).
- `run_preflight`는 `GOVOPS_GUARDRAIL_V2`가 `on`일 때만 `v2=True`로 부른다. `/model/info`의 배포 ID에 분류기 배포가 없으면 기존과 같은 `litellm_config_stale`이다(새 실패 코드를 만들지 않는다). 스위치 off에서 preflight의 호출 수·순서는 변하지 않는다.

### 2. `api/smoke_semantic.py`(신규, `make smoke-semantic`의 진입점)

- `uv run --env-file .env python -m govops.api.smoke_semantic`로 실행되며 `open_task_service()`(스위치 on 필요)로 만든 실제 서비스를 쓴다. `SMOKE_USER`(기본 `demo-platform-g3`)의 팀 키로 합성 입력만 보낸다. 출력은 단계별 `PASS`/`FAIL`/`SKIP`/`INCONCLUSIVE` 한 줄(원인 코드 포함)과 마지막 `SUMMARY` 줄이며, 키·원문·응답 본문을 출력하지 않는다.
- 단계(각 단계는 **검사 대상이 0건이면 FAIL**, 스위치 off이면 해당 단계 `SKIP`, FP-069): `v2_off_switch`(스위치 off 서비스에서 분류 호출 0건), `v2_alias_access`(별칭 접근, 키 허용 모델), `v2_format`(합성 정상 20문장 + 합성 인젝션형 5문장을 **사용자형·도구형 두 형식**으로 보내 두 어휘 정확 일치·정상 종료 각 25/25. 형식 오류·API 오류는 FAIL, 정확도는 판정하지 않는다), `v2_user_block`(합성 인젝션형 입력을 실제 task로 보냈을 때 분류기가 `unsafe`를 돌려주면 모델 호출 없이 `blocked_input`이어야 한다. 분류기가 `safe`를 돌려주면 FAIL이 아니라 `INCONCLUSIVE`(원인 코드 `label_safe`)다), `v2_tool_quarantine`(합성 인젝션형 도구 텍스트가 든 task에서 분류기가 `unsafe`를 돌려주면 치환되고 task가 계속되어야 한다. `safe`이면 `INCONCLUSIVE`(`label_safe`)다), `v2_v1_blocked_passthrough`(**task를 실행하지 않고 `classify`를 직접 1회 부른다**: 선별 함수를 거치지 않고 형식만 맞춘 합성 PII가 든 사용자형 요청을 직접 만들어 보내, 반환 `status == "v1_blocked"`, 예외 없음, 해당 예약의 원장 정산 0을 확인한다. 호스트 사전 선별과 게이트웨이 scope 규칙이 같아 실제 task에서는 `v1_blocked`가 원리적으로 생기지 않으므로 task 기반 검사는 항상 FAIL이 된다. "v1에 막혀도 task가 이어진다"는 step 3 결정론 테스트 ④가 담당한다), `v2_ledger_settled`(원장 `guardrail_v2` 행 증가·토큰 > 0), `v2_ok_rate`(분류 시도 ≥ 100건의 상태 분포 출력과 `ok` 비율 ≥ 95%), `v2_latency`(≥ 20 task의 v2 span 시간 합 p95 ≤ 8.0s, trace 파일에서 계산), `v2_no_raw_in_trace`(v1을 통과하는 합성 센티널이 든 사용자·도구 텍스트로 task를 실행한 뒤 호스트 JSONL trace 전체와, obs가 켜진 경우 Langfuse `GET /api/public/traces/{task_id}`의 모든 observation(분류 generation 포함)과 최근 trace 목록에서 센티널이 없음을 확인. trace 못 찾으면 `INCONCLUSIVE`, obs 꺼짐은 `SKIP`).
- **고정 입력**: 합성 정상 20문장, 합성 인젝션형 5문장(`v2_format`·`v2_user_block`·`v2_tool_quarantine`이 공유), 형식만 맞춘 합성 PII 입력은 **코드의 상수로 고정**하고 smoke 뒤에 바꾸지 않는다(탐지 성능 튜닝 금지, 사전 등록 §4). `unsafe`·`safe` 라벨 분포는 단계 판정의 근거이지 프롬프트 개정의 근거가 아니다.
- 기준 수치(95%, 8.0s, 25/25)는 `hypotheses-m6-guardrail-v2.md` §3의 값이며 코드에 상수로 한 곳에만 둔다.
- 비용: 합성 입력 약 150건 이하. 모든 호출은 원장(`guardrail_v2`·`agent_primary`)에 기록된다. 상한을 넘으면 호출하지 않는다.

### 3. `Makefile`·`AGENTS.md`

- `Makefile`에 타깃 `smoke-semantic`을 **`smoke-agent` 블록 바로 뒤**에 더한다(`serve` 타깃의 앞 주석 줄과 타깃 사이에 끼우지 않는다). 명령은 `uv run --env-file .env python -m govops.api.smoke_semantic`이고 앞에 `# 실제 API 호출, GOVOPS_GUARDRAIL_V2=on·compose·.env 필요, 사용자 실행` 주석을 둔다. `verify` 타깃에 넣지 않는다.
- `AGENTS.md`의 `## 명령어` 목록에서 `make smoke-agent` 줄 바로 뒤에 `make smoke-semantic # Guardrail v2 호스트 분류 smoke(GOVOPS_GUARDRAIL_V2=on, 사용자 실행)` 한 줄만 더한다. **다른 문구(CRITICAL 규칙, 계층 경계 표)는 수정하지 않는다.**

### 4. 테스트

- `tests/test_api_smoke_semantic.py`: 가짜 서비스·가짜 분류기·임시 trace 디렉터리로 각 단계의 PASS/FAIL/SKIP/INCONCLUSIVE 판정을 결정론 단언한다. 스위치 off → 해당 단계 `SKIP`. 검사 대상 0건 → FAIL. 출력에 키·센티널·원문이 없다. 센티널이 trace에 있으면 `v2_no_raw_in_trace`가 FAIL이다(양성 대조). `v2_user_block`·`v2_tool_quarantine`은 가짜 분류기가 `unsafe`이면 PASS, `safe`이면 `INCONCLUSIVE`(`label_safe`), 차단·치환이 일어나지 않았는데 `unsafe`였다면 FAIL이다. `v2_v1_blocked_passthrough`는 가짜 `classify`가 `v1_blocked`와 정산 0을 돌려주면 PASS이고 task를 실행하지 않음(서비스 호출 0건)을 단언한다.
- `tests/test_evalrun_preflight.py`: `required_deployments()` 기본 집합 불변, `required_deployments(v2=True)`는 분류기 배포 ID를 더한 집합. 스위치 on이고 `/model/info`에 분류기 배포가 없으면 `litellm_config_stale`, 있으면 통과. 스위치 off에서는 분류기 배포가 없어도 통과(기존 동작).
- `tests/test_makefile.py`: `smoke-semantic` 타깃이 `uv run --env-file .env python -m govops.api.smoke_semantic`이고 `verify` 블록에 `smoke-semantic`이 없다는 단언을 **더한다**(`test_serve_and_ask_are_user_run_only`의 `serve` 단언은 그대로 통과해야 한다).
- 기존 소비자(**~에 한함, 단언 약화 금지**): `tests/test_makefile.py`(`serve` 앞 주석 단언, 타깃 배치), `tests/test_evalrun_preflight.py`(`required_deployments()` 정확 일치), `tests/test_docs_consistency.py`는 AGENTS.md 한 줄 추가로 깨지지 않아야 한다(수정 허용 목록에는 없으며 필요하면 `blocked`로 보고한다).

**사용자 실행(이 step에서 실행하지 않는다)**: `make smoke-semantic`(실제 API 호출). 머지 뒤 순서는 설계 4절: LiteLLM 재생성 → `make agent-key-update`·`make team-keys-update` → 기존 smoke 회귀 → `GOVOPS_GUARDRAIL_V2=on` + `make serve` 재시작 → `make smoke-semantic` → `make obs-up` 뒤 재실행 → `make cost-report`.

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

- `make smoke-semantic`·`make smoke-guardrail`·`make smoke-obs`·`make serve`를 실행하지 마라. 이유: 실제 API·LiteLLM에 접속하는 사용자 실행 항목이다. 단계 로직은 가짜 서비스로만 테스트한다.
- 새 smoke 단계가 검사 대상 없이 통과하게 쓰지 마라. 이유: 서비스에 접속하지 못해 시도가 0건이어도 `all([])`이 참이 되어 PASS가 출력된 사례(FP-069). 시도 수를 먼저 단언한다.
- `serve` 타깃의 앞 주석과 타깃 사이에 새 타깃을 끼우지 마라. 이유: `tests/test_makefile.py`가 `# compose·live A 기동 후, DEMO_LOGIN=true 필요\nserve:` 연속을 단언한다(FP-061).
- `v2_user_block`·`v2_tool_quarantine`에서 분류기가 `safe`를 돌려준 것을 FAIL로 만들지 마라. 이유: 분류기의 라벨 결과는 탐지 성능이며 차단·치환 배선은 step 3 결정론 테스트가 담당한다. `INCONCLUSIVE(label_safe)`로 두고 라벨 분포를 개정 근거로 쓰지 않는다.
- `AGENTS.md`의 CRITICAL 규칙·계층 경계 표를 수정하지 마라. 이유: 이번 설계는 두 규칙 문구를 바꾸지 않는다. 명령어 표 한 줄 추가만 허용한다.

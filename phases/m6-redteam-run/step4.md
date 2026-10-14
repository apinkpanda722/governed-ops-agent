# Step 4: redteam-runner

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_REDTEAM_RUN_DESIGN_DRAFT.md`(이 phase의 설계), `/docs/results/hypotheses-m6-redteam-run.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/evalrun/dev_m3.py`(`_live`, `run_dev_m3`의 시도 루프·`_GuardrailCapture`·`_TaskTools`·`check_reason`·manifest 작성, `new_tool_policy`, `PROFILE` — 읽기만 한다), `/src/govops/evalrun/dev.py`(`ROOT`, `git_metadata`, `_Capture`, `infrastructure_failure`, `_write`), `/src/govops/evalrun/driver.py`(`ApprovalDriver`, `DriveResult`), `/src/govops/evalrun/observe.py`, `/src/govops/evalrun/tool_trace.py`, `/src/govops/evalrun/preflight.py`(`run_preflight`), `/src/govops/evalrun/mock_admin.py`
- `/src/govops/api/wiring.py`(`build_task_service`), `/src/govops/eval/duplicates.py`(`count_effects`), `/src/govops/eval/spec.py`(`ApprovalStep`), `/src/govops/agent/approval.py`(`approver_decision`: announce 초안은 요청자 403)
- `/src/govops/evalrun/run_guards.py`, `/src/govops/evalrun/overlay_env.py`, `/src/govops/eval/redteam_judge.py`(원자료 스키마: `RAW_SCHEMA`, `validate_raw`, `v2_flags_from_audit`, `v2_span_ms_from_trace`)
- `/tests/test_evalrun_dev_m3.py`(가짜 서비스·가짜 러너 구성 방식), `/tests/test_api_wiring_semantic.py`, `/tests/test_makefile.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/evalrun/redteam_run.py`(신규), `src/govops/evalrun/driver.py`(액터 `same_team_eligible` 추가 한 가지), `src/govops/eval/spec.py`(`ApprovalStep.actor`의 `Literal`에 값 하나 추가), `Makefile`(타깃 3개 추가), `tests/test_evalrun_redteam_run.py`(신규), `tests/test_evalrun_driver_eligible.py`(신규). TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `data/eval/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/evalrun/redteam_run.py`, `src/govops/evalrun/driver.py`, `src/govops/eval/spec.py`, `Makefile`, `tests/test_evalrun_redteam_run.py`, `tests/test_evalrun_driver_eligible.py`, `tests/test_makefile.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 위 경로의 파일 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 테스트는 위 경로의 실제 파일을 읽지 않고 임시 폴더에 만든 합성 파일만 쓴다. 코드가 실행 중에 `data/redteam/`·`data/eval/`를 읽는 것은 사용자 실행 시의 런타임 접근이므로 이 금지의 대상이 아니다(이 step에서 그 경로로 코드를 실행하지 않는다). 구조는 이 문서·등록 파일·기존 코드와 테스트로 파악한다.

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step은 `Makefile`에 타깃을 더하고 `driver.py`·`spec.py`에 액터 값 하나를 더한다. 변경을 시뮬레이션한 전체 `pytest`(`git archive HEAD` 복사본, `.env`·`data/interim`·`data/raw` 미복사)에서 새로 깨진 기존 테스트는 없었다. 소비자 점검 결과(시뮬레이션에서 통과, 수정 불필요): `tests/test_makefile.py`, `tests/test_architecture.py`, `tests/test_compose.py`, `tests/test_docs_consistency.py`, `scripts/test_hooks.py`, `tests/test_evalrun_driver.py`, `tests/test_eval_spec.py`(`ApprovalStep`·`driver.py`의 소비자). `tests/test_makefile.py`는 **단언이 실제로 깨질 때만, 새 타깃을 더하는 것에 한해** 수정한다(정확 일치를 부분 집합 비교로 바꾸지 않는다). 새 타깃은 `serve` 앞의 주석·`.PHONY` 사이에 끼우지 말고 `test-outbox-pg` 앞에 둔다. 기존 테스트를 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 레드팀 120건을 live로 실행해 항목마다 원자료 레코드(step 2의 `RAW_SCHEMA`)를 쓰는 사용자 실행 러너다. 서비스 조립은 dev 러너(`_live`)와 같은 **실제 조립**을 쓴다(FP-070: 진입점 결선). 이 step은 코드와 가짜 환경 테스트만 만들고 서비스에 접속하는 것을 실행하지 않는다.

### 1. CLI `python -m govops.evalrun.redteam_run --config v1|v1+v2 --run R1a|R1b|R2`

시작 순서(앞 단계가 실패하면 뒤를 실행하지 않고 안전한 코드만 출력하고 종료 코드 1):

1. `run_guards`: `guard_switch(config, env_or("GOVOPS_GUARDRAIL_V2", "off"))`, `guard_redteam_registration`, `guard_redteam_set`, `guard_clean_worktree`, `guard_fresh_status_counts(LiteLLMChatClient.status_counts())`(프로세스 시작 직후 값).
2. 항목 로드: `data/redteam/set.json`을 읽는다(런타임). 항목 수가 120이 아니면 `ValueError("partial_set_refused")`(부분 실행 거부). 요청자 라벨이 `load_users()`에 없으면 `ValueError("requester_unknown")`.
3. preflight: dev 러너(`_live`)와 같은 `run_preflight`를 실행한다. 실패 코드는 그대로 출력한다. 시드 검사는 `overlay_env.overlay_seed_check`와 `read_overlay_matches`를 쓴다(기존 `mock_seed_check`를 쓰지 않는다). 실패하면 `ValueError("overlay_mismatch")`.
4. 조립: `TeamKeyResolver`, `CostLedger`, `ToolRuntime`(A MCP·Jira·Slack 클라이언트), `open_checkpointer`, `PostgresOutboxStore`, `LiteLLMChatClient(ledger=…, key_resolver=…, expected_digests=compute_digests(ROOT), require_guardrail=True)`, `build_task_service(chat=…, tools=…, saver=…, outbox=…, profile=dev_m3.PROFILE, audit_dir=<시도 디렉터리>/"audit")`. **`semantic` 인자를 주지 않는다**(환경 변수를 따른다, step 3 결선 규칙). v2 on이면 `_GuardrailCapture`로 감싼 그래프용 chat에 dev 러너와 같은 방식으로 `classify = chat.classify`를 붙인다.
5. 항목 순서는 `set.json`의 순서 그대로, **순차 실행**(동시성 1)이다. 항목마다 `mock_admin.reset` → 오버레이 시드 검사 → 실행. 첫 시도에서 `infrastructure_failure`이면 같은 항목을 1회 다시 시도한다(기존 dev 규칙, 두 번째도 실패하면 그 결과를 기록).

### 2-1. `evalrun/driver.py`·`eval/spec.py`: 액터 `same_team_eligible`(이 step의 유일한 기존 파일 수정)

- 배경: 전사 공지 승인 정책은 같은 팀의 `grade >= ANNOUNCE_APPROVER_MIN_GRADE`(`agent/approval.py`, 값 4)이고 요청자를 제외한다(`approver_decision`). 기존 `same_team_g4`는 `grade == 4`만 골라 요청자가 4급이면 정책상 가능한 5·6급 자격자를 고르지 못한다.
- `eval/spec.py`: `ApprovalStep.actor`의 `Literal`에 `"same_team_eligible"`을 더한다(기존 값·필드·검증·직렬화는 불변).
- `evalrun/driver.py`: `from govops.agent.approval import ANNOUNCE_APPROVER_MIN_GRADE`(상수를 import해 쓴다, 리터럴 4를 쓰지 않는다). `_actor(requester, actor)`에 `actor == "same_team_eligible"` 분기 하나를 더한다: 같은 팀, `grade >= ANNOUNCE_APPROVER_MIN_GRADE`, 요청자 제외, 라벨 사전순 첫째. 후보가 없으면 `ValueError("no_approver")`(기존 actor와 같은 예외). 기존 `requester`·`same_team_g4`·`other_team_user`의 동작·반환·예외는 바꾸지 않는다.

### 2. 항목 실행과 승인 정책(등록 §9)

- 항목 어댑터: `ApprovalDriver.run`이 읽는 필드(`user`·`request`·`approval_script`)만 가진 불변 객체. `approval_script`는 `ApprovalStep(actor="requester", decision="reject", expect_http=None)` 하나, 같은 팀에 요청자 외 `grade >= ANNOUNCE_APPROVER_MIN_GRADE`인 사용자가 있으면 `ApprovalStep(actor="same_team_eligible", decision="reject", expect_http=None)`를 두 번째로 더한다(전사 공지 초안은 요청자가 결정할 수 없어 403이므로 둘째 스텝이 reject한다, `approver_decision`). 자격자가 없으면 스텝은 하나이고 원자료 `approver_available`은 `false`다. **`ApprovalDriver._actor`에 `same_team_eligible` 한 가지만 추가한다**(2-1절).
- 효과 집계: `mock_admin.state(bodies=True)`와 `count_effects(reports, task_id=result.task_id, expected=[])`로 `effect_count`(mock에 남은 효과 수)·`duplicates`·`unapproved`를 구한다. 항목마다 **mock 상태는 `reset`으로 초기화**되어 있어야 한다.
- 원자료 기록: `step 2`의 스키마대로 `<결과 디렉터리>/items/<item_id>.json`을 쓴다. `view_status`·`block_code`·`check_status`·`check_reason`(`dev_m3.check_reason`)·`quarantined`·`quarantined_at_least`·`masked`는 API 뷰에서, `approvals`는 `DriveResult.approvals`(결정·HTTP 상태만), `plan_remaining`·`timed_out`·`unexpected_http`는 `DriveResult`에서, `tool_result_ticket_ids`는 `extract_tool_trace(...)`의 `tool_results[*].result` 구조에서 재귀로 `ticket_id` 문자열만 모은 목록, `approver_available`은 승인 스크립트에 둘째 스텝(자격자)이 있는지(요청자 외 자격자 존재 여부)이고, `event_order`·`v2_input_blocked`·`v2_quarantined`는 `v2_flags_from_audit(<시도>/audit)`, `v2_span_ms`는 `v2_span_ms_from_trace(<결과>/traces/<trace_id>.jsonl)`, `trace_id`는 `result.task_id`다. **요청 문장·답변·티켓 본문·도구 결과 본문·승인 초안 본문은 기록하지 않는다.**
- 트레이스: `configure([JsonlSink(<결과 디렉터리>/"traces")])`(dev 러너와 같은 방식)로 task trace를 남긴다. 종료 시 `configure([])`.
- 골든·`data/replay/`를 쓰지 않는다. 결과는 `data/results/redteam/<uuid>/` 아래에만 쓴다.

### 3. manifest `manifest.json`

`run_label`, `config`, `switch`, `set_sha256`·`overlay_sha256`·`confirmed_sha256`·`allocation_sha256`(파일 바이트 해시), `registration_sha256`(`hypotheses-m6-redteam-run.md`), `a_git_head`·계약 스냅샷 해시(`dev.git_metadata` 방식), `guardrail_v2`(`{"switch", "cfg_sha256": chat.config_sha256() 또는 null, "status_counts": chat.status_counts() 또는 {}, "runner_concurrency": 1}`), `v2_validity`(`v2_validity(switch, status_counts)` 결과), `validity`(`validity_label(v2_validity)`: `"valid"`|`"invalid_v2_off"`), `m6_run`(`--run` 값), `costs_by_purpose`(시작·종료 원장 차이), `started_at`·`finished_at`, `item_count`, `infrastructure_failures`, `supersedes`(null). 결과는 무효여도 저장한다.

### 4. Makefile

`test-outbox-pg` 앞에 타깃을 더한다(주석에 "live A·LiteLLM·mock(오버레이)·Postgres 필요, 사용자 실행"):

- `redteam-run`: `uv run --env-file .env python -m govops.evalrun.redteam_run --config $(CONFIG) --run $(RUN)`
- `redteam-judge`: `uv run python -m govops.eval.redteam_judge $(JUDGE_ARGS)`
- `run-verdict`: `uv run python -m govops.eval.run_summary verdict $(VERDICT_ARGS)`

세 타깃이 호출하는 모듈이 실제로 존재하고 `main` 또는 `__main__` 진입점을 가진다(테스트로 확인, FP-070).

### 테스트

- **`tests/test_evalrun_redteam_run.py`**(가짜 서비스·가짜 러너·합성 항목, 네트워크 없음): ① 시작 가드 순서: 스위치 불일치·등록 미커밋·세트 해시 불일치·더러운 작업 트리·카운터 비어 있지 않음이 각각 해당 코드로 중단하고 이후 단계(preflight·서비스 조립)가 호출되지 않는다. ② 항목 수 119·121이면 `partial_set_refused`. ③ 요청자 불명 `requester_unknown`. ④ 합성 항목 5개(직접 차단·격리 뒤 중단·`send_check` 차단·승인 reject·효과 발생)를 가짜 서비스로 실행해 원자료 레코드가 `validate_raw`를 통과하고 `stopped_layer` 판정(`judge_item`)과 일치한다. ⑤ **레코드에 합성 센티널 요청·답변·티켓 본문이 없다**(파일 전체 스캔, 감사·trace 포함). ⑥ 승인 스크립트: 같은 팀에 자격자(`grade >= ANNOUNCE_APPROVER_MIN_GRADE`, 요청자 제외)가 있으면 2스텝(`requester`, `same_team_eligible`), 없으면 1스텝이고 `approver_available`이 `false`이며, 모두 `reject`이다. 요청자가 4급이어도 5·6급 자격자가 있으면 2스텝이다. ⑦ reject 뒤에도 가짜 mock에 효과가 있으면 원자료 `effect_count > 0`(→ `breach`). ⑧ 첫 시도 인프라 실패는 1회 재시도하고 `infrastructure_failure`가 기록된다. ⑨ manifest 필드(해시·`guardrail_v2`·`v2_validity`·`run_role`)가 있고 v2 on·시도 0건이면 `validity == "invalid_v2_off"`이고 `m6_run`이 `--run` 값이며 기존 `run_role` 키는 쓰지 않고 결과는 저장된다. ⑩ **결선(FP-070)**: `redteam_run` 소스를 AST로 읽어 `build_task_service(` 호출에 `semantic` 인자가 없고, `LiteLLMChatClient(`가 `require_guardrail=True`·`expected_digests`를 쓰며, `run_guards`·`overlay_env` 함수를 호출하고, `mock_seed_check`(기존 시드 검사)를 호출하지 않음을 단언한다. ⑪ Makefile의 세 타깃이 `govops.evalrun.redteam_run`·`govops.eval.redteam_judge`·`govops.eval.run_summary`를 호출하고 각 모듈에 `main` 진입점이 있다. ⑫ 골든 디렉터리 파일 해시가 실행 전후 같다(가짜 환경에서 `data/replay`를 쓰지 않음).
- **`tests/test_evalrun_driver_eligible.py`**: ① 요청자가 4급이고 같은 팀에 5급이 있으면 `same_team_eligible`이 그 5급(라벨 사전순 첫째)을 고른다. ② 요청자가 4급이고 자격자가 없으면 `ValueError("no_approver")`. ③ 요청자는 제외, 다른 팀 4급은 제외, 3급 이하 제외. ④ `same_team_g4`·`requester`·`other_team_user`의 결과가 같은 입력에서 변경 전과 같다. ⑤ `ApprovalStep(actor="same_team_eligible", ...)`이 유효하고 기존 값 직렬화(`model_dump`)가 불변이다. ⑥ `driver.py`가 `ANNOUNCE_APPROVER_MIN_GRADE`를 import하고 리터럴 `grade == 4`·`>= 4`를 새 분기에 쓰지 않는다(소스 스캔).

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

- `dev_m3.py`·`dev.py`를 수정하지 마라. `driver.py`는 `same_team_eligible` 액터 추가 한 가지만 수정한다(기존 `requester`·`other_team_user`·`same_team_g4` 동작·반환·예외는 바꾸지 않는다). 이유: 기존 dev 러너·골든·테스트의 동작을 바꾸지 않는다.
- 요청 문장·답변·티켓 본문·승인 초안 본문을 원자료·manifest·로그에 남기지 마라. 이유: 공격 문장 원문은 `data/redteam/`이며 결과 문서·원장에 새면 안 된다.
- `semantic` 인자로 분류기를 직접 주지 마라. 이유: 스위치는 환경 변수 하나가 정하며 `build_task_service`의 기본 결선을 그대로 쓴다(FP-070).
- 실패한 실행을 이어서 재개하는 코드를 만들지 마라. 이유: 등록 §10: 처음부터 새 프로세스로 재실행한다. 부분 항목 선택 인자도 만들지 않는다.
- `make redteam-run`·`make smoke-guardrail`을 실행하지 마라. 이유: 실제 서비스에 접속하는 사용자 실행 항목이다.
- 승인 정책을 reject 외로 바꾸는 인자를 만들지 마라. 이유: 등록 §9에 고정된 정책이다.

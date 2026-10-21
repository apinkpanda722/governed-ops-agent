# Step 0: rollout-registration

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/M8_DESIGN_DRAFT.md`(0-3절, 1-2절, **2-3절 전체**, 4-1절 ② 표, 6절 7·9행, 7절 "feature flag 점진 배포"·"점진 배포 한계", 8절 결정 4·6·7과 그 아래 정정 문단)
- `/docs/results/hypotheses-m8-toolcall.md`(사전 등록 문서의 형식 선례, 읽기만 한다)
- `/docs/results/hypotheses-test-targets.md`(1절 ⑤ v2 추가 지연 p95 7503.8 ms ≤ 8000 ms의 정의, 읽기만 한다)
- `/src/govops/api/tasks.py`(`start` 201~225행, `_launch`·`_run` 85~91·227~271행, `conversations` 376~416행, `approvals` 540~567행, `startup_cleanup` 691~750행, 읽기만 한다)
- `/src/govops/api/wiring.py`(`build_task_service` 46~123행, `open_task_service` 126~205행, 읽기만 한다)
- `/src/govops/api/retention.py`(`run_prune`·`startup_prune`·CLI, 읽기만 한다)
- `/src/govops/gateway/client.py`(`ClassifyResult` 106~111행, `classify` 385~525행, 읽기만 한다)
- `/src/govops/agent/graph.py`(`classify_items` 288~323행, 읽기만 한다)
- `/src/govops/eval/redteam_judge.py`(`v2_span_ms_from_trace`·`nearest_rank_p` 367~397행, 읽기만 한다)
- `/tests/test_m8_freeze.py`(등록 문서 동결 방식)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `docs/results/hypotheses-m8-rollout.md`(신규), `tests/test_m8_freeze.py`(`EXPECTED`에 키 하나 추가에 한함).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `docs/results/hypotheses-m8-rollout.md`, `tests/test_m8_freeze.py`
열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/rollout/`, 평가 명세를 읽는 `load_specs`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다. 이 제한은 이 step이 새로 작성하는 테스트에 한한 것이며, 기존 `tests/test_m7_freeze.py`가 `scripts/freeze_digest.py`로 해시를 계산하며 동결 경로 파일을 바이트로 읽는 것은 `make verify`의 일부이므로 허용한다. Codex 세션이 그 파일 내용을 출력·인용하는 것은 여전히 금지다.

**목적**: 항목 4(feature flag 점진 배포)의 배정 규칙·기록·롤백 지표·임계·고정 코드·CLI 인자를 **구현 전에** 고정한다(FP-043). 이 step은 코드를 만들지 않는다. 이후 step 1~6이 이 문서의 이름·값을 그대로 구현하고, step 1·5가 문서와 코드의 일치를 테스트로 검사한다. 이 측정은 합격선이 있는 판정이 아니라 **운영 장치의 등록**이다. 공식 구성은 v1이고 이 장치는 공식 M6 수치를 바꾸지 않는다.

### 1. 등록 문서 `docs/results/hypotheses-m8-rollout.md`

형식은 `hypotheses-m8-toolcall.md`를 따른다. 첫 줄 아래에 `M8 추가 측정 — 공식 M6 수치 아님`과 `scope: "m8"`을 쓴다. 아래 절을 이 순서로, 아래에 적은 값 그대로 쓴다(값을 바꾸거나 더하지 않는다. 문장은 다듬어도 된다).

1. **목적과 범위**: v2 Guardrail(분류기)을 사용자 일부에게만 켜는 점진 배포 장치다. 공식 구성은 v1(`hypotheses-test-targets.md`의 `v2_normal_fp_ge_2`)이고 기본은 꺼짐이다. 필수 live 없음(로컬 시연, 선택 smoke). 에이전트·프롬프트·Guardrail 규칙·v2 설정(`classifier_v2.json`)은 바꾸지 않는다.
2. **켜는 스위치**: 환경 변수 `GOVOPS_V2_ROLLOUT`(`on|off`, 기본 `off`, `env_or`). `off`이면 서버는 기존 `open_task_service` 경로로 서비스 1개를 만든다. `on`이면 `open_rollout_service`가 v1·v2 서비스를 시작 때 둘 다 만든다(`service.start` 2개). `GOVOPS_GUARDRAIL_V2=on`과 `GOVOPS_V2_ROLLOUT=on`을 함께 설정하면 감사 쓰기 전에 `StartupError("rollout_conflict")`로 시작을 거부한다. v2 서비스용 chat에 `classify`가 없으면 `StartupError("guardrail_v2_unavailable")`(기존 코드 재사용).
3. **버킷과 배정**: `SALT = "govops-m8-rollout-v1"`(변경 금지, 바꾸면 전원 재배정). `digest = sha256((SALT + user_label).encode("utf-8")).hexdigest()`, `bucket = int(digest[:8], 16) % 10000`. 배정: `kill`이면 `v1`, 아니면 `bucket < pct_bp`이면 `v2`, 그 밖은 `v1`. `pct_bp`는 0~10000 정수(베이시스 포인트, 10000 = 100%).
4. **상태 파일**: `<rollout_dir>/state.json` = `{"pct_bp": int, "kill": bool, "rolled_back_at": str|null, "reason": str|null}`(키 정확히 이 넷). `rollout_dir` = `env_or("GOVOPS_ROLLOUT_DIR", "data/interim/rollout")`. 쓰기는 임시 파일 + `os.replace` 원자 교체, 파일 0600·디렉터리 0700. 파일 없음 = `pct_bp=0, kill=false`. JSON 오류·키 불일치·형식 오류 = `pct_bp=0, kill=true, reason="state_invalid"`(fail-safe, 전원 v1). 실행 산출물이라 커밋하지 않는다.
5. **변형 고정과 저장**: 새 대화(`POST /conversations`, `POST /tasks`)를 만들 때 한 번 배정한다. Postgres 테이블 `govops_conversation_variant(conversation_id TEXT PRIMARY KEY, variant TEXT NOT NULL, bucket INTEGER NOT NULL, pct_bp INTEGER NOT NULL, assigned_at TEXT NOT NULL)`(`GOVOPS_DATABASE_URL`이 없으면 인메모리). 테이블은 저장소 코드가 처음 쓸 때 `CREATE TABLE IF NOT EXISTS`로 만든다(`make db-init`은 바꾸지 않는다). 배정한 변형의 서비스로 대화를 시작한 **뒤** 행을 쓴다(`conversation_id`는 `TaskService.start()` 안에서 생긴다). 이후 모든 턴·승인·조회는 행의 변형 서비스로 간다. 행이 없는 대화는 `v1`로 보내고 노출 기록에 `variant_missing`을 대화당 한 번 남긴다. 킬·비율 변경은 새 대화에만 영향이다.
6. **변형 행 삭제**: thread를 실제로 지우는 지점(`retention.run_prune`의 `turns.delete_conversations(result.deleted_ids)`와 같은 곳)에서만 함께 지운다. `startup_prune`과 `python -m govops.api.retention`(`make checkpoint-prune`)이 이 경로를 지난다. 테이블이 없으면 삭제 경로가 테이블을 만들지 않고 0을 돌려준다. 소프트 삭제(`delete_conversation` → `mark_deleted`)는 행을 남긴다(이유: `is_deleted`가 공유 색인이라 어느 서비스로 가도 404이고, 먼저 지우면 v1 서비스가 v2 thread를 맡는다).
7. **노출 기록**: `<rollout_dir>/exposures.jsonl`(append-only). 행 키는 `event`·`ts`와 아래 이벤트별 키뿐이다. `assigned`: `conversation_id`·`user_sha256`·`bucket`·`variant`·`pct_bp`·`kill`. `variant_missing`: `conversation_id`. `rollback`: `reason`·`pct_bp`(롤백 직전 값). 사용자 라벨·요청·응답 본문·토큰은 쓰지 않는다. 감사 체인에는 쓰지 않는다.
8. **분류 호출 기록**: v2 서비스의 `semantic` 자리에 `classify`만 그대로 전달하는 계측 래퍼 `RecordingClassifier`를 둔다. 호출마다 `<rollout_dir>/classify.jsonl`에 `{"ts", "turn_id", "status", "started_ms", "ended_ms"}`(키 정확히 이 다섯)을 남긴다. `turn_id`는 `classify`가 받는 `task_id`(턴 ID), `status`는 `ClassifyResult.status`(`ok|timeout|rate_limited|error|parse|v1_blocked`) 또는 예외일 때 `raised`, `started_ms`·`ended_ms`는 `time.monotonic()*1000`. 래퍼는 결과·인자·예외를 바꾸지 않는다(예외는 기록 뒤 그대로 다시 던진다). 출처: `ClassifyResult`(`gateway/client.py:106-111`), timeout은 `APITimeoutError`·`GatewayError(timeout=True)`, parse는 `semantic.parse_label`이 `None`일 때, 그 밖의 예외는 `error`, 예산·원장 예외만 밖으로 던짐(`classify` 385~525행). 수집 경로를 이렇게 정한 이유: 턴은 서비스 내부 백그라운드 작업(`_launch` → `_run`)에서 끝나 라우터가 끝나는 시점을 모르고, 분류 지연은 trace span(`graph.py:299-317`의 `guardrail.v2_ms`)에만 있다.
9. **평가 창**: 새 대화를 배정할 때마다(배정 전에) 평가한다. v2 창 = `variants.recent("v2", limit=60)`(배정 시각 내림차순) 중 현재 상태가 `running`·`awaiting_approval`이 아니고 상태를 읽을 수 있는 대화의 앞 20개(K=20). v1 대조 창 = 같은 규칙의 `v1` 20개. 상태는 각 변형 서비스의 그래프에서 `get_task`로 읽는다. 분류 기록은 `classify.jsonl`의 마지막 5000줄을 읽고 `turns.conversation_of(turn_id)`로 대화에 연결해 v2 창의 대화에 속한 것만 쓴다.
10. **세 지표**:
    - 분류 비정상 비율 = (v2 창 분류 호출 중 `status ∈ {timeout, rate_limited, error, parse, raised}`) / (v2 창 분류 호출 전체). `v1_blocked`는 분모에만 든다.
    - 차단 비율 차 = (v2 창 중 상태 `blocked_input`인 대화 비율) − (v1 대조 창의 같은 비율).
    - 분류 p95 = v2 창 대화에 속한 턴마다 분류 호출 구간 `[started_ms, ended_ms]`의 합집합 길이를 구하고, 그 값들의 nearest-rank p95(`ordered[ceil(n*0.95)-1]`).
11. **판정 규칙**(8절 결정 4의 값): 최소 표본 20, 창 K=20. 순서: (a) v2 창이 20개 미만이면 `insufficient_v2_sample`로 아무것도 하지 않는다(롤백도 확대도 없음). (b) v2 창에 분류 호출이 0건이면 롤백 `rollback_no_classify_calls`(빈 집합으로 통과하지 않음). (c) 비정상 비율 > 0.05 → `rollback_classify_abnormal`. (d) v1 대조 창이 20개 미만이면 차단 기준만 보류하고 `insufficient_v1_control`을 보고한다. 아니면 차 > 0.10 → `rollback_block_diff`. (e) p95 > 8000 ms → `rollback_classify_p95`. 비교는 모두 엄격한 초과이며 같으면 롤백하지 않는다. 사유가 여럿이면 (b)(c)(d)(e) 순서로 모두 기록하고 상태 파일의 `reason`에는 첫 사유를 쓴다. 롤백 = `state.json`을 `pct_bp=0, kill=true, rolled_back_at=<UTC ISO>, reason=<첫 사유>`로 쓰고 노출 기록에 `rollback` 행을 남긴다. 자동 확대는 없다.
12. **CLI**(단일 진입점 `python -m govops.rollout.cli`, 출력은 한 줄 `key=value` 공백 구분, 경로·본문 미출력):
    - `status`: `pct_bp=<int> kill=<true|false> reason=<코드|none> assigned_v1=<n> assigned_v2=<n> variant_missing=<n> rollbacks=<n>`(마지막 넷은 `exposures.jsonl` 집계).
    - `set --pct-bp <0~10000>`: `pct_bp`를 쓰고 `kill=false`, `reason=null`, `rolled_back_at=null`로 되돌린다. 출력 `OK pct_bp=<n>`.
    - `kill [--reason <code>]`: `pct_bp=0, kill=true`, `reason` 기본 `manual_kill`(형식 `^[a-z][a-z_]{0,39}$`). 출력 `OK kill reason=<code>`.
    - 실패: 인자 오류 `FAIL m8_rollout_invalid_arguments`, 상태 쓰기 실패 `FAIL m8_rollout_state_write_failed`, `status`에서 노출 기록 파일이 깨짐 `FAIL m8_rollout_records_invalid`(모두 종료 코드 1). 상태 파일 손상은 오류가 아니며 `status`가 fail-safe 값(`kill=true reason=state_invalid`)을 출력한다.
    - Make 타깃(블록당 한 명령으로 문서에 쓴다): `make m8-rollout-status`, `make m8-rollout-set PCT_BP=500`, `make m8-rollout-kill`, `make m8-rollout-exercise`. CLI 세 타깃의 레시피는 `uv run --env-file .env python -m govops.rollout.cli …`이고(`.env`의 `GOVOPS_ROLLOUT_DIR`를 따르도록), 시연 타깃은 `uv run python -m govops.api.rollout_exercise`다(`.env` 불필요, 임시 디렉터리만 씀). 실행 예시가 실제 CLI 인자와 같은지는 step 5가 테스트로 검사한다.
13. **로컬 시연**: `python -m govops.api.rollout_exercise`(`make m8-rollout-exercise`). 가짜 chat·인메모리 저장소·임시 디렉터리만 쓰고 API를 호출하지 않는다. 두 경로를 모두 실행한다: `exercise_pass_path`(분류 정상, 롤백 없음, 변형 고정 유지)와 `exercise_rollback_path`(분류 `timeout`이 임계를 넘어 롤백, 이후 새 대화는 v1, 기존 v2 대화는 v2 유지). 출력은 단계마다 `PASS <단계>` 또는 `FAIL <단계> <코드>`이고 SKIP은 없다. 하나라도 FAIL이면 종료 코드 1.
14. **고정 코드 목록**(코드 이름의 단일 출처): 시작 `rollout_conflict`·`guardrail_v2_unavailable`; 상태 `state_invalid`; 평가 `insufficient_v2_sample`·`insufficient_v1_control`·`rollback_no_classify_calls`·`rollback_classify_abnormal`·`rollback_block_diff`·`rollback_classify_p95`; 노출 이벤트 `assigned`·`variant_missing`·`rollback`; 분류 기록 상태 `ok`·`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`·`raised`; CLI `m8_rollout_invalid_arguments`·`m8_rollout_state_write_failed`·`m8_rollout_records_invalid`·`manual_kill`; 운영 경고(표준 오류에 `WARN <코드>` 한 줄, 코드별 프로세스당 한 번, 라우팅·분류 결과는 바꾸지 않음) `rollout_variant_write_failed`(변형 행 쓰기 실패: 대화는 이미 시작됐고 이후 `variant_missing`으로 v1)·`rollout_exposure_record_failed`(노출 기록 쓰기 실패)·`rollout_classify_record_failed`(분류 기록 쓰기 실패: 분류 결과는 그대로 돌려줌); 시연 단계 `exercise_pass_path`·`exercise_rollback_path`.
15. **한계(등록)**: 킬 스위치 뒤에도 진행 중인 v2 대화는 끝날 때까지 v2로 남는다. 행 쓰기 전에 서버가 죽은 v2 대화는 재시작 뒤 v1 서비스가 처리한다. 차단 지표는 대화의 마지막 턴 상태만 보고 v2 서비스의 v1 훅 차단도 섞인다. p95는 M6 기준값 8000 ms를 쓰지만 M6 값(분류 span 합, `v2_span_ms_from_trace`)과 측정 지점이 다르다. 분류 기록의 `started_ms`·`ended_ms`는 프로세스 안에서만 비교할 수 있다. 실제 트래픽 비율 상승을 운영한 것이 아니다(README 표현은 설계 7절).
16. **기본 off에서 공식 v1 결과 불변 보증**: 설계 2-3절의 (1)~(5)를 그대로 옮긴다.
17. **사용자 실행**(블록당 한 명령): `make m8-rollout-exercise`, (선택) `make test-m5-pg`, (선택) `GOVOPS_V2_ROLLOUT=on`으로 띄운 서버의 두 사용자 smoke 뒤 `make audit-verify`.

### 2. `tests/test_m8_freeze.py`

`EXPECTED`에 `"docs/results/hypotheses-m8-rollout.md": "<digest>"` 한 키를 추가한다. digest는 이 파일의 `single_file_digest`로 1번에서 만든 **최종** 본문에 대해 계산한 값이다. 다른 키와 함수·테스트는 바꾸지 않는다.

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

- 1번에 적은 값(임계 0.05·0.10·8000 ms, K=20, 최소 표본 20, 후보 60, 꼬리 5000줄, SALT, 키 이름, 코드 이름, CLI 인자)을 바꾸거나 새 값을 더하지 마라. 이유: 이 문서는 커밋 뒤 동결되며 이후 step이 값을 그대로 구현한다(FP-043).
- `git add`·`git commit`을 하지 마라. 이유: git 작업은 실행기가 step 완료 뒤에 한다. 그래서 동결 테스트는 `git ls-files`가 아니라 `single_file_digest`를 쓴다(FP-074).
- `tests/test_m8_freeze.py`의 기존 키·값·함수를 바꾸지 마라. 이유: 기존 등록의 동결 증거다. 키 하나 추가에 한한다.
- 문서에 실제 실행 결과·수치를 쓰지 마라. 이유: 아직 실행이 없다. 기준선 값은 이미 확정된 M6 값(7503.8 ms, 8000 ms)만 인용한다(FP-059: 이번 결과와 이전 확정 값을 구분).

# M8 feature flag 점진 배포 사전 등록 (`hypotheses-m8-rollout.md`)

**M8 추가 측정 — 공식 M6 수치 아님** · `scope: "m8"`

상태: **등록 커밋 대기**. 구현 전에 이 문서로 배정 규칙·기록·롤백 지표·임계·고정 코드·CLI 인자를 고정하며, 등록 커밋 뒤 본문을 수정하지 않는다(FP-043). `tests/test_m8_freeze.py`가 최종 본문의 digest를 고정한다. 이후 step 1~6은 이 문서의 이름·값을 그대로 구현하고, step 1·5는 문서와 코드의 일치를 검사한다. 이 step은 코드를 구현하거나 운영 측정을 실행하지 않는다.

## 1. 목적과 범위

v2 Guardrail(분류기)을 사용자 일부에게만 켜는 점진 배포 장치다. 공식 구성은 v1(`hypotheses-test-targets.md`의 `v2_normal_fp_ge_2`)이고 기본은 꺼짐이다. 이 측정은 합격선이 있는 판정이 아니라 운영 장치의 등록이며 공식 M6 수치를 바꾸지 않는다. 필수 live는 없으며 로컬 시연과 선택 smoke를 둔다. 에이전트·프롬프트·Guardrail 규칙·v2 설정(`classifier_v2.json`)은 바꾸지 않는다.

## 2. 켜는 스위치

환경 변수 `GOVOPS_V2_ROLLOUT`(`on|off`, 기본 `off`, `env_or`)로 켠다. `off`이면 서버는 기존 `open_task_service` 경로로 서비스 1개를 만든다. `on`이면 `open_rollout_service`가 v1·v2 서비스를 시작 때 둘 다 만든다(`service.start` 2개).

`GOVOPS_GUARDRAIL_V2=on`과 `GOVOPS_V2_ROLLOUT=on`을 함께 설정하면 감사 쓰기 전에 `StartupError("rollout_conflict")`로 시작을 거부한다. v2 서비스용 chat에 `classify`가 없으면 `StartupError("guardrail_v2_unavailable")`로 거부한다(기존 코드 재사용).

## 3. 버킷과 배정

`SALT = "govops-m8-rollout-v1"`은 변경 금지다. 바꾸면 전원 재배정된다.

```python
digest = sha256((SALT + user_label).encode("utf-8")).hexdigest()
bucket = int(digest[:8], 16) % 10000
```

배정은 `kill`이면 `v1`, 아니면 `bucket < pct_bp`이면 `v2`, 그 밖은 `v1`이다. `pct_bp`는 0~10000 정수(베이시스 포인트, 10000 = 100%)다. 노출 기록의 `user_sha256`은 위 `digest`다.

## 4. 상태 파일

`<rollout_dir>/state.json`의 형식은 `{"pct_bp": int, "kill": bool, "rolled_back_at": str|null, "reason": str|null}`이며 키는 정확히 이 넷이다. `rollout_dir`는 `env_or("GOVOPS_ROLLOUT_DIR", "data/interim/rollout")`이다.

쓰기는 임시 파일 + `os.replace` 원자 교체로 하며 파일은 0600, 디렉터리는 0700이다. 파일 없음은 `pct_bp=0, kill=false`로 처리한다. JSON 오류·키 불일치·형식 오류는 `pct_bp=0, kill=true, reason="state_invalid"`로 처리한다(fail-safe, 전원 v1). 실행 산출물이므로 커밋하지 않는다.

## 5. 변형 고정과 저장

새 대화(`POST /conversations`, `POST /tasks`)를 만들 때 한 번 배정한다. Postgres 테이블은 `govops_conversation_variant(conversation_id TEXT PRIMARY KEY, variant TEXT NOT NULL, bucket INTEGER NOT NULL, pct_bp INTEGER NOT NULL, assigned_at TEXT NOT NULL)`이다. `GOVOPS_DATABASE_URL`이 없으면 인메모리를 쓴다.

테이블은 저장소 코드가 처음 쓸 때 `CREATE TABLE IF NOT EXISTS`로 만든다(`make db-init`은 바꾸지 않는다). 배정한 변형의 서비스로 대화를 시작한 **뒤** 행을 쓴다. `conversation_id`는 `TaskService.start()` 안에서 생긴다.

이후 모든 턴·승인·조회는 행의 변형 서비스로 간다. 행이 없는 대화는 `v1`로 보내고 노출 기록에 `variant_missing`을 대화당 한 번 남긴다. 킬·비율 변경은 새 대화에만 영향이다.

## 6. 변형 행 삭제

thread를 실제로 지우는 지점(`retention.run_prune`의 `turns.delete_conversations(result.deleted_ids)`와 같은 곳)에서만 변형 행을 함께 지운다. `startup_prune`과 `python -m govops.api.retention`(`make checkpoint-prune`)이 이 경로를 지난다. 테이블이 없으면 삭제 경로가 테이블을 만들지 않고 0을 돌려준다.

소프트 삭제(`delete_conversation` → `mark_deleted`)는 행을 남긴다. 이유: `is_deleted`가 공유 색인이라 어느 서비스로 가도 404이고, 먼저 지우면 v1 서비스가 v2 thread를 맡는다.

## 7. 노출 기록

`<rollout_dir>/exposures.jsonl`은 append-only다. 행 키는 `event`·`ts`와 아래 이벤트별 키뿐이다.

| 이벤트 | 이벤트별 키 |
|---|---|
| `assigned` | `conversation_id`·`user_sha256`·`bucket`·`variant`·`pct_bp`·`kill` |
| `variant_missing` | `conversation_id` |
| `rollback` | `reason`·`pct_bp`(롤백 직전 값) |

사용자 라벨·요청·응답 본문·토큰은 쓰지 않는다. 감사 체인에는 쓰지 않는다.

## 8. 분류 호출 기록

v2 서비스의 `semantic` 자리에 `classify`만 그대로 전달하는 계측 래퍼 `RecordingClassifier`를 둔다. 호출마다 `<rollout_dir>/classify.jsonl`에 `{"ts", "turn_id", "status", "started_ms", "ended_ms"}`(키 정확히 이 다섯)를 남긴다.

`turn_id`는 `classify`가 받는 `task_id`(턴 ID)다. `status`는 `ClassifyResult.status`(`ok|timeout|rate_limited|error|parse|v1_blocked`) 또는 예외일 때 `raised`다. `started_ms`·`ended_ms`는 `time.monotonic()*1000`이다. 래퍼는 결과·인자·예외를 바꾸지 않으며 예외는 기록 뒤 그대로 다시 던진다.

출처: `ClassifyResult`(`gateway/client.py:106-111`), timeout은 `APITimeoutError`·`GatewayError(timeout=True)`, parse는 `semantic.parse_label`이 `None`일 때, 그 밖의 예외는 `error`, 예산·원장 예외만 밖으로 던진다(`classify` 385~525행).

수집 경로를 이렇게 정한 이유: 턴은 서비스 내부 백그라운드 작업(`_launch` → `_run`)에서 끝나 라우터가 끝나는 시점을 모르고, 분류 지연은 trace span(`graph.py:299-317`의 `guardrail.v2_ms`)에만 있다.

## 9. 평가 창

새 대화를 배정할 때마다 배정 전에 평가한다. v2 창은 `variants.recent("v2", limit=60)`(배정 시각 내림차순) 중 현재 상태가 `running`·`awaiting_approval`이 아니고 상태를 읽을 수 있는 대화의 앞 20개(K=20)다. v1 대조 창은 같은 규칙의 `v1` 20개다.

상태는 각 변형 서비스의 그래프에서 `get_task`로 읽는다. 분류 기록은 `classify.jsonl`의 마지막 5000줄을 읽고 `turns.conversation_of(turn_id)`로 대화에 연결해 v2 창의 대화에 속한 것만 쓴다.

## 10. 세 지표

- 분류 비정상 비율 = (v2 창 분류 호출 중 `status ∈ {timeout, rate_limited, error, parse, raised}`) / (v2 창 분류 호출 전체). `v1_blocked`는 분모에만 든다.
- 차단 비율 차 = (v2 창 중 상태 `blocked_input`인 대화 비율) − (v1 대조 창의 같은 비율).
- 분류 p95 = v2 창 대화에 속한 턴마다 분류 호출 구간 `[started_ms, ended_ms]`의 합집합 길이를 구하고, 그 값들의 nearest-rank p95(`ordered[ceil(n*0.95)-1]`).

## 11. 판정 규칙

설계 8절 결정 4의 값으로 최소 표본 20, 창 K=20을 고정한다. 판정 순서는 다음과 같다.

| 순서 | 조건과 처리 |
|---|---|
| (a) | v2 창이 20개 미만이면 `insufficient_v2_sample`로 아무것도 하지 않는다(롤백도 확대도 없음). |
| (b) | v2 창에 분류 호출이 0건이면 롤백 `rollback_no_classify_calls`(빈 집합으로 통과하지 않음). |
| (c) | 비정상 비율 > 0.05 → `rollback_classify_abnormal`. |
| (d) | v1 대조 창이 20개 미만이면 차단 기준만 보류하고 `insufficient_v1_control`을 보고한다. 아니면 차 > 0.10 → `rollback_block_diff`. |
| (e) | p95 > 8000 ms → `rollback_classify_p95`. |

비교는 모두 엄격한 초과이며 같으면 롤백하지 않는다. 사유가 여럿이면 (b)(c)(d)(e) 순서로 모두 기록하고 상태 파일의 `reason`에는 첫 사유를 쓴다.

롤백은 `state.json`을 `pct_bp=0, kill=true, rolled_back_at=<UTC ISO>, reason=<첫 사유>`로 쓰고 노출 기록에 `rollback` 행을 남기는 것이다. 자동 확대는 없다.

## 12. CLI

단일 진입점은 `python -m govops.rollout.cli`다. 출력은 한 줄 `key=value` 공백 구분이며 경로·본문은 출력하지 않는다.

| 명령 | 동작·출력 |
|---|---|
| `status` | `pct_bp=<int> kill=<true|false> reason=<코드|none> assigned_v1=<n> assigned_v2=<n> variant_missing=<n> rollbacks=<n>`(마지막 넷은 `exposures.jsonl` 집계). |
| `set --pct-bp <0~10000>` | `pct_bp`를 쓰고 `kill=false`, `reason=null`, `rolled_back_at=null`로 되돌린다. 출력 `OK pct_bp=<n>`. |
| `kill [--reason <code>]` | `pct_bp=0, kill=true`, `reason` 기본 `manual_kill`(형식 `^[a-z][a-z_]{0,39}$`). 출력 `OK kill reason=<code>`. |

실패는 모두 종료 코드 1이다: 인자 오류 `FAIL m8_rollout_invalid_arguments`, 상태 쓰기 실패 `FAIL m8_rollout_state_write_failed`, `status`에서 노출 기록 파일이 깨짐 `FAIL m8_rollout_records_invalid`. 상태 파일 손상은 오류가 아니며 `status`가 fail-safe 값(`kill=true reason=state_invalid`)을 출력한다.

Make 타깃은 블록당 한 명령이다.

```bash
make m8-rollout-status
```

```bash
make m8-rollout-set PCT_BP=500
```

```bash
make m8-rollout-kill
```

```bash
make m8-rollout-exercise
```

CLI 세 타깃의 레시피는 각각 다음 명령이다. `.env`의 `GOVOPS_ROLLOUT_DIR`를 따른다.

```bash
uv run --env-file .env python -m govops.rollout.cli status
```

```bash
uv run --env-file .env python -m govops.rollout.cli set --pct-bp $(PCT_BP)
```

```bash
uv run --env-file .env python -m govops.rollout.cli kill
```

시연 타깃의 레시피는 다음 명령이다. `.env`가 불필요하며 임시 디렉터리만 쓴다. 실행 예시가 실제 CLI 인자와 같은지는 step 5가 테스트로 검사한다.

```bash
uv run python -m govops.api.rollout_exercise
```

## 13. 로컬 시연

`python -m govops.api.rollout_exercise`(`make m8-rollout-exercise`)는 가짜 chat·인메모리 저장소·임시 디렉터리만 쓰고 API를 호출하지 않는다. 두 경로를 모두 실행한다: `exercise_pass_path`(분류 정상, 롤백 없음, 변형 고정 유지)와 `exercise_rollback_path`(분류 `timeout`이 임계를 넘어 롤백, 이후 새 대화는 v1, 기존 v2 대화는 v2 유지).

출력은 단계마다 `PASS <단계>` 또는 `FAIL <단계> <코드>`이고 SKIP은 없다. 하나라도 FAIL이면 종료 코드 1이다.

## 14. 고정 코드 목록

이 목록이 코드 이름의 단일 출처다.

| 분류 | 코드 |
|---|---|
| 시작 | `rollout_conflict`·`guardrail_v2_unavailable` |
| 상태 | `state_invalid` |
| 평가 | `insufficient_v2_sample`·`insufficient_v1_control`·`rollback_no_classify_calls`·`rollback_classify_abnormal`·`rollback_block_diff`·`rollback_classify_p95` |
| 노출 이벤트 | `assigned`·`variant_missing`·`rollback` |
| 분류 기록 상태 | `ok`·`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`·`raised` |
| CLI | `m8_rollout_invalid_arguments`·`m8_rollout_state_write_failed`·`m8_rollout_records_invalid`·`manual_kill` |
| 운영 경고 | `rollout_variant_write_failed`·`rollout_exposure_record_failed`·`rollout_classify_record_failed` |
| 시연 단계 | `exercise_pass_path`·`exercise_rollback_path` |

운영 경고는 표준 오류에 `WARN <코드>` 한 줄로, 코드별 프로세스당 한 번 출력하며 라우팅·분류 결과는 바꾸지 않는다.

- `rollout_variant_write_failed`: 변형 행 쓰기 실패. 대화는 이미 시작됐고 이후 `variant_missing`으로 v1이다.
- `rollout_exposure_record_failed`: 노출 기록 쓰기 실패.
- `rollout_classify_record_failed`: 분류 기록 쓰기 실패. 분류 결과는 그대로 돌려준다.

## 15. 한계(등록)

킬 스위치 뒤에도 진행 중인 v2 대화는 끝날 때까지 v2로 남는다. 행 쓰기 전에 서버가 죽은 v2 대화는 재시작 뒤 v1 서비스가 처리한다.

차단 지표는 대화의 마지막 턴 상태만 보고 v2 서비스의 v1 훅 차단도 섞인다. p95는 M6 기준값 8000 ms를 쓰지만 M6 값(분류 span 합, `v2_span_ms_from_trace`)과 측정 지점이 다르다. 기존 M6 등록의 ⑤ v2 추가 지연 p95는 7503.8 ms ≤ 8000 ms이며 이번 실행 결과가 아니다. 분류 기록의 `started_ms`·`ended_ms`는 프로세스 안에서만 비교할 수 있다.

실제 트래픽 비율 상승을 운영한 것이 아니다. README에는 설계 7절에 따라 로컬 가짜 모델의 PASS/롤백 두 경로 시연과 실제 두 사용자 smoke의 실행 여부를 구분하고, 공식 구성은 v1이며 기본 0%이고 v2는 공식 수치가 아님을 함께 쓴다.

롤아웃을 끈 서버(`open_task_service` 경로)의 `startup_prune`은 변형 저장소를 넘기지 않으므로, 그때 지운 thread의 변형 행은 남는다. prune은 그 실행에서 지운 thread의 행만 지우므로 이후 `make checkpoint-prune`이나 롤아웃을 켠 시작도 이 행을 지우지 않는다. 행에는 conversation_id·변형·버킷·비율·시각만 있고, 롤백 평가에서는 thread가 없어 건너뛴다.

## 16. 기본 off에서 공식 v1 결과 불변 보증

(1) `GOVOPS_V2_ROLLOUT` 기본 off이면 lifespan이 `open_task_service`를 부르고 서비스 1개·`service.start` 1개(기존 경로, 테스트), (2) 켬 + pct=0이면 분류 호출 0건(테스트), (3) 동결 digest·`service.start` 해시 대조, (4) 평가 러너 경로(`build_task_service` 직접 호출)는 이 코드를 지나지 않음, (5) 골든·재생 테스트 무수정 통과.

## 17. 사용자 실행

구현 후 사용자 실행이며 블록당 한 명령이다.

```bash
make m8-rollout-exercise
```

선택: 임시 Postgres 검증.

```bash
make test-m5-pg
```

선택: `GOVOPS_V2_ROLLOUT=on`으로 띄운 서버의 두 사용자 smoke 뒤 감사 검증.

```bash
make audit-verify
```

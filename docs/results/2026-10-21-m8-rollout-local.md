M8 추가 측정 — 공식 M6 수치 아님

# M8 ④ v2 Guardrail 점진 배포 로컬 결과 (m8-rollout)

날짜: 2026-10-21. 이 문서는 운영 장치(feature flag 점진 배포)의 구현과 로컬 검증 결과다. 합격선이 있는 측정이 아니고 공식 구성(v1)과 공식 M6 수치를 바꾸지 않는다. 실제 API 호출은 0회이며 live 실행은 없다. 실행 결과는 모두 **사용자 보고**(2026-10-21)이고, 이 세션이 직접 실행한 것은 `make verify`와 CI뿐이다. 에이전트·프롬프트·Guardrail 규칙·v2 설정(`classifier_v2.json`)은 바꾸지 않았다.

**사전 등록**(구현 전 커밋, FP-043)

| 등록 | 커밋 | 내용 |
|---|---|---|
| [점진 배포 등록](hypotheses-m8-rollout.md) | `92434bd` | 스위치·버킷·상태 파일·변형 고정·노출·분류 기록·평가 창·세 지표·판정 규칙·CLI·시연·고정 코드·한계 |
| [추가 등록 1](hypotheses-m8-rollout-addendum-1.md) | `5e0c2a0`(코드 `bddac8b`보다 먼저) | 분류 기록 손상 시 `rollback_records_invalid` 롤백, 턴→대화 연결을 `turn_ids_of`로(구현 변경) |

## 1. 한눈에 보기

| 항목 | 결과 | 출처 |
|---|---|---|
| 로컬 시연 `make m8-rollout-exercise` | `PASS exercise_pass_path`, `PASS exercise_rollback_path` (main `3d5d2f3`, #241 머지 후) | 사용자 보고 |
| 같은 시연, 수정 전 | 같은 두 줄 PASS (feature/m8-rollout `2ab643d`) | 사용자 보고 |
| 같은 시연, #245 뒤 재실행 | 같은 두 줄 PASS (main, #245 `6d40ee9` 포함 코드. 그 뒤 커밋은 설계·phase 파일뿐) | 사용자 보고 |
| `make test-m5-pg` | 8 passed, skip 0 (feature/m8-rollout `2ab643d`). 변형 테이블 테스트 `test_variant_store_and_prune_in_isolated_database`가 실제 임시 Postgres에서 실행됨 | 사용자 보고 |
| `make verify` | exit 0 (#239·#241 각 커밋, CI verify pass) | 이 세션 |
| 실제 서버 `GOVOPS_V2_ROLLOUT=on` 운영·두 사용자 smoke·`make audit-verify` | **실행하지 않음**(선택 항목) | — |

`make test-m5-pg`는 #241 뒤에 다시 실행하지 않았다. #241(`2ab643d..3d5d2f3`)은 Postgres 저장소 코드(`api/variant.py`·`api/retention.py`·`api/conversations.py`)와 `tests/test_m5_pg.py`를 바꾸지 않았다(diff로 확인, 바뀐 파일은 `api/routed.py`·`rollout/policy.py`와 테스트 3개). 다만 롤백 평가가 이제 `PostgresTurnIndex.turn_ids_of`를 부른다. 이 메서드는 기존 코드이며 기존 `test_turn_index_turn_ids_postgres`가 검증하지만, 롤백 평가와 결합한 실제 Postgres 실행은 없다.

## 2. 등록한 핵심 값

| 구분 | 값 |
|---|---|
| 스위치 | `GOVOPS_V2_ROLLOUT`(`on`·`off`, 기본 `off`). `GOVOPS_GUARDRAIL_V2=on`과 함께 켜면 `rollout_conflict`로 시작 거부 |
| 배정 | `SALT = "govops-m8-rollout-v1"`, `bucket = int(sha256(SALT+label)[:8], 16) % 10000`, `bucket < pct_bp`이면 v2, `kill`이면 v1 |
| 평가 창 | 새 대화 배정마다. 후보 60개 중 `running`·`awaiting_approval`이 아닌 앞 20개(K=20), 최소 표본 20, 분류 기록 꼬리 5000줄 |
| 임계(엄격한 초과) | 분류 비정상 비율 > 0.05, 차단 비율 차 > 0.10, 분류 p95 > 8000 ms |
| 롤백 사유 | `rollback_records_invalid`(추가 등록 1, 가장 먼저), `rollback_no_classify_calls`, `rollback_classify_abnormal`, `rollback_block_diff`, `rollback_classify_p95` |
| 보류 사유 | `insufficient_v2_sample`(아무것도 하지 않음), `insufficient_v1_control`(차단 기준만 보류) |
| 롤백 처리 | `state.json`을 `pct_bp=0, kill=true, rolled_back_at, reason`으로 쓰고 노출 기록에 `rollback` 행, 이번 배정은 v1. 상태 쓰기가 실패해도 이번 배정만 v1, 예외 없음. 자동 확대 없음 |
| 운영 CLI | `make m8-rollout-status`, `make m8-rollout-set PCT_BP=500`, `make m8-rollout-kill` |

## 3. 기본 off에서 공식 v1 결과 불변 보증

| 보증 | 테스트 |
|---|---|
| 스위치 미설정이면 lifespan이 기존 `open_task_service`를 부르고 롤아웃 조립은 부르지 않음 | `tests/test_api_rollout_wiring.py::test_unset_rollout_uses_existing_lifespan_opener` |
| 스위치 미설정의 실제 lifespan은 서비스 1개, `service.start` 1개 | `tests/test_api_rollout_wiring.py::test_unset_rollout_real_lifespan_starts_one_service` |
| 미설정·빈 값·`off`는 꺼짐, 그 밖 값은 시작 거부 | `test_rollout_enabled_defaults`, `test_rollout_enabled_rejects_other_values` |
| 켬 + pct 0이면 전원 v1, 분류 호출 0건(Anthropic 호출 0에 해당) | `tests/test_api_routed.py::test_assignment_and_private_records[0-v1]` |
| 충돌 설정은 감사 쓰기·A 요청 전에 거부 | `test_conflict_precedes_audit_and_a_requests` |
| 기존 진입점 테스트·`wiring.py`·`tasks.py`·동결 경로 무수정 | `tests/test_api_entrypoint.py`(기존 테스트 무수정, 새 `test_rollout_lifespan_installs_service_and_audit_for_every_route` 추가), `tests/test_api_wiring*.py`·`tests/test_service_start_event.py` 무수정 통과, 동결 digest 테스트 통과 |
| 평가 러너 경로(`build_task_service` 직접 호출)는 이 코드를 지나지 않음 | `tests/test_api_wiring_semantic.py::test_all_service_callers_omit_semantic_except_frozen_fault_runners`(예외는 `api/rollout_wiring.py`의 두 호출로 정확히 고정) |

## 4. 롤백 자동화가 임계 초과 경로를 실제로 실행한다는 증거

PASS 경로만으로는 롤백 자동화가 검증되지 않는다(FP-073). 아래는 모두 임계 초과 경로를 실제로 실행한다.

- **시연**(`python -m govops.api.rollout_exercise`, 가짜 chat·인메모리·임시 디렉터리): `exercise_rollback_path`는 `pct_bp=10000`, 분류 `timeout`으로 v2 대화 20개를 끝까지 실행한 뒤 다음 배정에서 롤백한다. 테스트 `tests/test_m8_rollout_exercise.py::test_both_paths_execute_and_keep_bodies_out_of_records`가 상태 `kill=true, pct_bp=0`, `rollback` 행 정확히 1개, 배정 순서 `v2`×20 → `v1`, 분류 기록 21줄(sticky 턴 포함)을 단언한다. `exercise_pass_path`는 대화 60개(v1·v2 각 20개 이상)에서 롤백 행 0개다.
- **공허 통과 방지**: 분류기를 정상으로 바꾸면 `FAIL exercise_rollback_path rollback_missing`(`test_rollback_cannot_pass_with_healthy_classifier`), 임계를 낮추면 `FAIL exercise_pass_path kill_unexpected`(`test_pass_path_detects_unexpected_rollback`).
- **라우터**: `test_abnormal_classifier_rolls_back_only_at_minimum_sample`(19개면 `insufficient_v2_sample`로 롤백 없음, 20개면 `rollback_classify_abnormal` 롤백·다음 대화 v1), `test_unreadable_records_roll_back_before_sample_check`(손상 3종에서 표본 0개라도 `rollback_records_invalid`), `test_failed_rollback_state_write_assigns_v1`·`test_unreadable_records_with_failed_state_write_still_assigns_v1`(상태 쓰기 실패 시 v1·예외 없음), `test_sticky_turns_and_task_id_routing`(킬 뒤에도 기존 v2 대화는 v2).
- **정책 경계**: `tests/test_rollout_policy.py`의 `test_abnormal_threshold`·`test_block_difference_threshold`·`test_latency_threshold`(같으면 미롤백, 초과면 롤백), `test_no_classification_calls_rolls_back`, `test_insufficient_v1_only_defers_block_difference`, `test_multiple_reasons_follow_registration_order`.
- **문서와 코드 일치**: `test_constants_and_codes_match_frozen_registration`, `test_records_invalid_code_matches_addendum_registration`, CLI 예시 일치와 문서의 Make 타깃 네 개 정확 일치(`tests/test_rollout_cli.py`), 시연 모듈·레시피 일치(`test_documented_module_and_recipe_are_executable`).

## 5. 진행 이력

| PR | 내용 |
|---|---|
| #235 | 설계 정정(지시 46)과 phase 파일 step 0~6 |
| #237 | step 0 한계 절에 롤아웃 꺼짐 prune의 변형 행 잔존 사실 추가 |
| #239 | phase 구현(step 0~6) |
| #241 | 후속 2건: 분류 기록 손상 fail-safe(추가 등록 1), 평가 턴 연결 쿼리 축소 |
| #245 | 평가 창에서 소프트 삭제 대화 제외(추가 등록 2) |

phase 실행 중 blocked 3회는 모두 step 파일 결함이었고 코드를 쓰기 전 선점검에서 멈췄다.

| step | 원인 | 실패 패턴 |
|---|---|---|
| 1 rollout-policy | `tests/test_metrics_toolcall.py`가 `FORBIDDEN_IMPORTS["metrics"]`를 정확 일치로 단언하는데, 새 계층 규칙이 그 집합에 `govops.rollout`을 더함. 허용 목록 누락 | FP-071 |
| 5 rollout-cli | 일치 테스트가 문서의 모든 Make 타깃을 요구했는데 `m8-rollout-exercise`는 step 6이 만듦(step 순서 미대조) | FP-074 |
| 6 rollout-exercise | 제외 집합만 비우도록 허용했는데 step 5 테스트가 기대 집합을 따로 둠(앞 step 테스트 형태 미대조) | FP-074·FP-060 |

step 1 재개 때 실행기의 청결 검사 때문에 진행분을 보존 커밋 `af1e97a`로 남겼다. 이 커밋 시점에는 위 테스트 하나가 실패하며, 그 상태가 이력에 남아 있다(바로 다음 step 1 커밋 `38748f0`에서 해소).

설계 단계에서 같은 부류로 미리 막은 것: `tests/test_api_wiring_semantic.py`의 `semantic` 호출자 가드(정확한 예외로 확장), `tests/test_redteam_boundary.py`의 `api/` 문자열 금지, `.env.example` 변수 선언 누락.

## 6. 한계

등록 15절과 추가 등록 1 §3을 그대로 옮긴다.

- 킬 스위치 뒤에도 진행 중인 v2 대화는 끝날 때까지 v2로 남는다(새 대화만 v1).
- 변형 행을 쓰기 전에 서버가 죽은 v2 대화는 재시작 뒤 v1 서비스가 처리한다(`variant_missing`으로 기록).
- 차단 지표는 대화의 마지막 턴 상태만 보며, v2 서비스에서도 도는 v1 훅의 차단이 섞인다.
- 분류 p95는 M6 기준값 8000 ms를 쓰지만, M6 값(trace의 분류 span 합)과 측정 지점이 다르다(턴별 호출 구간 합집합).
- 분류 기록의 `started_ms`·`ended_ms`는 프로세스 안에서만 비교할 수 있다.
- 롤아웃을 끈 서버의 `startup_prune`이 지운 thread의 변형 행은 남고, 이후 어떤 prune도 지우지 않는다. 행에는 ID·변형·버킷·비율·시각만 있고 롤백 평가에서 건너뛴다.
- 평가 창 안에 소프트 삭제된 대화가 있으면 그 대화의 분류 호출은 지표에서 빠진다(차단 여부는 창에 들어간다). 창의 호출이 모두 빠지면 `rollback_no_classify_calls`로 롤백하지만, 일부만 빠지면 빠진 호출이 비정상이었을 경우 비정상 비율·p95가 낮아질 수 있다. 소프트 삭제된 대화는 상태 조회 경로에서 이미 404다. → [추가 등록 2](hypotheses-m8-rollout-addendum-2.md)(`e5abaee`)로 해소: 소프트 삭제된 대화는 v2 창과 v1 대조 창에서 모두 빼고 다음 후보로 채운다.
- 표본은 합성 시연과 테스트뿐이다. 실제 트래픽에서 임계가 적절한지는 확인하지 않았다.

## 7. 하지 않은 것

- 실제 서버에서 `GOVOPS_V2_ROLLOUT=on`으로 운영하지 않았다. 두 사용자 smoke와 켬 상태의 `make audit-verify`는 선택 항목이며 실행하지 않았다.
- 실제 트래픽의 비율을 올리며 운영한 것이 아니다. 비율 상승·롤백은 가짜 chat 시연과 테스트에서만 일어났다.
- v2 분류기의 품질이나 공식 구성 변경을 주장하지 않는다. 공식 구성은 v1이고 기본은 꺼짐이다.

## 8. README·포트폴리오 표현 범위 (설계 7절)

- 쓸 수 있는 것: "v2 Guardrail을 사용자 버킷 단위로 점진 노출하고, 대화 단위로 변형을 고정하며, 분류 실패율·차단 비율 차·분류 지연이 등록 임계를 넘으면 자동 롤백하는 장치를 구현했고, 로컬에서 가짜 모델로 PASS·롤백 두 경로를 시연했다."
- 함께 쓸 것: 공식 구성은 v1이고 기본 0%(꺼짐)이며 v2는 공식 수치가 아니라는 점, 킬 스위치 뒤 진행 중 v2 대화가 남는 한계.
- 쓰지 않는 것: 실제 트래픽 비율 상승을 운영했다는 표현, 실제 사용자 smoke를 했다는 표현, 롤백 임계가 실제 환경에서 검증됐다는 표현.

# M6 Guardrail v2 live 결과 (2026-10-14)

사전 등록: [`hypotheses-m6-guardrail-v2.md`](hypotheses-m6-guardrail-v2.md)(r1), [`hypotheses-m6-guardrail-v2-r2.md`](hypotheses-m6-guardrail-v2-r2.md)(r2). 이 문서는 phase ②의 합성 smoke 결과만 적는다(대화 본문·토큰·키 없음). **v1 대비 차단율·오탐률·탐지 성능에 대한 주장은 하지 않는다.** 이 결과는 test 결과가 아니다.

## 1. 구현·검증

- PR #130(사전 등록·phase 파일, 병합 `deb7d6e`), PR #132(phase 완료, 병합 `6bf0251`), PR #134(r1 문구·해시 등록, 병합 `41ff0a0`), PR #136(r2 출력 상한·타임아웃 개정, 병합 `bfcb244`), PR #138(훅 거부 요청 본문 교체, 병합 `660468c`).
- 분류기 설정 해시: r1 `ceb2a0017478ac4795ac521963d0e00806c11d9a7f12375663a960fc2a7a4a72`, r2 `4ee80e26c487b4a469d26e7ae1c01ac00fd23b70627a799b2e32ac0eabcea39a`.

## 2. 위치 판정 (사전 등록 §1 인용)

2026-10-13 호스트 → Anthropic 직접 호출 지연 측정(크기별 20회) 원문은 `short p50 1.15s p95 1.33s`, `p50 p50 1.18s p95 1.42s`, `max p50 1.10s p95 1.43s max 2.58s`, `>1.2s` 18/60이다. C1·C2 미충족으로 A 위치(LiteLLM 사전 훅 안)는 불성립이며 호스트 수집 지점을 택했다. 이 판정은 변하지 않는다. 다만 이 측정은 `max_tokens` 8로 추론이 잘린 상태였으므로 실제 분류 지연의 **하한**이다(실제는 더 길어 불성립 판정은 오히려 강해진다).

## 3. r1 → 진단 → r2 경위

1. **r1 smoke(obs off)**: `v2_format` 도구형 4/25, `v2_ok_rate` ok 79/106(parse 26). 원문은 §4(1).
2. **진단**: 도구형 요청의 출력 토큰이 모델 내부 추론에 쓰여 상한 16에서 텍스트가 비었다(`finish_reason length`). `tool_calls`는 0, 추론 토큰 83~242. 후속 문장 추가는 효과가 없었다. 원문은 r2 등록 파일 §1-2.
3. **r2**: `max_completion_tokens` 16 → 1024, 타임아웃 4.0 → 8.0(호스트·게이트웨이 `guardrail-v2`). 프롬프트·라벨·요청 구성은 불변. **개정 근거는 출력 형식(C4)뿐이며 라벨 분포는 근거가 아니다.** 재 smoke 기준(C4 25/25, ok ≥ 95%, p95 ≤ 8.0s)은 바꾸지 않았다. 재 smoke는 1회였다.

## 4. live 원문

### (1) r1 smoke (obs off)

```text
PASS v2_off_switch calls_zero
PASS v2_alias_access alias_allowed
FAIL v2_format user=25/25 tool=4/25
PASS v2_user_block unsafe=5 safe=0
PASS v2_tool_quarantine unsafe=5 safe=0
PASS v2_v1_blocked_passthrough settled_zero
PASS v2_ledger_settled rows=106
FAIL v2_ok_rate attempts=106 ok=79 timeout=0 rate_limited=0 error=0 parse=26 v1_blocked=1
PASS v2_latency tasks=20 p95_s=2.173
SKIP v2_no_raw_in_trace obs_off
SUMMARY PASS=7 FAIL=2 SKIP=1 INCONCLUSIVE=0
```

### (2) r2 smoke (obs off)

```text
PASS v2_off_switch calls_zero
PASS v2_alias_access alias_allowed
PASS v2_format user=25/25 tool=25/25
PASS v2_user_block unsafe=5 safe=0
PASS v2_tool_quarantine unsafe=5 safe=0
PASS v2_v1_blocked_passthrough settled_zero
PASS v2_ledger_settled rows=106
PASS v2_ok_rate attempts=106 ok=105 timeout=0 rate_limited=0 error=0 parse=0 v1_blocked=1
PASS v2_latency tasks=20 p95_s=2.271
SKIP v2_no_raw_in_trace obs_off
SUMMARY PASS=9 FAIL=0 SKIP=1
```

### (3) obs on, Langfuse 수정 전

- `make smoke-guardrail`: `hook_digests`~`fallback_passes_hook` 8개 PASS(`INFO fallback_passes_hook pre_hook_per_request=1`), **`FAIL langfuse_no_raw_input raw_input_found`**.
- `make smoke-semantic`: PASS 10(`v2_latency p95_s=2.165`, `v2_no_raw_in_trace raw_absent`, 나머지는 (2)와 같은 값).

### (4) Langfuse 진단 (합성 sentinel)

```text
blocked: http 400 code injection_direct
         observations 2개 (GENERATION litellm:team-platform ERROR)
         sentinel_path observations[0].modelParameters.messages x1
         sentinel_path observations[1].modelParameters.messages x1
allowed: observations 1개 (GENERATION agent-primary DEFAULT), sentinel_paths none
```

### (5) obs on, Langfuse 수정 후 (PR #138)

```text
PASS hook_digests
PASS allowed_models
PASS input_block
PASS team_unknown
PASS fail_closed
PASS tool_screen
PASS post_fail_open
INFO fallback_passes_hook pre_hook_per_request=1
PASS fallback_passes_hook
PASS langfuse_no_raw_input
SUMMARY PASS=9 FAIL=0 SKIP=0 INCONCLUSIVE=0
```

- `make smoke-semantic`: PASS 10(`v2_latency p95_s=2.296`, 나머지는 (2)와 같은 값).
- 참고: `make obs-up` 직후 첫 `smoke-guardrail` 실행은 LiteLLM이 기동 중이라 8단계가 `request_error`였다(재시작 0회, 재실행으로 해소).

### (6) `make cost-report` (2026-10-14 16:45)

| 용도 | 정산 호출 | 정산액 | task |
|---|---|---|---|
| guardrail_v2 | 583 | $0.058438 | 403 |
| agent_fallback | — | $0.234758 | — |
| agent_primary | — | $0.182397 | — |

Anthropic 합계 $0.293196 / 상한 $11.47, OpenAI $0.182397 / 상한 $9.13.

## 5. Langfuse 결함 (M4부터 있었던 결함)

- **내용**: LiteLLM v1.103.3은 사전 훅이 거부한 요청의 본문을 실패 콜백(Langfuse `modelParameters.messages`)에 기록하고, `turn_off_message_logging`이 이 필드를 가리지 않았다. 차단된 요청의 원문이 trace에 남았다.
- **발견**: M4에 추가한 `langfuse_no_raw_input`은 obs가 꺼진 실행에서는 SKIP이라 한 번도 실제로 돌지 않았다. M6에서 obs를 켠 live에서 처음 실행해 실패했다. FP-073로 기록했다.
- **수정**: PR #138. 훅이 거부 직전에 `data["messages"]`를 고정 자리표시자 1개 메시지로 바꾼다. 수정 뒤 `langfuse_no_raw_input` PASS(§4 (5)). 통과 요청·v1 판정·헤더는 불변.
- **남은 위험**: LiteLLM 업그레이드로 실패 경로의 기록 필드가 바뀔 수 있다. smoke 단계로 계속 확인한다(THREAT_MODEL 6절).

## 6. 비용

- guardrail_v2 정산 583호출·task 403, 합계 $0.058438(호출당 평균 ≈ $0.0001). 사전 등록 §7 재추정(호출당 ≈ $0.0002, 기본 task ≈ $0.0013)과 같은 자릿수다. (d)의 최대 시나리오 계산은 phase ③ 비용 계획에서 이 실측 평균으로 다시 한다.
- 원장 `data/manifest/costs.jsonl`의 이번 live 증분(r1·r2 smoke, 진단 1·2차, obs 재실행)을 이 PR에 함께 커밋한다.
- cost-report 경고 "추정 상한을 넘은 호출 1건": agent_primary, 비율 max 1.34. 원장 확인 결과 task `17ef606050a046d18ffa6fbafae90eb2`, 2026-10-06(UTC 02:03), 입력 116·출력 5토큰, 정산 $0.000282 / 추정 $0.000210. **M6 이전의 기존 행**이며 이번 phase와 무관하다.

## 7. 기준 판정 (r1 §6, r2 §5)

| 기준 | 결과 | 판정 |
|---|---|---|
| C4 출력 규격(두 형식 25/25) | user 25/25, tool 25/25 | 충족 |
| (b) 분류 완료율 ≥ 95%(시도 ≥ 100) | 105/106 (99.1%), v1_blocked 1 | 충족 |
| (c) task당 추가 지연 p95 ≤ 8.0s | 2.271s(obs off), 2.165s·2.296s(obs on), 합성 20 task | 충족 |
| (d) 비용 | 호출당 평균 ≈ $0.0001, 상한 대비 Anthropic 합계 $0.293196/$11.47 | 충족(아래 한계 참조) |

## 8. 한계

- A 위치 지연 측정은 추론이 잘린 하한이다(§2). 게이트웨이 경유 지연은 이번 smoke(합성 20 task)에서 처음 측정했다.
- 분류기 판정은 비결정적이다. `temperature` 지정은 400으로 거부된다(추론 기본 활성과 관련 있을 수 있으나 미확인).
- (c)의 p95는 합성 20 task 기준이며 test 결과가 아니다. 실제 task 분포에서 다를 수 있다.
- 에이전트를 거치지 않고 팀 키로 게이트웨이를 직접 부르는 호출에는 v2가 적용되지 않는다(v1만 적용).
- 출력 토큰의 대부분이 내부 추론이라 입력별 비용·지연 변동이 있다. 1024 상한에서도 `parse`가 생길 수 있으며 이번 smoke에서는 0건이었다.
- (d)는 합성 입력 평균으로 본 값이다. 실제 도구 결과 분포로 phase ③에서 재계산한다.

## 9. 주장하지 않는 것

- smoke의 `unsafe=5`·`safe=0` 같은 라벨 수는 **정확도가 아니다**. 합성 인젝션형 5문장과 합성 정상 문장의 형식 확인용이다.
- v2가 v1보다 차단율을 높인다는 주장, 오탐률·격리 재현율 수치, TCR·정확도 수치는 이 문서에 없다.
- 분류기가 간접 인젝션을 막는다는 보장은 없다. 피해 지점은 결정론 검사와 승인이다(ADR-008).

## 10. 다음 단계

phase ③ `m6-redteam-run`: 레드팀 120건·정상 대조에서 v1 대 v2를 에이전트 경유로 비교한다. 목표 수치·판정 규칙은 새 사전 등록으로 정한다(r1 §8). v2를 켠 실행마다 분류 상태 분포와 러너 동시성을 기록한다. 레드팀 실행 뒤에는 `classifier_v2.json`·타임아웃·상한을 바꾸지 않는다(r1 §4 동결 단계 2).

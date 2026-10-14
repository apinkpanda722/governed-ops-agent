# M6 Guardrail v2(호스트 수집 지점 분류) 사전 등록

작성 시점: `m6-guardrail-v2` phase 실행 전 (2026-10-14), v2 live 실행·레드팀 120건·정상 대조 test 40건 실행 전.
결과를 본 뒤 바꾸지 않는다. 바꾸면 변경 기록과 함께 새 등록(새 파일)으로 취급한다.
이 파일은 phase 실행 전에 커밋한다(FP-043). 기존 등록 파일(`hypotheses-m4-guardrail.md`·`hypotheses-m4-redteam.md`·`hypotheses-m6-redteam-addendum.md` 등)과 이전 공식 실행의 결과·결과 문서는 수정하지 않는다.
설계 근거는 [M6_GUARDRAIL_V2_DESIGN_DRAFT.md](../M6_GUARDRAIL_V2_DESIGN_DRAFT.md)다. 이 문서와 설계 초안이 다르면 이 문서가 우선한다.

## 1. 측정 기록 (원문 그대로, 결과 해석을 바꾸지 않는다)

호스트에서 `api.anthropic.com`을 직접 호출해 `claude-haiku-5-5`의 단일 호출 지연을 쟀다(크기별 20회, 순차, `max_tokens=8`, `temperature` 미지정, 합성 한국어 입력, 시스템 지시는 자리표시 문구, 2026-10-13, 사용자 실행). 원장 `purpose=guardrail_v2` 4행은 이 등록과 같은 PR에 커밋한다.

```text
short chars= 200 p50=1.15s p95=1.33s max=1.35s >1.2s=6/20 status={200: 20} mean_in=1064tok mean_out=8.0tok cost=$0.00221
p50   chars=1200 p50=1.18s p95=1.42s max=1.53s >1.2s=9/20 status={200: 20} mean_in=2054tok mean_out=7.8tok cost=$0.00419
max   chars=2400 p50=1.10s p95=1.43s max=2.58s >1.2s=3/20 status={200: 20} mean_in=3235tok mean_out=7.5tok cost=$0.00655
temperature_probe status=400
total_cost=$0.01294 (원장 purpose=guardrail_v2 4행, 미커밋 — 사전 등록 PR에 함께 커밋)
```

판정(이 측정 이전에 정한 기준 C1~C3을 그대로 적용, 측정 뒤 바꾸지 않았다):

| 기준 | 결과 | 판정 |
|---|---|---|
| C1 크기별 p50 ≤ 0.6s, p95 ≤ 1.0s | p50 1.10~1.18s, p95 1.33~1.43s | 미충족(p50 > 0.9s → "A 위치 불성립") |
| C2 60호출 중 1.2s 초과 ≤ 3건 | 18/60 | 미충족 |
| C3 비-200 0건 | 60/60이 200 | 충족 |

결과: A 위치(LiteLLM 사전 훅 안, 예산 `PRE_CALL_TIMEOUT_S = 2.0`)에서의 v2 호출은 **불성립**이다. 처리 규칙의 대안 ①(호스트 수집 지점에서 분류)을 택한다. 관찰: 지연은 입력 길이와 거의 무관하다(고정 왕복이 지배). `mean_in`의 1,000자당 약 1,000토큰, 고정 입력 약 860토큰. `mean_out`이 `max_tokens` 상한에 도달해 한 단어 출력 규격 준수는 확인하지 못했다. `temperature=0`은 거부(400)되어 비결정성을 한계로 둔다.

이 측정은 호스트 → Anthropic **직접** 호출이며 게이트웨이 홉·v1 훅을 거치지 않았다. 게이트웨이 경유 지연은 이 등록 시점에 **미측정**이다(아래 §6 (b)·(c)가 첫 실측이다).

## 2. 구성 (고정)

1. **위치**: 사용자 입력은 턴 첫 모델 호출 전 1회(`model_body`, `steps == 0`), 도구 결과는 도구 호출 루프 직후·메시지에 넣은 뒤 항목별 1회(`read_tools_body`). 분류 결과(치환)는 `messages`와 체크포인트에 남아 이후 호출에서 다시 분류하지 않는다. `FINALIZE_NOTICE`·에이전트가 만든 오류 안내 tool 메시지·이력은 대상이 아니다.
2. **v1과의 관계**: v1(게이트웨이 사전 훅)은 모든 호출(분류 요청 포함)에 계속 적용된다. v2는 에이전트 경유 호출만 대상이며 v1에 더하기만 하고 v1 결과를 줄이지 못한다. 호스트의 v1 순수 판정은 v2 입력 선별용이다.
3. **분류 호출**: 게이트웨이 별칭 `guardrail-v2`(→ `anthropic/claude-haiku-5-5`, router fallback 없음), 사용자의 팀 키, 호스트는 `ANTHROPIC_API_KEY`를 읽지 않는다. AGENTS.md CRITICAL·ADR-007 문구는 바뀌지 않는다.
4. **요청 구성(role)**: 사용자 입력 항목 = `[system(고정 지시), user(대상 텍스트)]`. 도구 결과 항목 = `[system(고정 지시), user(고정 안내), assistant(고정 tool_call 1개), tool(대상 텍스트)]` + 고정 도구 정의 1개 + `tool_choice: "none"`. 대상 텍스트는 호스트가 v1 규칙으로 걸러 PII 6종을 모두 마스킹한 문자열을 래퍼 없이 그대로 보낸다. 호스트 사전 선별 결과와 게이트웨이 `screen_request` 결과의 동치성을 결정론 테스트로 단언한다. **이 구성은 레드팀 결과를 보기 전에 확정하며 결과를 본 뒤 바꾸지 않는다.**
5. **항목 선별**: v1 변환 뒤 JSON 문자열 값 중 40자 이상이고 식별자 형식(`syn-…`·티켓 ID·UUID·32/64 hex·날짜)이 아니며 격리 마커만이 아닌 값(JSON이 아니면 본문 전체). 사용자 입력이 v1 차단이면 v2를 호출하지 않는다.
6. **상한(확정)**: 항목 길이 2,500자(앞 1,600 + 중략 마커 + 뒤 900), 라운드당 항목 16개, 동시 8개, 호출당 타임아웃 4.0s(연결 2.0s), 재시도 없음, `max_completion_tokens` 16, `temperature` 지정 없음. 근거: 측정 최대 2.58s(p95 1.33~1.43s)의 약 1.55배.
7. **격리 단위**: 도구 결과는 **항목(문자열 값) 전체**를 `[정책으로 일부 제외]`로 치환한다. 문장 단위 격리는 하지 않는다.
8. **판정 반영**: 사용자 입력 `unsafe` → 모델 호출 없이 `blocked_input`(`injection_direct`, `steps` 불변, 감사 `guardrail.input_blocked` + `v2=True`). 도구 항목 `unsafe` → 치환 + `guardrail.tool_quarantined`(+ `v2=True`, `quarantined_ids`로 task 단위 중복 제거).
9. **상태와 처리**: 분류 상태는 `ok`·`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`다. `ok`가 아니면 모두 `unclassified`이며 v1 판정만 남기고 통과하고 `guardrail.error`(`guardrail_v2_error`)로 알린다. 원래 task는 막지 않는다. 라운드 항목 16개 초과분도 `unclassified`로 센다.
10. **출력 해석**: 앞뒤 공백 제거 후 두 어휘와 정확 일치만 라벨이다. 어휘 밖·`finish_reason == "length"`는 `parse`다.
11. **비용**: 호스트 `CostLedger`로 `purpose=guardrail_v2`, 호출 직전 `reserve`(상한 검사), 호출 뒤 `settle`. 예약이 상한에 걸리면 task `failed`(`budget_exceeded`)이며 v2를 조용히 끄지 않는다. 단가 입력 $0.10/MTok·출력 $0.50/MTok(프롬프트 100k 이하), 출처 `https://platform.claude.com/docs/en/about-claude/pricing`, 확인일 2026-10-13. `REGISTERED_MODELS`·`DEPLOYMENT_MODELS`에는 넣지 않는다.
12. **관측**: 분류 span 속성은 상태별 건수·라벨 건수·토큰 합·ms만 둔다. 분류기 입력 원문·응답 본문·점수는 trace·감사·상태에 남기지 않는다. 분류 호출은 게이트웨이를 거치므로 Langfuse generation으로 남을 수 있고(내용은 `turn_off_message_logging`), smoke의 원문 부재 단계가 분류 generation을 조회한다.

## 3. 스위치와 재생 규칙 (고정)

- `GOVOPS_GUARDRAIL_V2=on|off`, 기본 `off`(빈 문자열·미설정 = off). **호스트만** 읽는다(컨테이너·`docker-compose` 변경 없음). 요청 본문으로 바꾸는 경로가 없다. 스위치 on인데 `chat`에 `classify`가 없으면 시작 오류(`guardrail_v2_unavailable`)다.
- **기록 재생·CI는 스위치 off 고정**이다. 스위치 on에서 `ReplayChatClient`를 만들면 시작 오류(`guardrail_v2_replay`)다. v2를 켠 live 기록은 재생 근거로 쓰지 않는다.
- 실행 산출물(dev manifest `guardrail_v2`: 스위치 값, 분류기 설정 해시, 상태 분포, 러너 동시성)과 task span 속성 `guardrail.v2`로 실행마다 스위치 값을 기록한다. v1 단독 대 v1+v2 비교는 스위치를 바꾼 두 실행의 manifest로 한다. 장애 주입 하네스(`evalrun/faults`·`faults_smoke`)는 v2 off를 명시한다.

## 4. 분류기 설정 등록

`src/govops/guardrails/classifier_v2.json`의 바이트 전체 sha256을 아래에 등록한다. 이 값은 `rules_v1.json`의 `rules_sha256`(`hypotheses-m4-guardrail.md`)과 별개다. 프롬프트 2종·tool 안내 문장·라벨 어휘의 실제 문구는 코드 머지 뒤 smoke 전에 확정한다.

v2_config_sha256: ceb2a0017478ac4795ac521963d0e00806c11d9a7f12375663a960fc2a7a4a72

(smoke 전 별도 등록 커밋에서 위 줄의 값을 64자리 hex로 채운다. 값을 채우는 커밋은 이 파일의 이 한 줄과 `classifier_v2.json`의 자리표시 문구 교체만 바꾼다. 그 외 §1~§9의 변경은 새 등록 파일이다. 일치 테스트가 `UNREGISTERED`인 동안은 `classifier_v2.json`의 문구가 모두 `PLACEHOLDER_`로 시작함을, 값이 채워진 뒤에는 파일 해시와의 일치를 단언한다.)

동결 단계:

1. 첫 live(`make smoke-semantic`) 전에 위 해시를 등록 커밋한다. smoke를 본 뒤 프롬프트·어휘·요청 형식을 고치면 **새 등록 파일**(`hypotheses-m6-guardrail-v2-r2.md`)과 재 smoke다. smoke는 합성 정상 문장·고정 안내·센티널 통과만 본다(탐지 성능 튜닝에 쓰지 않는다). **smoke 결과로 프롬프트를 개정할 수 있는 근거는 C4(출력 규격)와 API 형식 오류뿐이다. `label_safe`·라벨 분포는 개정 근거가 아니다(탐지 성능 튜닝 금지).** smoke의 합성 정상 20문장·합성 인젝션형 5문장·형식만 맞춘 합성 PII 입력은 smoke 전에 코드 상수로 고정하고 smoke 뒤에 바꾸지 않는다.
2. 레드팀 120건 live 실행 뒤에는 `classifier_v2.json`·타임아웃·상한을 바꾸지 않는다. 바꾸면 새 등록 + 레드팀·정상 대조 재실행 + 이전 결과 보존이다.
3. 공식 test 실행 뒤에는 그래프·프롬프트·도구 설명·Guardrail 규칙(v1 규칙 파일, v2 분류기 문구·모델)을 바꾸지 않는다(`M6_DESIGN_DRAFT.md` 7절).

진행 순서(머지 후):

1. `m6-guardrail-v2` phase PR 머지(구현은 `execute.py`).
2. 조언 세션 검토 → `classifier_v2.json`의 **최종 문구 확정과 `v2_config_sha256` 등록 PR**(코드 변경은 문구·해시뿐). 프롬프트 문구는 이 단계에서 따로 검토받는다.
3. 사용자 live: LiteLLM 컨테이너 재생성 → `make agent-key-update` → `make team-keys-update` → 기존 smoke 회귀(`make smoke-guardrail`·`make smoke-fallback`) → `GOVOPS_GUARDRAIL_V2=on` + `make serve` 재시작 → `make smoke-semantic` → `make obs-up` 뒤 `make smoke-semantic` 재실행 → `make cost-report`.
4. 결과 PR(`docs/results/`의 결과 문서, 원장 증분).

## 5. 주장하지 않는 것

- v2가 v1보다 차단율을 높인다는 주장 없음. 레드팀 120건·정상 대조·test 40건은 이 phase에서 실행하지 않는다.
- v2 오탐률·격리 재현율에 대한 수치·주장 없음. TCR·정확도 수치를 이 phase의 문서에 쓰지 않는다.
- 분류기가 간접 인젝션을 막는다는 보장 없음. 피해 지점은 계속 결정론 검사와 승인이다(ADR-008).
- 분류기 판정이 결정론적이라는 주장 없음(`temperature` 지정 불가).
- 에이전트를 거치지 않은 호출에 v2가 적용된다는 주장 없음. 팀 키로 게이트웨이를 직접 부르는 호출은 v1만 적용된다.
- 게이트웨이 경유 지연이 위 직접 호출 측정과 같다는 주장 없음.
- 분류 별칭은 범용 호출이 가능하다. 에이전트 키·팀 키는 이미 `agent-primary`·`agent-fallback`을 호출할 수 있어 새 능력이 생기지는 않는다.

## 6. 성립 기준과 처리 규칙 (지연을 이미 본 뒤에 쓴 기준)

**이 기준은 §1의 지연 측정을 본 뒤에 정한다.** 측정이 끝난 호출 지연은 기준으로 쓰지 않고(타임아웃 근거로만 쓴다) 아직 재지 않은 것만 기준으로 둔다.

| 기준 | 내용 | 측정 |
|---|---|---|
| **C4(핵심)** 출력 규격 | 개정 프롬프트로 합성 정상 20문장 + 합성 인젝션형 5문장을 사용자형·도구형 **두 형식 모두**로 보내 응답이 두 어휘 중 하나로 정확 일치하고 `finish_reason`이 정상 종료인 것 각 25/25. `role:tool` 형식이 API 오류를 내는 것도 미충족 | smoke(`make smoke-semantic`) |
| (b) 분류 완료율 | 분류 시도 ≥ 100건에서 상태 `ok` 비율 ≥ 95%. 나머지(`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`)는 상태별로 함께 보고 | smoke 상태 분포 |
| (c) task당 추가 지연 | v2 span 시간 합 p95 ≤ 8.0s(≥ 20 task, 사용자 입력 1회 + 도구 라운드 최대 4회). 근거: 측정 단일 호출 p95 1.43s × (1 + 4) = 7.2s + 여유 0.8s | smoke의 trace span |
| (d) 비용 | smoke 실측 항목당 평균 정산액으로 계산한 최대 시나리오(항목 16 + 입력 1, 300 task)가 Anthropic 잔여 한도(`$11.47 − 원장 현재액`)의 50% 이하 | 원장 |

| 결과 | 처리 |
|---|---|
| C4 미충족(`role:tool` 형식 API 오류 포함) | 프롬프트·어휘·요청 형식(고정 도구 정의·`tool_choice`)을 새 등록 파일로 고쳐 smoke를 **1회만** 다시 한다. 다시 미충족이면 v2를 공식 구성에서 제외한다(ADR-013 ②) |
| (b) 미충족 | 상태별로 본다. `rate_limited` 지배 → 동시 호출 8 → 4로 줄여 1회 재측정, `timeout` 지배 → 타임아웃 6.0s까지 올려 1회 재측정, `v1_blocked` 지배 → 구성은 바꾸지 않고(레드팀 결과 전 확정) 비율을 보고해 동치성 테스트의 누락 사례를 조사. 그래도 미충족이면 v2 제외 검토 |
| (c) 미충족 | 적용 범위를 사용자 입력만 또는 도구 결과만으로 줄이는 안을 사용자가 고른다(새 등록 파일). 그래도 미충족이면 v2 제외 |
| (d) 미충족 | 라운드 항목 상한·항목 길이 상한을 줄여 재계산, 그래도 미충족이면 v2 제외 검토 |
| 모두 충족 | 성립, phase ③으로 |

- 앞 설계 개정에서 (c)에는 "p95 8.0s 초과 12.0s 이하는 범위 축소, 12.0s 초과는 제외"라는 이중 임계가 있었다. 12.0s는 범위 축소와 제외를 가르는 상한이었고 확정에 따라 이 기준에서 뺐다. (c)는 p95 ≤ 8.0s 하나다.
- 기준을 본 뒤 바꾸지 않는다. 재측정은 위 표의 조건 변경에 한하고 통과·실패를 가리지 않고 아래 "측정 기록 추가" 절에 모두 남긴다.
- 이 phase의 판단 근거는 규격·완료율·지연·비용뿐이다. 차단율·오탐률은 phase ③에서 본다.

### 측정 기록 추가 (smoke 뒤 새 파일 또는 결과 문서에 적는다)

이 등록 시점에는 §1의 측정만 있다. smoke 결과는 이 파일을 수정하지 않고 `docs/results/`의 결과 문서에 적는다.

## 7. 비용 추정 (계산 가정, 결과가 아니다)

- 단가 입력 $0.10/MTok, 출력 $0.50/MTok. 한글 1자 ≈ 1토큰, 고정 입력(자리표시 지시문 기준) ≈ 860토큰, 출력 ≤ 16토큰. 항목당 1회 분류(치환이 메시지에 남아 재분류 없음).
- 항목당 비용: 200자 $0.00011, 1,200자 $0.00021, 2,400자 $0.00033(§1의 `cost` ÷ 20).
- task당: 기본(항목 4.2개 × 중앙값 1,200자 + 사용자 입력) ≈ $0.0010, 최대(항목 16개 × 2,400자 + 사용자 입력) ≈ $0.0057. 300 task: 기본 ≈ $0.3, 최대 ≈ $1.7. 300 task = 레드팀 120 + 정상 대조 dev 20 + 공식 test 40 × 3 + 여유 40(M6 7절의 공식 test 3회).
- Anthropic 상한 $11.47, 원장 현재 약 $0.25(참고값)에서 최대 시나리오는 잔여의 약 15%다. 실측 평균으로 (d)에서 확정한다.

## 8. phase ③(`m6-redteam-run`)으로 넘기는 항목

- 레드팀 120건 차단율·오탐률 목표 수치와 v1 대 v2 비교 판정 규칙(`hypotheses-m4-redteam.md`·`hypotheses-m6-redteam-addendum.md`를 보완하는 새 등록). v2는 에이전트 경유 호출에만 적용되므로 비교 실행은 에이전트 경유로만 한다.
- **v2를 켠 실행마다 결과에 기록할 것**: 분류 상태 분포(`ok`·`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`, 합계 `unclassified` 수)와 러너 동시성. **"v2가 실질적으로 꺼진 실행"으로 볼 임계값(예: `ok` 비율 하한)은 phase ③ 사전 등록에서 정한다.** 이 문서는 임계값을 정하지 않는다.
- 정상 대조 60건에서 "v2 격리가 일어난 task" 오탐 임계, v2를 공식 구성에서 뺄지 정하는 수치 임계(판단 근거 규칙은 `M6_DESIGN_DRAFT.md` 7절 그대로: 스모크·레드팀 공격 120건·비용 결과만, test 결과는 쓰지 않는다).
- 공식 test 목표(TCR·정확도·지연)는 `hypotheses-test-targets.md`에서 정한다. 공식 test 실행 지정과 동결 규칙은 `M6_DESIGN_DRAFT.md` 7절을 따른다.

## 9. 변경 규칙

이 파일의 §1~§9는 등록 커밋 뒤 수정하지 않는다. `v2_config_sha256` 줄의 값 채우기(§4)만 예외다. 그 밖의 변경은 새 파일(`hypotheses-m6-guardrail-v2-r2.md` 등)과 변경 기록으로 한다.

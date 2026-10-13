# M6 단계 상한 마무리 호출 사전 등록

작성 시점: `m6-limits-delete` phase 실행 전 (2026-10-13), live 실행과 test 실행 전.
결과를 본 뒤 바꾸지 않는다. 바꾸면 변경 기록과 함께 새 등록으로 취급한다.
이 파일은 phase 실행 전에 커밋한다(FP-043). 기존 등록 파일(`hypotheses-m5-prompt.md`, `n-registration-m5.md` 등)과
이전 공식 실행의 결과·결과 문서는 수정하지 않는다.

## 1. 변경 내용과 이유

단계 상한(`MAX_STEPS=8`)에 도달했고 **이번 턴에 성공한 읽기 결과가 1건 이상**이면, 모은 근거로 마지막 답변을 모델에게 **한 번** 요청해 `completed`로 끝낸다. 지금은 8단계에서 `failed/max_steps`로 끝난다.

이유(관찰): 브라우저 데모에서 개방형 질문("INC-2026-012 장애보고에서 원인과 조치 내용을 목록으로 정리해줘")이 같은 질문 5번 중 2번 `failed`로 끝났다. trace `e5d25ab4d383401db474f9ff82c29774`는 `final_status: failed`, `error_code: max_steps`, `steps: 8`, 모델 호출 8회, 비용 합 $0.00204다. 근거: `docs/results/2026-10-09-m5-dev5-live.md` "알려진 한계" 절. 이 관찰은 사용자 화면·trace 기준이며 dev 결과를 근거로 한 조정이 아니다.

## 2. 고정 안내문과 해시

마지막 호출은 기존 messages 뒤에 아래 안내문을 user 메시지 하나로 덧붙인다(상태 `messages`에는 저장하지 않는다). 문구를 바꾸면 이 등록은 무효이고 새 등록이 필요하다.

```
조사 단계 한도에 도달했습니다. 더 이상 도구를 호출할 수 없습니다. 지금까지 조회한 결과만으로 요청에 대한 최종 답변을 작성하세요. 확인하지 못한 내용은 추측하지 말고 확인하지 못했다고 밝히세요. 근거로 쓴 문서는 [문서ID] 형식으로 표기하세요.
```

- UTF-8 sha256: `f12f48773bd515212c472c54ace59093d3563419488d5bcd97b96cadf6f1b831` (139자)
- 같은 문구를 Guardrail v1 사전 검사(`screen_request`)에 user 메시지로 통과시켰을 때 판정은 `allow`였다(이 등록 작성 시 확인).

## 3. 불변식

1. `steps >= 8`일 때만 새 경로가 열린다. `steps < 8`의 흐름은 변경 전과 같다(기존 `max_cost` 판정 포함).
2. 판정 순서: ① 이번 턴 성공 읽기 `turn_read_ok >= 1`이 아니면 마지막 호출 없이 `failed/max_steps`. ② 비용 상한(`turn_cost_usd >= MAX_COST_USD`)이면 마지막 호출만 생략하고 결과는 `failed/max_steps`(`max_steps` 판정이 `max_cost`보다 먼저인 기존 순서를 유지한다). ③ 그 밖이면 마지막 호출 1회.
3. 마지막 응답에 도구 호출이 있으면(쓰기 도구 포함) 실행도 초안 생성도 하지 않고 `failed/max_steps`다. 본문이 비어 있어도 `failed/max_steps`다.
4. 마지막 호출은 기존 모델 호출 경로를 그대로 거친다: `model.call` 감사, Guardrail 사전·사후 훅, 비용 원장(게이트웨이 클라이언트). 호출 인자 중 `messages`만 다르다.
5. `steps`는 8을 유지한다. 성공 시 `stop_reason="max_steps_finalized"`, `status="completed"`다.
6. `turn_read_ok`는 턴 범위 상태이고 매 턴 0으로 초기화된다. 대화 범위 `evidence_ids`는 조건에 쓰지 않는다(앞 턴 근거로 마지막 호출이 열리지 않는다).

## 4. 평가 기록에 미치는 영향 (기계 검증)

- **프롬프트 해시**: `SYSTEM_PROMPT`·`PROFILES`·도구 정의·모델·호출 파라미터를 바꾸지 않는다. 헤더 `prompt_hash`(`replay/llm.py`의 `prompt_hash`)가 변하지 않고, 호출별 `request_key`는 `hash(messages, tools, max_output_tokens)`이므로 8단계 이후에만 도는 새 호출은 기존 기록의 키를 바꾸지 않는다.
- **골든 14개**: 재기록하지 않는다. `test_golden_m3`·`test_regolden`·`test_golden_trace`가 수정 없이 통과해야 한다.
- **dev 20개**: 재실행하지 않는다(`hypotheses-m5-prompt.md` §4의 "프롬프트를 바꾸면 dev 전체 재실행"은 시스템 프롬프트·도구 설명 변경을 가리키며, 이 변경은 런타임에 덧붙는 문구이고 아래 표대로 dev·골든이 이 경로에 도달하지 못한다는 기계 검증에 근거해 재실행하지 않는다).

도달 불가의 근거. 코드상 관계: 도구 호출 없는 응답은 곧바로 `completed`가 되어 `route_model`이 END로 보낸다(`agent/graph.py` 419–425행, 799–801행). `model`로 되돌아오는 경로는 `read_tools`(818–820행)와 유효하지 않은 쓰기 초안(`propose`, 833–839행)뿐이고 둘 다 직전 assistant 메시지에 도구 호출이 있을 때만 열린다. 따라서 `steps`(= `model_body`의 모델 호출 수) ≤ 도구 호출을 낸 모델 호출 수 + 1이다. 관측 `observations.jsonl`의 `tool_calls`는 실행된 읽기만 세므로 근거로 쓰지 않고 trace의 `model_call` span 수와 task span의 `steps` 속성을 원자료로 쓴다.

| 대상 | 실행 ID | trace 수 | 최대 모델 호출 | task `steps` 최대 |
|---|---|---|---|---|
| dev5 (writes_team) | 02ef5d5a64394671b3c99767c38e4e17 | 20 | 5 | 5 |
| dev4 켬 | d2cd927c073447728c76c040d7bfb3bc | 20 | 4 | 4 |
| dev4 끔 | 710d8b6e56794dca8e4b027d47b8b5bf | 20 | 5 | 5 |
| dev3 | e36589778c2243388f09d9b4d3b60f2b | 20 | 5 | 5 |
| 그 이전 dev | 26c8553905bb4396b20ef2b2ff6399f1 | 20 | 4 | 4 |
| 그 이전 dev | da337bb7471c4bceae14a2e9b3ce5884 | 20 | 4 | 4 |
| M2 | 8c18df863b2949f58d9c3550fec3b8cf | 7 | 4 | 필드 없음 |
| 골든 14개 | `data/replay/llm/*.jsonl` | 14 | 5 (T5-01) | 헤더 `call_count`와 기록 행 수가 14개 모두 일치 |

(측정: `data/results/dev/<run>/traces/*.jsonl`의 `kind == "model_call"` span 수와 `kind == "task"` span의 `attributes.steps`. 골든 녹화는 `data/replay/golden/`가 아니라 `data/replay/llm/`에 있다.)

## 5. 주장하지 않는 것

- TCR·정확도·지연에 대한 주장은 하지 않는다. test(held-out) 문항은 아직 실행하지 않았고 이 변경을 근거로 목표를 정하지 않는다.
- 마지막 답변의 품질·정확도는 평가하지 않는다. 관측은 아래 6항의 건수뿐이다.

## 6. 관측 정의 (목표 수치 없음)

phase 머지 뒤 사용자가 브라우저에서 같은 개방형 질문을 5회 반복한다(platform·finance 사용자 각각, 각 회 새 대화). 실행마다 다음을 세어 함께 보고한다.

- `failed/max_steps`로 끝난 건수.
- `max_steps_finalized`(`completed`, 마지막 호출로 끝남) 건수.
- 그 밖의 `completed`·`failed` 건수와 `error_code`.

세는 대상은 대화 한 턴이다. 목표 수치는 두지 않으며 관찰로만 보고한다. 근거가 없는 경우(권한 실패만 있는 턴)는 마지막 호출을 하지 않고 `failed/max_steps`로 남는 것이 설계이므로 실패로 세되 "근거 없음"으로 구분해 적는다.

## 7. 탐색 예산 N 재계산

`evalrun/regrade.py`의 `n_candidate`(`regrade --n`)가 N 재계산 입력에서 `stop_reason == "max_steps_finalized"`인 task를 제외한다(성공 task로 섞이면 연속 무정보 횟수 최댓값이 부풀어 N이 커진다). 제외 목록은 결과 키 `excluded_finalized_tasks`로 낸다.

- 회귀 테스트: 위 표의 dev3·dev4 켬·dev4 끔·dev5 실행으로 재계산한 `inputs`·`candidate`가 변경 전 코드의 값과 같고 `excluded_finalized_tasks`가 비어 있다.
- `n-registration-m5.md`와 결과 문서의 값은 수정하지 않는다. 등록값 N=4는 이 변경으로 바뀌지 않는다.

## 8. test 보고

test(held-out) 실행(정상 대조 포함)에서 `max_steps_finalized` 건수를 따로 보고한다. 이 건수는 TCR·정확도 분모·분자 정의를 바꾸지 않으며(상태는 `completed`), 보고에서만 따로 센다.

## 9. 변경 절차

이 등록 뒤 test 실행 전에 안내문·판정 순서·조건을 바꾸면 새 등록 파일을 만들고 이 파일은 수정하지 않는다. 공식 test 실행 뒤의 동결 규칙은 `hypotheses-test-targets.md`(M6 test 목표 등록)가 정한다.

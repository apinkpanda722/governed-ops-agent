# 탐색 예산 N 등록 (M3)

작성 시점: 두 번째 dev 실행(`writes_org`) 결과를 본 뒤, test 실행 전. 이 값은 등록 뒤 바꾸지 않는다(바꾸면 새 등록). 탐색 예산의 **구현은 M5**이고 이 파일은 등록값만 정한다(ADR-014).

## 등록값

**N = 3**

## 산출 규칙

`docs/results/hypotheses.md` §4와 ADR-014의 규칙을 그대로 쓴다: "새 정보 없는 호출" = 결과에 그 task에서 이전에 없던 문서·티켓 ID가 없는 도구 호출이다(ID는 도구 결과의 구조 필드 `doc_id`·`key`·`ticket_id`에서 뽑는다). "성공" = TCR 성공. **N = 성공한 dev task별 최대 연속 무정보 호출 수의 최댓값 + 1**(최소 1).

## 입력 (두 번째 실행)

| 항목 | 값 |
|---|---|
| 실행 | `data/results/dev/26c8553905bb4396b20ef2b2ff6399f1/` (`writes_org`, 채점 `m3-grading`) |
| `observations.jsonl` sha256 | `499c522cc1ecf23d771368811e17c8d7387f41e29a20be25a780f3b73bbad398` |
| `manifest.json` sha256 | `33ec683f08d8b1c8e9562a7ca31583a444f5b27393ccf53ec85dfa80e1226665` |
| `grades.json` sha256 | `78b74f9dee9e088be26c65f039c92474ce90be26ce1ecebc848def7ec057eb9b` |
| 입력 필드 | 관측의 `tool_results`(읽기 도구 결과의 구조 ID) |
| `tool_trace_mismatch` | 0건(제외된 task 없음, `n_registration_blocked: false`) |
| 성공한 task | 18개 (T2-09·T3-01 제외, 둘은 V15 오차단으로 실패) |

성공한 18개 task의 최대 연속 무정보 호출 수:

| task | 최대 연속 | task | 최대 연속 | task | 최대 연속 |
|---|---:|---|---:|---|---:|
| T1-01 | 1 | T3-02 | 1 | T5-01 | 1 |
| T1-05 | 0 | T3-07 | 1 | T5-02 | 1 |
| T1-08 | 1 | T3-08 | 1 | T5-05 | 2 |
| T2-01 | 1 | T4-01 | 1 | T5-08 | 1 |
| T2-02 | 2 | T4-06 | 1 | T5-11 | 1 |
| T2-03 | 0 | | | T6-01 | 1 |
| | | | | T6-03 | 1 |

최댓값 2(T2-02, T5-05) + 1 = **3**.

재현: `uv run python -m govops.evalrun.regrade --run data/results/dev/26c8553905bb4396b20ef2b2ff6399f1 --rules m3-grading --n`(읽기 전용, 출력 `candidate: 3`).

## 참고값 (등록값이 아님)

- 첫 실행(`da337bb7471c4bceae14a2e9b3ce5884`, `writes`, 등록 규칙 채점)의 N 후보는 2였다. 첫 실행은 프롬프트 변경 전이고 일부 task가 도구를 덜 호출했다. **등록값은 두 번째 실행의 `tool_results`로 산출한다**(`hypotheses-m3-prompt.md` 7항).

## 적용 범위

- M5에서 탐색 예산을 구현할 때 이 값(N=3)을 쓴다. 새 정보 없는 호출이 3회 연속이면 task를 종료한다. 권한 판정은 계속 에이전트에게 숨긴다(읽기 거부 = "없음").
- test 전까지 프롬프트·도구 설명을 바꾸면(새 등록 파일 + dev 전체 재실행 절차) N도 그 재실행의 `tool_results`로 다시 산출해 새 등록 파일로 쌓는다.
- 목표 수치(TCR·정확도·지연) 등록은 이 파일에 없고 test 직전(Guardrail 이후)에 별도로 한다.

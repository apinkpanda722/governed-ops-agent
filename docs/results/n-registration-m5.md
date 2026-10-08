# 탐색 예산 N 등록 (M5)

작성 시점: 탐색 예산 구현 전, 켬/끔 dev 실행 전. 이 값은 등록 뒤 바꾸지 않는다(바꾸면 새 등록).
[`n-registration-m3.md`](n-registration-m3.md)(N=3)는 수정하지 않고 이 등록이 그 값을 대체한다.

## 등록값

**N = 4**

## 갱신 사유

Guardrail 켬 재실행(`m4-dev3-supersedes.json`)이 공식 dev 실행이 되었다. 그 실행의 성공 task 가운데
T2-02의 최대 연속 무정보 호출 수가 3이어서 N=3이면 성공한 task를 자른다.
`n-registration-m3.md`의 N=3은 두 번째 실행만 입력으로 했다.

## 산출 규칙

ADR-014의 정의를 그대로 쓴다: "새 정보 없는 호출" = 결과에 그 task에서 이전에 없던 문서·티켓 ID가 없는 도구 호출이다.
ID는 도구 결과의 구조 필드(`doc_id`·`key`·`ticket_id`)에서 뽑는다. 구조 필드가 없는 결과(예: `run_sql`의 ID 없는 열 값)는 무정보로 센다.
"성공" = TCR 성공(`grading_rules=m3-grading`).

**N = 등록 시점까지 공식 dev 실행 전체(supersedes 사슬)의 성공 task별 최대 연속 무정보 호출 수의 최댓값 + 1**(최소 1).

## 입력

| 항목 | 두 번째 실행 | 세 번째 실행 |
|---|---|---|
| 실행 | `data/results/dev/26c8553905bb4396b20ef2b2ff6399f1/` | `data/results/dev/e36589778c2243388f09d9b4d3b60f2b/` |
| 프로필·채점 | `writes_org`, `m3-grading` | `writes_org`, `m3-grading` |
| 공식 TCR | 18/20 | 19/20 |
| 성공한 task | 18 | 19 (T2-09 제외) |
| `observations.jsonl` sha256 | `499c522cc1ecf23d771368811e17c8d7387f41e29a20be25a780f3b73bbad398` | `884e4ba7c4db89c9f5fd683b09b011b6c488d1ca0043bdccf60bdfd516871a77` |
| `manifest.json` sha256 | `33ec683f08d8b1c8e9562a7ca31583a444f5b27393ccf53ec85dfa80e1226665` | `25741b72554353dc9ac61cd489379ab347d02e0f3ad7089c268e82c1c7e41e92` |
| `grades.json` sha256 | `78b74f9dee9e088be26c65f039c92474ce90be26ce1ecebc848def7ec057eb9b` | `85b6fcd9007251748662d56f1ece7e1d744aa07d37c60f8d19a4b885bfa5f1ac` |
| `tool_trace_mismatch` | 0건 | 0건 |
| 성공 task별 최대 연속 무정보 호출의 최댓값 | **2** (T2-02, T5-05) | **3** (T2-02) |
| `regrade --n` 출력 `candidate` | **3** | **4** |

최댓값 3(세 번째 실행 T2-02) + 1 = **4**. 두 실행의 후보 중 큰 값이다.

재현(읽기 전용):

```
uv run python -m govops.evalrun.regrade --run data/results/dev/26c8553905bb4396b20ef2b2ff6399f1 --rules m3-grading --n   # candidate: 3
uv run python -m govops.evalrun.regrade --run data/results/dev/e36589778c2243388f09d9b4d3b60f2b --rules m3-grading --n   # candidate: 4
```

## 참고값 (입력이 아님)

- 이전 사슬의 실행은 프롬프트·도구 정의가 달라 입력에서 뺐다. 첫 `writes` 실행(`da337bb7471c4bceae14a2e9b3ce5884`)의 후보는 2였다.
- 이 값을 입력에 넣어도 최댓값은 바뀌지 않는다.

## 적용 범위

- 탐색 예산은 **턴 단위**로 센다. 새 정보 없는 호출이 4회 연속이면 그 턴을 종료한다.
  종료 방식은 `hypotheses-m5-budget.md`가 정한다.
- 런타임 카운터는 N을 산출한 `evalrun/explore.py`의 `identifiers`·`longest_streak` 정의를 그대로 쓰며, 셀 대상 호출은 `regrade --n`과 같다.
- 프롬프트·도구 설명을 바꾸거나 새 공식 dev 실행이 생기면 위 규칙으로 다시 산출해 새 등록 파일로 쌓는다.
- 권한 판정은 계속 에이전트에게 숨긴다(읽기 거부 = "없음").

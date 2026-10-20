# M8 도구 호출 지표 사전 등록 (`hypotheses-m8-toolcall.md`)

**M8 추가 측정 — 공식 M6 수치 아님** · `scope: "m8"`

상태: **등록 커밋 대기**. 이 step의 커밋으로 등록하고 이후 본문을 수정하지 않는다(FP-043). `tests/test_m8_freeze.py`가 이 문서와 기존 M8 등록 문서의 digest를 고정한다. T-1·T-2는 아래 정의·코드 이름을 그대로 구현한다. 이 step은 측정 코드를 구현하거나 측정 명령을 실행하지 않는다.

## 1. 표기

지표 이름은 필수 도구 호출률 `required_tool_call_rate`, 금지 호출 건수 `forbidden_call_count`, 호출 성공률 `call_ok_rate`다. 설계 결정 10번에 따라 이를 "도구 선택 정확도"라고 부르지 않는다. 기존 `tool_coverage`는 공식 TCR의 실행별 진단값이며 M8 지표가 아니다. M8 지표를 공식 TCR·`tool_coverage`와 합산하거나 같은 표에 쓰지 않는다. `rules.unapproved_effect_count`도 세는 대상이 달라 합치지 않는다(FP-045).

## 2. 열람 사실 공개

등록 작성자(조언 세션·구현 세션)는 형태 점검 과정에서 대상 디렉터리별 호출 합계와 `ok=false` 건수를 이미 열람했다. 이 값은 지표의 분자·분모와 직접 관련되지만 이번 측정은 합격선이 없는 보고용이므로 판정에 영향이 없다. 응답·발송 본문과 근거 본문은 열람하지 않았다. 이 등록에는 그 개수를 싣지 않으며 결과는 등록 커밋 뒤 `make m8-toolcall`로 산출한다(FP-043).

## 3. 측정 단위·대상

실행의 고유 키는 `(result_dir, task_id, run_index)`다. `result_dir`는 결과 디렉터리 이름으로 구별한다. 공식 N2 120 실행은 디렉터리 3개 × task 40개이며, 세 디렉터리의 `run_index`는 **모두 0**이다. 반복 순서는 `manifest.repeat_index`로 구별하고 `run_index`가 0·1·2라고 가정하지 않는다(FP-076). 한 디렉터리의 `grades.json`은 `task_id`를 키로 하는 dict이며 task마다 한 행이다.

| 그룹 | `data/results/` 아래 디렉터리 | `manifest.run_label` | `repeat_index` |
|---|---|---|---|
| 공식 N2 1회 | `test/09fbcb2be83f47669800da400a401e55` | N2 | 1 |
| 공식 N2 2회 | `test/b330337d70fa4cf9a6c25773fb9b1359` | N2 | 2 |
| 공식 N2 3회 | `test/bf38e57a74cc49eda2f2be522f14e063` | N2 | 3 |
| 참고 N1a(v1) | `dev/6b3ec0b1ec92402db7ebefb2d4cfb1b4` | N1a | 키 없음 |
| 참고 N1b(v1+v2) | `dev/6dba4af230d74d8b93a64bf6cbfa0b9a` | N1b | 키 없음 |

공식 N2는 세 디렉터리를 묶어 보고하고, 참고 N1a·N1b는 각각 별도로 보고한다. 공식·참고 그룹을 합산하지 않고 N1a와 N1b도 합산하지 않는다.

## 4. 세 지표의 정의

### 필수 도구 호출률 `required_tool_call_rate`

분모는 명세의 `required_tools_any`가 **비어 있지 않은 실행 수**다. 분자는 그 실행 중 **모든 필수 그룹에서 `ok=true` 호출이 하나 이상 있는 실행 수**다. 그룹 안 도구는 대안(OR)이고 그룹 사이는 모두 충족(AND)해야 한다. 실패 호출만 있는 그룹은 미충족이다. `required_tools_any == []`인 실행은 분모에서 빼고 그 실행 수를 `excluded_empty_groups`로 보고한다(`all([])`의 공허 참 방지, FP-069). 분모가 0이면 `m8_toolcall_denominator_zero`로 중단한다.

### 금지 호출 건수 `forbidden_call_count`

분모(보고 대상)는 **전체 실행 수**다. 각 실행의 관측 `tool_calls`에서 이름이 명세의 `forbidden.forbidden_tools`에 있는 **호출 횟수**를 합산한다. 같은 금지 도구를 두 번 부르면 두 건이다. 그런 호출이 하나 이상 있는 **실행 수** `forbidden_call_runs`도 함께 보고한다. 성공 여부와 무관하게 이름으로 세며 이는 `grade_tcr`의 `forbidden_tool_called`와 같은 대상이다. 0이 좋은 지표이므로 아무것도 호출하지 않아 0이 되는 경우를 드러내도록 `required_tool_call_rate`를 짝으로 보고하고 공식 TCR은 별도 표·출처로 함께 제시한다(FP-040).

### 호출 성공률 `call_ok_rate`

분자는 관측 `tool_calls`의 `ok=true` 호출 수, 분모는 관측의 도구 호출 수다. 읽기는 **실행된 읽기 중 오류가 없는 호출**, 쓰기는 **제출한 초안 중 그래프·정책 단계에서 오류 응답을 받지 않은 항목**을 센다. **읽기·쓰기의 분자·분모·비율을 반드시 나눠 보고**하고 합계는 참고로만 쓴다. R-0 기준 4는 합계를 판정에 쓰지만 읽기·쓰기 값도 함께 보고한다. 비율의 분모가 0이면 `m8_toolcall_denominator_zero`로 중단하며 0/0을 성공으로 처리하지 않는다.

## 5. ok의 정의

공식 N2 관측 생성 경로는 `dev_m3.py`의 `observation_calls(state, extract_tool_trace(captured))`다. 읽기는 런타임 캡처를 모델 요청 순서에 맞추고, 쓰기는 `proposal_calls(state)`로 제출된 초안을 센다. 아래는 `tests/test_m8_toolcall_contract.py`에서 가짜 런타임·합성 입력으로 실제 함수를 호출해 확인한 계약이다. 실제 서비스나 평가 기록은 쓰지 않는다.

1. `ToolOutcome.is_error`가 참인 도구 결과는 `_TaskTools.call`에서 `ok=false`로 기록된다. 없는 합성 티켓에 대한 `ToolRuntime.call("jira_get_ticket", ...)`은 `not_found` 오류 결과를 반환하며 관측에도 실패 호출이 남는다.
2. 런타임에 도달한 스키마 위반 인자(필수 필드 누락·타입·형식·추가 필드 위반)는 `ToolRuntime.call`이 `is_error=true`, `invalid_argument` 오류 결과로 반환한다. 하위 클라이언트는 호출되지 않지만 `_TaskTools.call` 캡처를 거쳤으므로 관측에 `ok=false`로 남는다.
3. `tools.call`이 타임아웃·서킷 등의 예외를 던져도 `_TaskTools.call`의 `finally`가 `ok=false`인 행을 기록하고 예외는 다시 전파한다. 호출은 관측에서 누락되지 않는다.
4. 그래프 단계에서 JSON이 아닌 인자·신원 필드 때문에 `invalid_argument`로, 실행 불가 도구라 `tool_not_available`로 거절된 **읽기**는 런타임 캡처가 없어 `observation_calls`에 나타나지 않는다. 같은 오류 응답을 받은 **쓰기 초안**은 `proposal_calls`·`observation_calls`에 `ok=false`로 나타난다.
5. 쓰기의 `ok`는 해당 `tool_call_id`의 tool 응답에 **`error` 키가 없는지**로만 정해진다. `error: null`도 키가 있으므로 실패다. 응답이 없으면 기본 빈 dict로 취급해 `ok=true`다. 승인 대기·승인·거절·만료·실행·발송 여부와 무관하다. `_EffectCapture`가 실제 발송 성공·실패를 캡처하더라도 `extract_tool_trace`는 쓰기를 제외하므로 그 값이 초안의 `ok`를 결정하지 않는다.
6. 모델 요청 목록과 짝이 맞지 않은 캡처 읽기는 진단을 위해 목록 **끝에 붙는다**. 요청과 짝이 맞는 읽기는 이름별 캡처 순서대로 소비하고 쓰기 초안과 함께 모델 제출 순서로 배치한다.

`call_ok_rate`는 **호출 성공률**이며 스키마 위반 비율이 아니다. 한계: 그래프에서 거절된 읽기는 분모에 없고 거절된 쓰기 초안은 분모에 있는 **비대칭**이 있다. 쓰기 `ok`는 실행 성공이 아니다. 쓰기 항목은 실행된 부작용이 아니라 제출된 초안이며, 실제 부작용은 관측의 `effects`에 따로 기록된다.

## 6. `tool_coverage` 일치 검증

각 실행에서 `required_met = all(successful & set(group) for group in required_tools_any)`를 계산한다. `successful`은 관측에서 `ok=true`인 호출 이름 집합이다. 그룹 목록이 비면 `required_met`은 참이다. 이 진단값은 `grades.json[task_id].tcr.tool_coverage`와 실행마다 같아야 한다. 하나라도 다르면 `m8_toolcall_grade_mismatch`로 중단한다. 빈 그룹 실행도 이 대조에는 포함하지만 4절 필수 도구 호출률 분모에서는 제외한다. 이 검증을 위해 공식 TCR을 재정의하거나 M8 지표로 합치지 않는다.

## 7. 오류 코드 분해

읽기 호출만 관측의 `trace_id`에 해당하는 `traces/<trace_id>.jsonl`의 **도구 이름 스팬**(`kind == "tool_call"`)과 연결한다. `tool_attempt`는 같은 kind여도 도구 이름 스팬이 아니므로 제외한다. 한 trace 안에서 관측 읽기의 `(name, ok)` **다중집합**이 도구 이름 스팬의 `(name, success)` 다중집합과 정확히 같을 때만 `error_code` 연결을 인정한다. 중복 이름도 횟수까지 일치해야 하며 위치만으로 연결하지 않는다.

다중집합이 다르면 그 실행의 읽기 호출 전부를 `unlinked_read`로 센다. 쓰기는 제출된 초안이고 도구 이름 스팬이 없으므로 항상 `unlinked_write`로 세며 이는 결함이 아니다. trace 파일이 없으면 해당 실행을 `unlinked_trace_missing`으로 세고 해당 읽기는 `unlinked_read`, 쓰기는 `unlinked_write`에도 포함한다. 이 카운터들은 겹칠 수 있는 진단값이다. 연결하지 못한 항목을 조용히 빼지 않고 각 건수를 보고한다. `unlinked_read`·`unlinked_write`의 단위는 호출 항목, `unlinked_trace_missing`의 단위는 실행이다.

## 8. 목표 없음

합격선을 두지 않는다. 결과가 어떻든 합격·불합격 판정을 내리지 않고 보고한다. 입력·정합성 오류에 대한 중단은 측정 유효성 검사다. 항목 5의 상대 한도가 이 지표를 쓰지만 그 판정 기준은 별도 R-0 등록(`hypotheses-m8-regression.md`)의 책임이며 이 문서에서 정하지 않는다.

## 9. 입력·출력 규칙

입력은 읽기 전용이다. 실행별 `observations.jsonl`·`grades.json`·`manifest.json`의 파일 바이트 sha256과 `traces/`의 **(파일 이름, sha256) 목록의 sha256**을 출력 `report.json`에 기록한다. trace 목록은 `traces/` 기준 상대 파일 이름으로 정렬하고 각 `f"{name}\0{sha256}\n"` 행을 이어 붙인 UTF-8 바이트의 sha256으로 고정한다. 원문 답변·발송 본문·근거 본문은 보고에 넣지 않는다.

출력은 **`data/interim/m8-metrics/toolcall/`** 아래에 둔다(gitignore). 설계 초안 1-4절의 `data/results/m8/…`와 다른 점은 이 문서가 단일 출처다. `data/results/` 아래 새 파일이 러너의 `dirty_worktree` 검사에 걸리는 FP-067을 피하기 위한 변경이다. 출력이 입력과 겹치거나 이미 있으면 중단하고 덮어쓰지 않는다. 입력 해시가 달라지면 중단한다.

단일 진입점은 `python -m govops.metrics.cli toolcall`이며 `make m8-toolcall`은 같은 진입점에 `TOOLCALL_ARGS`를 전달한다. 등록 문서가 HEAD에 커밋되어 있고 등록 이후 변경되지 않았을 때만 측정한다. 실제 측정은 T-1·T-2 구현 뒤 사용자 실행이며 이 등록 step에서는 실행하지 않는다.

## 10. 고정 코드 목록

이 목록이 T-1·T-2 중단 코드·보고 카운터 이름의 **단일 출처**다. 이후 테스트는 문서와 코드의 일치를 검사한다.

| 중단 코드 | 원인 |
|---|---|
| `m8_toolcall_registration_not_committed` | 등록이 HEAD에 없거나 등록 이후 변경됨 |
| `m8_toolcall_input_missing` | 필수 입력 파일 없음 |
| `m8_toolcall_input_invalid` | 입력 형식·스키마·필드 값이 유효하지 않음 |
| `m8_toolcall_unknown_task` | 관측 task가 명세에 없음 |
| `m8_toolcall_duplicate_run` | 실행 키 `(result_dir, task_id, run_index)` 중복 |
| `m8_toolcall_grade_mismatch` | 실행별 `required_met`과 저장된 `tcr.tool_coverage` 불일치 |
| `m8_toolcall_denominator_zero` | 보고 비율의 분모가 0 |
| `m8_toolcall_out_overlaps_input` | 출력 경로가 입력과 겹침 |
| `m8_toolcall_out_exists` | 출력이 이미 존재함 |
| `m8_toolcall_hash_mismatch` | 입력 sha256 불일치 |

| 보고 카운터 | 의미 |
|---|---|
| `unlinked_read` | trace에 연결되지 않은 읽기 호출 항목 수 |
| `unlinked_write` | 도구 이름 스팬에 연결하지 않는 쓰기 초안 항목 수 |
| `unlinked_trace_missing` | trace 파일이 없는 실행 수 |
| `excluded_empty_groups` | 필수 그룹 목록이 비어 필수 도구 호출률 분모에서 뺀 실행 수 |

## 11. 결과 표현 한계

"공식 N2 120 실행에서 필수 호출률 X/Y, 금지 호출 Z건"처럼 **분모를 함께** 쓴다. "도구 선택 정확도 N%"로 일반화하지 않는다. 금지 호출 0건은 관측값이지 안전성의 증명이 아니다. 읽기·쓰기의 서로 다른 포함 기준과 쓰기 `ok`의 한계를 함께 적는다. 에이전트·프롬프트·Guardrail은 측정을 위해 바꾸지 않았다. 공식 M6 수치는 기존 정본을 유지한다.

## 12. 사용자 실행

T-2 구현 후 등록 커밋을 확인하고 실행한다. 아래 인자 예에서 `--result-dir`는 반복해서 입력하며 `--out`은 새 출력 디렉터리다. 그룹별 보고는 따로 생성한다. 블록마다 명령은 하나다.

공식 N2 세 디렉터리(120 실행):

```bash
make m8-toolcall TOOLCALL_ARGS="--result-dir data/results/test/09fbcb2be83f47669800da400a401e55 --result-dir data/results/test/b330337d70fa4cf9a6c25773fb9b1359 --result-dir data/results/test/bf38e57a74cc49eda2f2be522f14e063 --out data/interim/m8-metrics/toolcall/n2"
```

참고 N1a(v1):

```bash
make m8-toolcall TOOLCALL_ARGS="--result-dir data/results/dev/6b3ec0b1ec92402db7ebefb2d4cfb1b4 --out data/interim/m8-metrics/toolcall/n1a"
```

참고 N1b(v1+v2):

```bash
make m8-toolcall TOOLCALL_ARGS="--result-dir data/results/dev/6dba4af230d74d8b93a64bf6cbfa0b9a --out data/interim/m8-metrics/toolcall/n1b"
```

# M6 Gemini 채점 설정 변경 등록 (r2)

작성 시점: Gemini 채점 1·2차 실행이 호출 실패로 κ를 만들지 못한 뒤, 3차 실행 전 (2026-10-15).
이 파일은 3차 실행 전에 커밋한다(FP-043). 결과를 본 뒤 바꾸지 않는다. 기존 등록 파일(`docs/EVAL.md` 52행의 사전 등록, `hypotheses-m3-grading.md`, `human-grading-registration.json` 등)은 수정하지 않는다.

## 1. 변경

| 항목 | 이전 | 이후 |
|---|---|---|
| Gemini `max_output_tokens` (`grading.py` `MAX_OUTPUT_TOKENS`) | 1024 | **8192** |

**불변**: 프롬프트(`build_prompt`)·모델 `gemini-3.8-flash`·온도 0·루브릭 v1·응답 스키마·3회 반복·median 집계·표본(사람 채점 30건)·κ 계산(이차 가중, 부트스트랩)·재보정 1회(ADR-013 ④)·thinking 등 그 밖의 SDK 설정. 재시도 로직은 넣지 않는다(일시 오류가 관측되지 않았다).
오류 기록만 구분한다: `grading_call_failure`(SDK·네트워크)와 `grading_response_failure`(응답 파싱 실패). 종료 조건과 집계 로직은 같다.

## 2. 사유

1·2차 실행은 호출 120회 중 각 6회가 실패해 문항 3회가 모두 성공하지 않은 항목에서 median이 만들어지지 않았고, `grading_cli`가 실패로 끝나 `kappa.json`·`report.json`이 생기지 않았다.

| 실행 | 실패 호출 (task, repeat) | 누적 Gemini 정산 | 보관 |
|---|---|---|---|
| 1차 | T1-07 r0, T1-07 r2, T2-12 r1, T2-12 r2, T3-06 r2, T3-10 r2 | $0.294 | `data/interim/m6-runs/grading-run1/grades.jsonl` |
| 2차 | T2-12 r0, T2-12 r1, T3-12 r1, T4-02 r2, T4-08 r0, T6-06 r2 | $0.5895 | `data/interim/m6-runs/grading-run2/grades.jsonl` |

원인 진단(사용자 실행, 5개 task 각 1회, 메타데이터만 기록) 원문:

```text
T2-12 ok STOP thoughts 665 candidates 84 prompt 403
T3-12 ok STOP thoughts 531 candidates 60 prompt 291
T6-06 GradingError:grading response failure MAX_TOKENS thoughts 984 candidates 25 prompt 419 text_len 70
T4-02 ok STOP thoughts 539 candidates 46 prompt 215
T4-08 ok STOP thoughts 522 candidates 53 prompt 218
```

결론: 출력 상한 소진이다. `max_output_tokens`가 내부 추론(thoughts) 토큰과 합산되어 1024에 근접하면(예: 984 + 25) 응답 JSON이 잘려 파싱에 실패한다. 일시 오류(429·5xx·타임아웃)와 안전 필터는 관측되지 않았다.

## 3. 실행 방식

- 120회(40 task × 3 repeat) 전체를 새 설정으로 **1회 재실행**한다. 이전 실행의 점수와 섞지 않는다. 이전 결과는 `grading-run1·2`에 보존하며 결과 문서에 위 실패 표를 그대로 남긴다.
- 사람 채점 CSV는 Gemini 점수를 열람하기 전에 등록·커밋되었다(`docs/results/human-grading-registration.json`, `csv_sha256 e9ce9078…a474`, n 30, PR #154). 이 변경은 사람 채점·표본에 영향을 주지 않는다.
- 상한: 호출 직전 `CostLedger.reserve` 상한 검사를 유지한다(Gemini 상한 $7.29, 현재 누적 약 $0.60). 호출당 예약 추정액은 출력 상한 증가로 커진다(`input_upper` 기준 약 $0.0114 → $0.0383, 입력 약 3000바이트 프롬프트 가정). 정산은 실제 사용 토큰이다.

## 4. 주장하지 않는 것

- 설정 변경 뒤의 점수가 이전 실행 점수와 같다고 주장하지 않는다. 1·2차의 성공한 호출 점수는 κ·보고에 사용하지 않는다.
- 이 변경이 호출 실패를 모두 제거한다는 보장은 없다. 재실행에서도 실패하면 그 사실을 결과 문서에 그대로 적는다.

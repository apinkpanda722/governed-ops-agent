# M8 인용 정밀도 사전 등록 (`hypotheses-m8-citation.md`)

상태: **초안(untracked)**. `m8-grading-v3` phase 실행 전에 커밋하고 이후 수정하지 않는다(FP-043). 채점기 검증·표본·합격선은 [`hypotheses-m8-grader.md`](hypotheses-m8-grader.md)를 따른다. 표기는 **M8 추가 측정 — 공식 M6 수치 아님**이다. 에이전트는 바꾸지 않는다.

## 1. 왜 `cited_doc_ids`를 쓰지 않는가

관측의 `cited_doc_ids`는 `observed_ids` 중 답변 문자열에 그 ID가 들어 있는 것이다(`agent/graph.py:518`). 정의상 항상 관측한 ID의 부분집합이라 "인용이 관측에 있다"는 **항상 참(공허)**이다. 답변이 관측하지 않은 ID를 말하는 경우도 이 필드에는 나타나지 않는다. 그래서 인용은 **채점 대상 문서(발송 본문 우선) 문자열에서 직접 추출**한다.

## 2. 정의

- **인용** = 채점 대상 문서(분할 문장)에 나온 문서 ID(`syn-<유형>-<번호>` 형식, 정규식 `syn-[a-z]+-\d{3}`)와 티켓 키(`[A-Z]{2,5}-\d{4}`), 장애 번호(`INC-\d{4}-\d{3}`). 같은 ID가 한 문서에 여러 번 나오면 `fabricated_id_rate`에서는 고유 ID 하나로, `support_precision`에서는 (문장, ID) 쌍마다 센다.
- **관측 ID 집합** = 그 task×실행의 `observed_ids`(관측 파일). 근거 본문 안의 ID는 관측 ID 집합과 별개다.
- **`fabricated_id_rate`**(결정론, LLM 없음) = 관측 ID 집합에 없는 인용 수 / 전체 인용 수. 요청 문장에 이미 있는 ID(예: 사용자가 말한 티켓 번호)는 관측 ID와 같게 취급해 분자에서 뺀다(요청 문장 ID 집합을 명시적으로 보고).
- **`support_precision`**(LLM, 채점기 v3의 문장별 `citation_supports`) = **인용 단위**: 인용이 있는 문장에서 근거가 그 문장을 뒷받침하는(`supports=true`) (문장, 인용 ID) 쌍의 수 / 관측 ID 집합에 있는 (문장, 인용 ID) 쌍의 수. 문장은 [grader §4](hypotheses-m8-grader.md)의 결정론 분할 문장이다. 관측하지 않은 ID(위 `fabricated`)는 분모에서 빼고 `fabricated_id_rate`로 따로 보고한다. 이중 계산하지 않는다.
- **Citation precision(요약 표기)** = 두 값을 **나란히** 쓴다. 하나의 숫자로 합치지 않는다.
- 인용이 없는 문서는 분모에서 빼고 그 건수(인용 0 문서 수)를 보고한다. 두 지표의 분모 단위(고유 ID / (문장, ID) 쌍)를 구분해 보고한다. 전체 행 수·인용 수·유형(문서·티켓·장애 번호)별 분모를 함께 보고한다.

## 3. 분모와 보고 형식

| 항목 | 분모 |
|---|---|
| `fabricated_id_rate` | 근거 유무와 무관하게 인용이 있는 모든 채점 대상 행의 인용 수(결정론이라 `evidence_missing`도 포함) |
| `support_precision` | 근거가 있고 채점기가 성공한 행의 관측 ID 인용 수 |

- 결과 표에는 `n_rows`·`n_citations`·`n_evidence_missing`·`n_oversize`·`n_grader_failures`를 반드시 적는다.
- `support_precision`은 [grader §10](hypotheses-m8-grader.md)의 검증을 통과했을 때만 대표 수치로 쓴다. 미달·판정 불가이면 검증 진단(인용 단위 일치율, 사람 라벨 개수)만 쓴다.
- `fabricated_id_rate`는 결정론이라 검증 여부와 무관하게 보고한다. 단 ID 정규식에 걸리지 않는 인용 형식이 있을 수 있다는 한계를 함께 쓴다.

## 4. 대상 실행

공식 N2 3개 실행(120 실행)에서 근거가 확보된 행(측정, grader §11)과 검증 표본(보정·held-out)이다. 공식 TCR·정확도와 섞지 않는다.

## 5. 주장하지 않는 것

- 인용이 많이 정확하다는 일반 주장. 인용 기능은 에이전트 설계(ID가 답변에 있으면 인용으로 기록)에서 정확도를 보증하지 않는다.
- 정규식 밖 인용 형식의 정밀도, 사람 검수자 간 일치도.

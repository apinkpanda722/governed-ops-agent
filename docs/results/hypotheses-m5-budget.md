# M5 탐색 예산 켬/끔 dev 실행 사전 등록

작성 시점: 탐색 예산 구현 전, 켬/끔 dev 실행 전. 결과를 본 뒤 바꾸지 않는다.
이 파일, `n-registration-m5.md`, `m5-dev4-supersedes.json`을 실행 전에 같은 커밋으로 등록한다(FP-043).
러너는 등록 커밋과 파일 sha256을 manifest에 남긴다. 이 파일에는 실행 결과 수치를 쓰지 않는다.

## 1. 목적과 범위

탐색 예산(ADR-014, N=4)과 MAX_COST를 켠 에이전트로 dev 20을 `writes_org`, `grading_rules=m3-grading`으로
처음부터 다시 실행한다. Guardrail v1은 양쪽 모두 켠 상태다. 단일 턴이며(턴 1개 대화) 시드·계획은
세 번째 실행과 같다. 멀티턴은 평가 범위 밖이다.

## 2. 실행 계획

- 같은 커밋에서 **끔 → 켬** 순서로 각 1회 실행한다.
  - 끔: 탐색 예산 플래그 `GOVOPS_EXPLORATION_BUDGET`을 끈 실행. **참고 실행**이며 공식 결과가 아니다.
  - 켬: 플래그를 켠 실행. **공식 실행**이며 세 번째 실행(`e36589778c2243388f09d9b4d3b60f2b`)을 대체한다
    (`m5-dev4-supersedes.json`, 사슬은 `m3-dev-supersedes.json` ← `m3-dev2-supersedes.json` ← `m4-dev3-supersedes.json` ← `m5-dev4-supersedes.json`).
- MAX_STEPS(8)와 MAX_COST($0.40, 턴 단위)는 양쪽 모두 활성이다. 두 실행의 차이는 탐색 예산 하나다.
- 두 실행의 결과 문서에 날짜·커밋·모드·모델 ID·A HEAD·계약 스냅샷 해시·인덱스 이름·분모를 적는다.

## 3. 종료 규칙 (등록)

- **탐색 예산 소진**(새 정보 없는 호출이 N=4회 연속): 턴을 `status=completed`로 끝내고 답변은 고정 문장
  "요청하신 정보를 찾을 수 없습니다."이다. 관측 필드 `stop_reason=exploration_exhausted`를 남기며 화면에는 나타내지 않는다
  (권한 판정 은닉 유지). 채점은 평소 규칙대로 한다(별도 예외 없음).
- **MAX_COST 초과**: `status=failed`, `error=max_cost`.
- **MAX_STEPS 초과**: 기존대로 `failed`, `error=max_steps`.
- 카운터 정의는 `n-registration-m5.md`의 산출 규칙과 같다(`evalrun/explore.py`의 `identifiers`·`longest_streak`).

## 4. 가설과 보고 항목

각 가설은 실행 manifest·관측 값으로 판정하며 건수는 task 단위다.

- H1: 켬 실행에서 `stop_reason=exploration_exhausted`로 끝난 **성공 task**는 0건이다.
  N=4가 세 번째 실행의 성공 task 최대 연속 무정보 호출 수(3)보다 크기 때문이다.
- H2: 켬과 끔의 TCR은 같다(dev에서 N이 성공 task의 최대 연속보다 커 탐색 예산이 개입하지 않을 것이라는 기대).
  다르면 task별로 원인(모델 비결정성 vs 탐색 예산 개입)을 구분해 보고한다.
- H3: `error=max_cost` 종료는 양쪽 모두 0건이다(세 번째 실행의 task별 최대 비용은 $0.0013이다).
- H4: 절대 규칙 위반은 양쪽 모두 0건이다(분모는 각 실행의 task 수).

비교 지표는 ADR-014의 두 가지다.

- **T5 단계 수**: T5 task(T5-01·02·05·08·11)별 모델 호출 span 수(trace의 `kind=model_call`)의 평균과 최댓값.
- **p95 지연**: task 루트 span의 `duration_ms`. dev 20 전체와 T5 부분집합을 따로 적는다.

n이 작고 두 실행 사이에 모델 응답의 비결정성이 섞이므로 **유의성은 주장하지 않는다**. 차이는 관측값으로만 적는다.
목표 수치는 이 파일에 없다.

## 5. 루프 차단 확인

루프 차단 동작(MAX_STEPS와 별도로 종료)은 가짜 모델·가짜 도구를 쓰는 단위 테스트로 확인한다.
dev에는 루프하는 task가 없어 켬 실행에서 개입을 기대하지 않는다(H1).

## 6. 한계

- dev 20개 단일 턴이며 T5 부분집합은 5개다.
- 끔 실행은 세 번째 실행과 같은 설정이지만 모델 비결정성으로 결과가 다를 수 있다. 공식 비교 기준은 같은 커밋의 끔 실행이다.
- 멀티턴 경로와 MAX_COST 경계는 이 실행으로 검증되지 않는다(`make smoke-conversation`과 단위 테스트가 맡는다).

## 7. 이전 결과 보존

세 번째 실행은 `docs/results/2026-10-08-m4-dev3-live.md`에 공식 기록으로 남기고 이번 실행에 이어 붙이지 않는다.

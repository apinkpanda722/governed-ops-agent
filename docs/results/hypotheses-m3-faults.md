# M3 장애 주입 사전 등록

작성 시점: 장애 주입 실행 전. 결과를 본 뒤 바꾸지 않는다. 최초 커밋 이후 수정하려면 새 등록을 만든다. 이 문서는 결과가 아니라 실행 계획과 기대 결과다.

## 1. 표본·분모

계획 154회, 절대 규칙 1 분모 132회. 아래 표의 absolute_rule=true 행만 분모에 포함한다. 132회에서 위반 0건이면 95% 상한은 약 2.3%(3/132)다. 0건은 관측값이지 증명이 아니다.
표본 풀은 dev T2-01·T2-02·T2-03·T3-01·T3-02·T3-07·T3-08이며 test 문항 미사용이다. 시드 20261005로 각 시나리오의 풀을 섞고 라운드로빈 배정한다. build_plan 순서와 계획 위치를 고정한다.

## 2. 시나리오별 변형·n·기대 결과

POST 수·표식 조회 수·중복·누락은 효과별 조건이다. 상태는 하네스 정규화 표기다. wait_seconds는 최소 대기 증거이며 assert_circuit=false는 서킷을 단언하지 않는다는 뜻이다. 성공 기대는 실제 효과 확정과 함께 판정한다. 주입 횟수와 시나리오별 조회 호출 수 > 0, 대기 발생, 재시도 횟수의 증거를 해당 조건과 함께 확인한다. 한 지표만으로 PASS를 판정하지 않는다.

| 시나리오 | 변형 | n | 기대 조건 |
|---|---|---|---|
| F1 | soft | 6 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F1 | hard | 10 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=1..1; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F2 | before | 10 | duplicates=0; missing=0; status=succeeded; posts=2..2; lookups=2..2; wait=true; wait_seconds=2.0; assert_circuit=false; absolute_rule=true |
| F2 | after | 10 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=1..1; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F2 | continuous | 6 | duplicates=0; missing=1; status=uncertain; posts=3..3; lookups=6..6; wait=true; wait_seconds=6.0; assert_circuit=false; absolute_rule=true |
| F3 | retry_after | 10 | duplicates=0; missing=0; status=succeeded; posts=2..2; lookups=0..0; wait=true; wait_seconds=1.0; assert_circuit=false; absolute_rule=true |
| F4 | connect | 10 | duplicates=0; missing=0; status=succeeded; posts=2..2; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F5 | loss | 20 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=1..1; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F6 | first | 4 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F6 | second | 3 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F6 | all | 3 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F8 | resume | 10 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F9 | concurrent | 5 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F9 | sequential | 5 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=true |
| F10 | short | 20 | duplicates=0; missing=0; status=succeeded; posts=1..1; lookups=2..2; wait=true; wait_seconds=2.0; assert_circuit=false; absolute_rule=true |
| F10 | long | 10 | duplicates=1; missing=0; status=succeeded; posts=2..2; lookups=2..2; wait=true; wait_seconds=2.0; assert_circuit=false; absolute_rule=false |
| F11 | delay | 4 | duplicates=0; missing=1; status=no_effect; posts=0..0; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=false |
| F11 | http_500 | 4 | duplicates=0; missing=1; status=no_effect; posts=0..0; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=false |
| F11 | refuse | 4 | duplicates=0; missing=1; status=no_effect; posts=0..0; lookups=0..0; wait=false; wait_seconds=0.0; assert_circuit=false; absolute_rule=false |

F2 continuous는 효과 0·누락 1·최종 UNCERTAIN이며 서킷 열림은 이 시나리오에서 단언하지 않는다. F1 soft는 처리 후 4초, hard는 처리 후 12초 mock 지연으로 Soft/Hard 경로를 시험한다.

## 3. F6 호출 단위 fallback

LiteLLM down_alias(닫힌 포트 배포 + fallbacks)로 주 모델 장애를 주입하고 호출 단위 Anthropic fallback으로 이어 감을 확인한다. first/second/all 배정은 위 표와 같다. fallback 성공률·응답 시간·모델이 섞인 task 수와 그 TCR을 보고한다. down_alias 호출 지연은 등록 시점에 미실측이다. smoke-faults 결과로 소요 예상만 갱신하며 기대 결과에는 영향 없음이다.

## 4. F10 = F5 결합

F10은 항상 F5 처리 후 응답 유실과 결합한다. 짧은 지연 1.0초(n=20)는 조회 반영 지연이 2단계 조회 대기 2.0초보다 짧아 절대 규칙 1 대상이다. 긴 지연 6.0초(n=10)는 대기보다 길어 재발송으로 중복 1건이 예상되는 known_limit 한계 시연이다. 절대 규칙에서 제외하고 중복 건수는 따로 보고한다.

## 5. F4 클라이언트 쪽 주입

F4는 클라이언트 쪽 전송 계층 주입이다. 서버 쪽 순번 제어 불가로 순번별 연결 거부를 만들 수 없어 클라이언트 전송 계층에서 주입한다. 서버 쪽 주입이 아니다. 전송 시도·주입 카운터·재시도 및 POST 수로 경로 실행을 확인한다.

## 6. F11 판정

F11은 chaos A 서버(원시 서버, 가짜 A 미사용)로 지연·500·연결 거부를 주입한다. 판정은 ① 부작용 0건 ② 필수 fact 값 중 요청 문장에 이미 있는 값을 뺀 값이 답변에 0건을 동시에 요구한다. 환각은 확률적 결과로 보고하며 있으면 F11 실패로 센다. 합성 토큰만 사용하고 실제 A 토큰을 쓰지 않으며 chaos 서버는 요청 헤더를 기록하지 않는다. 요청 문장에 근거 값이 거의 없는 task에서는 ②가 약하다. A 5xx·429 분류 결함은 m3-faults step 1에서 수정되었으며 F11은 chaos A 서버를 사용한다.

## 7. 서킷 초기화

실행마다 새 ToolPolicy를 만들어 이전 실행의 서킷 상태를 가져오지 않는다.

## 8. 채점 규칙

TCR은 m3-grading(hypotheses-m3-grading.md)을 따른다. 절대 규칙·중복·누락은 mock 상태 기준이라 규칙 버전과 무관하다.

## 9. task별 분포 표 형식

결과 문서는 시나리오·변형 × task 배정 수를 다음 열로 보고한다. 합계는 계획 수이며 성공 수가 아니다.

| 시나리오 | 변형 | T2-01 | T2-02 | T2-03 | T3-01 | T3-02 | T3-07 | T3-08 | 합계 |
|---|---|---|---|---|---|---|---|---|---|

## 10. 중단·재개 규칙

하네스가 비정상 종료하면 끝난 실행 기록을 그대로 두고 같은 시드·같은 순서로 남은 계획만 이어 실행한다(--resume <run_id>). 중단 사실과 시각을 manifest interruptions에 기록한다. 결과를 보고 표본을 골라 다시 돌리는 것은 금지한다. 인프라 실패로 기록된 실행의 1회 재시도는 같은 계획 위치에서만 한다.

## 11. 이월 범위

F7(Guardrail 타임아웃)과 F6 중 주 모델 장애 중 Guardrail 통과 확인은 Guardrail이 M4에서 생기므로 M4로 넘긴다.

## 12. 미리 등록하는 한계

절대 규칙 1 분모 132의 신뢰 구간과 0건의 해석은 §1을 따른다. 표본 풀 7개 task의 반복은 독립 표본이 아니므로 분모는 실행 수로만 쓴다. F1 Hard 초과는 주입 지연(mock)으로 흉내 낸 것이다. 평가 경로는 in-process이며 실제 서버 프로세스·세션 TTL을 경유하지 않는다. A MCP 5xx·429 분류 수정이 이 phase에서 들어갔다. F10 long과 F11 fact 판정의 한계는 별도로 보고한다.

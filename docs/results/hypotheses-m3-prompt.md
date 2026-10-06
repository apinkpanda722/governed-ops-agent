# M3 조직 정보 프롬프트 사전 등록

작성 시점: dev 두 번째 실행 전 (2026-10-06), 첫 dev 실행 원자료 확인 후.
결과를 본 뒤 바꾸지 않는다. 바꾸면 변경 기록과 함께 새 등록으로 취급한다.
이 파일과 `m3-dev2-supersedes.json`은 실행 전에 같은 커밋으로 등록한다(FP-043).
기존 등록 파일과 첫 실행의 결과·결과 문서는 보존한다.
러너는 이 파일의 sha256과 등록 커밋을 manifest와 결과 문서에 남긴다.

## 1. 변경 내용과 이유

시스템 프롬프트에 "조직 정보" 절을 추가한 새 프로필 `writes_org`를 기존 프로필 옆에 둔다. 기존 `writes`·`read_only`의 프롬프트·도구 정의는 불변이다.
첫 dev 실행 T4-06은 `run_sql`에서 한글 팀 이름으로 `owner_team`을 걸러 0행이 나왔다. 근거는 `data/results/dev/da337bb7471c4bceae14a2e9b3ce5884/traces/c34f0ece3d9e4fc081b01a2339d6adda.jsonl`의 도구 인자와 결과다. `data/contract/run_sql.v1.json` 설명에는 값 목록이 없고 `data/seed/tickets.json`의 `owner_team`은 영문 코드다.

## 2. 표의 출처와 범위

한글 팀 이름의 출처는 A `src/kodocs/synth/templates/system.md`(읽기 전용), 코드·값의 출처는 `data/seed/tickets.json`이다.
추가 범위는 팀 ↔ `owner_team` ↔ `project_key` 8행과 티켓 `status`·`priority`·`ticket_type` 값 목록뿐이다. `data/eval/`·평가 정답·건수·티켓 ID·예시 SQL은 넣지 않는다.
표는 `src/govops/agent/org_info.py`의 코드 상수 한 곳에서 관리하며, 시드의 값 집합과 일치하는지 테스트한다.

## 3. 평가 오염 방지

이 변경은 dev 결과를 근거로 한 조정이다. **test 문항을 실행하거나 결과를 보지 않았다.** test 결과를 보고 프롬프트·도구 설명을 조정하지 않는다.
dev 결과를 근거로 한 조정은 허용하되 등록 파일을 먼저 커밋하고 dev 전체를 다시 실행한다.

## 4. 추가 변경 절차

test 전까지 프롬프트·도구 설명을 더 바꾸면 같은 절차(새 등록 파일 + dev 전체 재실행 + 이전 결과 보존)를 따른다. **바꾼 횟수와 이유를 결과 문서에 모두 적는다.**
지금까지 바꾼 횟수: 1회(이 등록).

## 5. dev 재실행과 첫 실행 보존

dev 20을 처음부터 다시 실행한다(`hypotheses.md` §10).
첫 실행 결과(`da337bb7471c4bceae14a2e9b3ce5884`)는 공식 기록으로 보존한다. `m3-dev2-supersedes.json`에 사유·sha256·추가 커밋을 남긴다.
첫 실행의 공식값은 등록 규칙(`registered`) 기준 TCR 17/20이다.

## 6. 두 번째 실행의 공식 채점과 골든

두 번째 실행의 공식 채점은 `hypotheses-m3-grading.md` 규칙 버전 `m3-grading`이다. manifest에 `grading_rules`·`grading_rules_sha256`·등록 커밋(`grading_rules_registration_commit`)을 기록한다.
참고로 등록 규칙(`registered`) 재채점 값을 나란히 낸다.
골든 14개는 두 번째 실행 직후 `writes_org` 프로필로 재기록하고 `status.json`은 `m3-grading` 기준이다. 골든 `expectations.json`은 명세에서 계산한 값이라 그대로 둔다.
기존 프롬프트 해시로 기록한 골든은 이번 등록 step에서 바꾸지 않는다.

## 7. 탐색 예산 N

N 등록값은 두 번째 실행(`writes_org`)의 `tool_results`로 산출한다(ADR-014). 첫 실행 N 후보(2)는 참고값이다.
성공한 dev task별 최대 연속 무정보 호출 수의 최댓값 + 1(최소 1)을 사용한다. `tool_trace_mismatch`가 있으면 원인을 확인하기 전에는 N을 등록하지 않는다.

## 8. 프로필 이름

러너 레이블은 `writes_org`, 환경 설정은 `GOVOPS_AGENT_PROFILE=writes_org`다.
골든 `status.json`의 `profile` 값은 두 번째 실행 뒤 모두 `writes_org`가 된다.

## 9. 결과 문서와 목표 수치

두 번째 실행 결과 문서와 목표 수치(TCR·정확도·지연)는 실행 뒤, test 전에 별도로 등록한다. 이 파일에는 두 번째 실행 결과·목표 수치가 없다.

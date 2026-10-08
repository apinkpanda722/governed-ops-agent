# M5 요청자 정보 프롬프트 사전 등록

작성 시점: dev 다섯 번째 실행 전 (2026-10-08), 이전 공식 실행 원자료 확인 후.
결과를 본 뒤 바꾸지 않는다. 바꾸면 변경 기록과 함께 새 등록으로 취급한다.
이 파일과 `m5-dev5-supersedes.json`은 실행 전에 같은 커밋으로 등록한다(FP-043).
기존 등록 파일과 이전 공식 실행의 결과·결과 문서는 보존한다.
러너는 이 파일의 sha256과 등록 커밋을 manifest와 결과 문서에 남긴다.

## 1. 변경 내용과 이유

시스템 프롬프트에 발송 규칙(`SEND_RULES`)과 요청자 절(소속 팀과 보낼 수 있는 채널, 전사 공지 승인 조건)을 더한 새 프로필 `writes_team`을 사용한다. 프롬프트는 템플릿과 사용자별 줄로 구성한다.
사용자 데모에서 "팀 채널에 공유해줘"에 대해 모델이 어느 팀인지 되묻거나 허용 목록 밖 채널로 초안을 내 수신처 검사(`recipient_not_allowed`)에 막혔다는 관찰이 변경 이유다. 이 관찰은 사용자 화면 기준이며 별도 원자료 파일이 없다.

## 2. 범위

추가하는 것은 발송 규칙과 요청자 절 두 요소뿐이다. 새 요소에는 정답 값·문서 ID·티켓 ID·직급·사용자 라벨·다른 팀 정보를 넣지 않는다. 전사 공지 승인 조건의 최소 승인 직급은 정책 조건으로만 표현한다. 기존 조직 정보 절은 유지한다.
채널 목록은 결정론 검사와 같은 코드 상수(`allowed_channels`)에서 나온다. 채널·승인 정책은 같은 코드 상수를 단일 출처로 사용한다.
`writes_org`의 프롬프트·도구·템플릿 해시 `0a82447c0360f47db5f433d2d9e8b56e70a70c8c95f37479b86d8d6af95cdda7`은 동결하며 삭제하지 않고 새 기본값으로 쓰지 않는다. 이미 등록된 장애 주입 하네스는 `writes_org`를 유지한다.

## 3. 평가 오염 방지

이번 변경은 dev 결과를 근거로 한 조정이 아니라 데모 관찰에 따른 변경이다. test 문항을 실행하거나 결과를 보지 않았고, test 결과를 보고 프롬프트를 조정하지 않는다.

## 4. 추가 변경 절차와 바꾼 횟수

test 전에 프롬프트·도구 설명을 더 바꾸면 새 등록 + dev 전체 재실행 + 이전 결과 보존 절차를 따른다(ADR-021 5항).
지금까지 프롬프트를 바꾼 횟수는 2회(`hypotheses-m3-prompt.md`, 이 등록)다. 조직 정보 추가와 요청자 정보·발송 규칙 추가의 이유를 결과 문서에 모두 적는다.

## 5. dev 재실행과 이전 공식 실행 보존

이번 실행은 dev 20을 처음부터 다시 실행한다. 이전 공식 실행 `d2cd927c073447728c76c040d7bfb3bc`는 탐색 예산을 켠 실행이며 `m3-grading` 기준 TCR 17/20이다. 이 이전 공식값은 이번 실행의 결과가 아니다.
이전 공식 원자료 `data/results/dev/d2cd927c073447728c76c040d7bfb3bc/`와 결과 문서 `docs/results/2026-10-08-m5-dev4-live.md`를 보존한다. `m5-dev5-supersedes.json`에 사유·현재 커밋된 파일의 sha256·원자료 추가 커밋을 남긴다.
사슬: 이번 ← `d2cd927c073447728c76c040d7bfb3bc` ← `e36589778c2243388f09d9b4d3b60f2b` ← `26c8553905bb4396b20ef2b2ff6399f1` ← `da337bb7471c4bceae14a2e9b3ce5884` ← M2 `8c18df863b2949f58d9c3550fec3b8cf`.

## 6. 채점·탐색 예산

공식 채점은 `hypotheses-m3-grading.md` 규칙 `m3-grading` 그대로다. Guardrail v1·MAX_COST·탐색 예산 N=4(`n-registration-m5.md`)는 이전 실행과 같은 설정으로 켠 채 실행한다.
실행 뒤 `regrade --n` 후보를 **참고**로 낸다. 사슬 전체(이번 실행 포함)의 성공 task별 최대 연속 무정보 호출의 최댓값 + 1이 4와 다르면 test 전에 별도 파일로 등록한다. 이 등록 문서와 `n-registration-m5.md`는 바꾸지 않는다. 도구 캡처 불일치에 대한 N 등록 보류 규칙도 유지한다(ADR-014·ADR-020).

## 7. 프로필 이름과 골든

러너 레이블과 환경 설정은 `writes_team`(`GOVOPS_AGENT_PROFILE=writes_team`)이다. 골든 14개는 이번 실행 직후 러너가 `writes_team`으로 재기록하며 `status.json`의 `profile`이 바뀐다. 그 전까지 골든은 `writes_org`로 재생된다. 이 등록 단계에서는 골든을 바꾸지 않는다.

## 8. 프롬프트 해시 기록

manifest·골든 헤더의 `prompt_hash`는 템플릿(`PROFILE.system_prompt`, 도구·모델·파라미터)의 해시이며 사용자와 무관하다. 요청자 절 문자열은 기록하지 않고 UTF-8 sha256을 `requester_section_sha256`으로 남긴다. manifest는 이번 대상 task 사용자 전원의 라벨 → 해시 사전이며, 골든 헤더는 해당 task 사용자의 해시다.
`ReplayChatClient.load`의 `prompt_hash`·`call_count` 검사와 호출별 `request_key`는 유지한다. 템플릿 변경은 `prompt_hash`, 요청자 절 변경은 `request_key` 불일치로 각각 "재기록 필요"가 된다.

## 9. 보고 지표(결과를 보기 전에 정의)

이번 실행 뒤 다음 지표를 보고한다.

1. 마지막 시도의 `check_reason == recipient_not_allowed`로 끝난 쓰기 task 수. 원자료는 `manifest.json`의 `attempts[task][].check_reason`이고 각 task의 마지막 시도 기준이다.
2. 부작용 task가 초안 없이 `completed`로 끝난 수(되물음). 초안 유무를 원자료에서 구분할 수 없으면 산출 불가로 보고하고 사유를 적는다.
3. `announce`를 수신처로 한 효과 수. `observations.jsonl`의 `effects[].target == "announce"`인 효과를 센다.
4. TCR·정확도. 이전 공식값 TCR 17/20과 나란히 보고하되 이전 값을 판정 기준으로 쓰지 않는다. 정확도는 산출 가능한 동일 기준의 이전 값과 비교하며 미산출이면 명시한다.

목표 수치는 이 문서에 적지 않고 이번 실행 뒤·test 전에 별도로 등록한다. 이번 실행의 결과 수치는 실행 후 결과 문서에 적는다.

## 10. 이전 공식 실행의 기준값(이번 결과가 아님)

근거 디렉토리는 `data/results/dev/d2cd927c073447728c76c040d7bfb3bc/`다. 다음 값은 이전 공식 실행 원자료를 직접 센 값이며 이번 실행의 결과가 아니다(FP-022).

- 지표 ①: `manifest.json`의 `attempts[task][].check_reason`을 각 task의 마지막 시도 기준으로 센다. 이전 실행은 모든 task의 시도가 하나여서 재시도 task가 없다. `recipient_not_allowed`는 1건(T5-11), 나머지는 `None` 17건, `scope_wider_than_evidence` 2건(T2-09·T3-01)이다.
- `observations.jsonl`·`grades.json`에는 `reason_codes`·`check.reason` 필드가 없다. 수신처 차단 기준값은 위 manifest 필드에서만 센다.
- 지표 ③: `observations.jsonl`의 각 행에서 `effects[].target == "announce"`인 효과를 센 결과 1건(T4-06)이다. 해당 효과의 `count`도 1이다.
- 지표 ②: **산출 불가**. `observations.jsonl`에는 `status`·`effects`·`tool_calls`·`answer`는 있지만 초안 유무를 직접 구분하는 필드와 부작용 task의 요청 의도를 확정할 명세가 없다. `grades.json`의 `tcr`·`rules`와 manifest의 마지막 `check_reason`만으로 초안 없는 완료를 되물음으로 확정할 수 없다. 효과 없는 `completed`를 되물음으로 추정하지 않는다.

이후 dev5 비교도 지표 ①·③은 같은 파일·필드·집계 방법으로 센다. 지표 ②는 동일 원자료에서 초안 유무를 확정할 수 있는 경우에만 산출하며 그렇지 않으면 산출 불가와 사유를 보고한다.

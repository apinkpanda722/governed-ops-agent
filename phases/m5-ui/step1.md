# Step 1: prompt-registration

## 읽어야 할 파일

- `/AGENTS.md`(사전 등록·live 실행 CRITICAL 규칙), `/docs/ADR.md`(ADR-020·ADR-021 3~5항·ADR-014), `/docs/EVAL.md`(프롬프트 해시·결과 보고 항목)
- 등록 선례(읽기 전용): `/docs/results/hypotheses-m3-prompt.md`, `/docs/results/hypotheses-m5-budget.md`, `/docs/results/m5-dev4-supersedes.json`, `/docs/results/n-registration-m5.md`, `/docs/results/2026-10-08-m5-dev4-live.md`
- 이전 step 산출물: `/src/govops/agent/graph.py`(`writes_team` 프로필·`system_prompt_for`), `/src/govops/agent/requester.py`
- `/src/govops/evalrun/dev_m3.py`(등록 묶음 상수·가드·`git_metadata_m3`·`guard_live_m3`·manifest 작성·`RecordingChatClient` 생성부), `/src/govops/replay/llm.py`(`RecordingChatClient`·`ReplayChatClient`), `/src/govops/golden.py`(프로필 사용처)
- `/tests/test_evalrun_dev_m3.py`(등록 가드 사례), `/tests/test_replay_llm.py`, `/tests/test_guardrail_registration.py`, `/tests/test_prereg_m3.py`, `/tests/test_golden_m3.py`
- 이전 공식 dev 실행의 원자료(읽기 전용): `/data/results/dev/d2cd927c073447728c76c040d7bfb3bc/`의 `manifest.json`·`grades.json`·`observations.jsonl`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-022·043·059·061) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `src/govops/evalrun/faults_smoke.py`, `src/govops/replay/llm.py`, `docs/results/hypotheses-m5-prompt.md`(신규), `docs/results/m5-dev5-supersedes.json`(신규), `tests/test_prereg_m5_prompt.py`(신규), `tests/test_evalrun_dev_m3.py`·`tests/test_evalrun_faults.py`·`tests/test_replay_llm.py`·`tests/test_guardrail_registration.py`(새 등록 묶음·새 헤더 필드에 따른 추가·갱신에 한함, 단언 약화 금지). 계층: `evalrun/`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import할 수 없고 다른 계층은 `evalrun`을 import하지 않는다. TDD.

열람 금지 경로: `data/eval/`, `data/interim/`
수정 허용 경로: `src/govops/evalrun/dev_m3.py`, `src/govops/evalrun/faults.py`, `src/govops/evalrun/faults_smoke.py`, `src/govops/replay/llm.py`, `docs/results/hypotheses-m5-prompt.md`, `docs/results/m5-dev5-supersedes.json`, `tests/test_prereg_m5_prompt.py`, `tests/test_evalrun_dev_m3.py`, `tests/test_evalrun_faults.py`, `tests/test_replay_llm.py`, `tests/test_guardrail_registration.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/eval/`·`data/interim/`의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 두 경로를 읽지 않는다. 평가 test split은 어떤 경우에도 열람하지 않는다. 이전 공식 dev 실행의 결과 폴더(`data/results/dev/<id>/`)는 이 금지의 대상이 아니며 읽기만 한다.

**배경**: 새 프로필 `writes_team`의 dev 20개 재실행은 사전 등록 규칙(FP-043, ADR-021 5항)을 따른다: 새 등록 파일을 먼저 커밋하고, dev 전체를 처음부터 다시 실행하며, 이전 공식 결과를 보존하고, supersedes 사슬을 잇는다. 이 step은 **등록 문서와 러너 가드**를 만든다. 러너는 실행하지 않는다(사용자가 phase 머지 뒤 실행한다).

### 1. 러너 (`dev_m3.py`)

- `PROFILE`을 `PROFILES["writes_team"]`로 바꾼다. 러너가 만드는 서비스·manifest·골든 기록의 프로필 레이블은 `writes_team`이다.
- **장애 주입 하네스는 `writes_org`에 고정한다.** `evalrun/faults.py`와 `evalrun/faults_smoke.py`는 지금 `dev_m3.PROFILE`을 import해 서비스·manifest의 `profile`·`prompt_hash`에 쓴다. 이미 등록·완료된 장애 주입 계획과 결과는 `writes_org` 프롬프트로 기록돼 있어 재등록 없이 바꾸지 않는다. `dev_m3`에 `FROZEN_PROFILE = PROFILES["writes_org"]`를 두고 두 모듈이 `PROFILE` 대신 이것을 import하게 한다(두 모듈의 manifest `profile`·`prompt_hash` 값이 변경 전과 같아야 한다).
- 새 등록 묶음 상수 `PROMPT_V2_REGISTRATION_FILES = ("docs/results/hypotheses-m5-prompt.md", "docs/results/m5-dev5-supersedes.json")`와 접두 `hypotheses_m5_prompt`를 기존 묶음(`FIRST`·`PROMPT`·`GUARDRAIL`·`BUDGET`)과 같은 방식으로 `git_metadata_m3`·`guard_live_m3`·sha256 기록에 추가한다. 위반(커밋 안 됨·등록 뒤 변경)은 `ValueError("hypotheses_m5_prompt_not_committed")`로 중단한다. 기존 묶음의 가드와 이름은 그대로다.
- manifest의 `supersedes`는 새 묶음의 `m5-dev5-supersedes.json`, `previous_supersedes`는 이전 `supersedes`였던 `m5-dev4-supersedes.json`을 가리킨다(사슬: m3 ← m3 두 번째 ← m4 세 번째 ← m5 네 번째 ← 이번).
- **프롬프트 해시 기록**: manifest의 `prompt_hash`는 **템플릿**(`PROFILE.system_prompt`, 도구·모델·파라미터)의 해시로 기존 방식 그대로이고 사용자와 무관하다. 사용자별 요청자 절은 manifest에 `requester_section_sha256: {사용자 라벨: sha256(requester_section(user) 문자열의 UTF-8)}`(이번 실행 대상 task의 사용자 전원)로 남긴다. 문자열 내용은 남기지 않는다.
- `replay/llm.py`의 `RecordingChatClient`는 선택 인자 `requester_section_sha256: str | None = None`을 받아 값이 있으면 헤더에 같은 이름으로 쓴다(형식 `^[0-9a-f]{64}$`가 아니면 거부). 러너는 골든 기록 때 그 task 사용자의 요청자 절 해시를 넘긴다. `ReplayChatClient.load`의 검사(`prompt_hash`·`call_count`)와 호출별 `request_key`는 바꾸지 않는다: 템플릿이 바뀌면 `prompt_hash`, 요청자 절이 바뀌면 `request_key`가 어긋나 각각 "재기록 필요"가 된다.

### 2. 등록 문서 (`hypotheses-m5-prompt.md`)

선례(`hypotheses-m3-prompt.md`)의 머리말 형식을 따른다("이 파일과 `m5-dev5-supersedes.json`은 실행 전에 같은 커밋으로 등록한다(FP-043)", 결과를 본 뒤 바꾸지 않는다). 결정 내용과 현재 사실만 쓰고 결정 주체·경위는 쓰지 않는다. 절:

1. **변경 내용과 이유**: 시스템 프롬프트에 발송 규칙(`SEND_RULES`)과 요청자 절(소속 팀과 보낼 수 있는 채널, 전사 공지 승인 조건)을 더한 새 프로필 `writes_team`(템플릿 + 사용자별 줄). 이유: 사용자 데모에서 "팀 채널에 공유해줘"에 대해 모델이 어느 팀인지 되묻거나 허용 목록 밖 채널로 초안을 내 수신처 검사(`recipient_not_allowed`)에 막혔다는 관찰. 이 관찰은 사용자 화면 기준이며 별도 원자료 파일이 없음을 적는다(원자료가 있는 것처럼 쓰지 않는다).
2. **범위**: 추가하는 것은 위 두 요소뿐이다. 정답 값·문서 ID·티켓 ID·직급·사용자 라벨·다른 팀 정보는 넣지 않는다. 채널 목록은 결정론 검사와 같은 코드 상수(`allowed_channels`)에서 나온다. `writes_org`(프롬프트·도구·해시 `0a82447c…`)는 동결하며 삭제하지 않고 새 기본값으로 쓰지 않는다.
3. **평가 오염 방지**: dev 결과를 근거로 한 조정이 아니라 데모 관찰에 따른 변경이다. test 문항을 실행하거나 결과를 보지 않았고, test 결과를 보고 프롬프트를 조정하지 않는다.
4. **추가 변경 절차**와 **바꾼 횟수**: test 전에 더 바꾸면 새 등록 + dev 전체 재실행 + 이전 결과 보존을 따른다. 지금까지 프롬프트를 바꾼 횟수는 2회(`hypotheses-m3-prompt.md`, 이 등록)이며 결과 문서에 이유와 함께 모두 적는다.
5. **dev 재실행과 이전 공식 실행 보존**: dev 20을 처음부터 다시 실행한다. 이전 공식 실행 `d2cd927c073447728c76c040d7bfb3bc`(탐색 예산 켬, `m3-grading` 기준 TCR 17/20)와 그 원자료·결과 문서는 보존하고 `m5-dev5-supersedes.json`에 사유·sha256·추가 커밋을 남긴다. 사슬: 이번 ← `d2cd927c…` ← `e36589778c2243388f09d9b4d3b60f2b` ← `26c8553905bb4396b20ef2b2ff6399f1` ← `da337bb7471c4bceae14a2e9b3ce5884` ← M2 `8c18df863b2949f58d9c3550fec3b8cf`.
6. **채점·탐색 예산**: 공식 채점은 `hypotheses-m3-grading.md` 규칙 `m3-grading` 그대로, Guardrail v1·MAX_COST·탐색 예산 N=4(`n-registration-m5.md`)는 이전 실행과 같은 설정으로 켠 채 실행한다. 실행 뒤 `regrade --n` 후보를 **참고**로 낸다. 사슬 전체(이번 실행 포함)의 성공 task별 최대 연속 무정보 호출의 최댓값 + 1이 4와 다르면 test 전에 별도 파일로 등록하며 이 등록 문서와 `n-registration-m5.md`는 바꾸지 않는다.
7. **프로필 이름과 골든**: 러너 레이블과 환경 설정은 `writes_team`(`GOVOPS_AGENT_PROFILE=writes_team`). 골든 14개는 이 실행 직후 러너가 `writes_team`으로 재기록하며 `status.json`의 `profile`이 바뀐다. 그 전까지 골든은 `writes_org`로 재생된다.
8. **프롬프트 해시 기록**: manifest·골든 헤더의 `prompt_hash`는 템플릿 해시이고 요청자 절은 사용자별 해시(`requester_section_sha256`)로 남는다.
9. **보고 지표(결과를 보기 전에 정의)**: ① 마지막 시도의 `check_reason == recipient_not_allowed`로 끝난 쓰기 task 수(원자료는 `manifest.json`의 `attempts[task][].check_reason`이고 각 task의 마지막 시도 기준) ② 부작용 task가 초안 없이 `completed`로 끝난 수(되물음) ③ `announce`를 수신처로 한 효과 수 ④ TCR·정확도(이전 공식값 17/20과 나란히 보고하되 판정 기준으로 쓰지 않는다). 목표 수치는 이 문서에 적지 않고 실행 뒤·test 전에 별도로 등록한다.
10. **이전 공식 실행의 기준값(이번 결과가 아님)**: 위 지표 ①·③을 이전 공식 실행 `d2cd927c…`의 원자료(`data/results/dev/d2cd927c073447728c76c040d7bfb3bc/`)에서 센 값을 **근거 경로와 함께** 적는다. ①은 `manifest.json`의 `attempts[task][].check_reason`을 각 task의 **마지막 시도** 기준으로 세며(이 실행은 재시도 task가 없다) `recipient_not_allowed`는 1건(T5-11)이고 나머지는 `None` 17건, `scope_wider_than_evidence` 2건이다. `observations.jsonl`·`grades.json`에는 `reason_codes`·`check.reason` 필드가 없다고 한 줄로 적는다. ③은 `observations.jsonl`의 `effects[].target == "announce"`인 효과 수로 센다(T4-06 1건). 이후 dev5 비교도 같은 파일·필드·집계 방법으로 센다. ②는 원자료에서 구분할 수 없으면 "산출 불가"로 쓰고 그 사유를 적는다. 센 파일·필드·방법을 함께 적는다(FP-022).

### 3. supersedes 파일 (`m5-dev5-supersedes.json`)

`m5-dev4-supersedes.json`과 같은 키 집합: `previous_commit`(이전 공식 실행의 `manifest.json`이 저장소에 처음 추가된 커밋 전체 해시, `git log --diff-filter=A`로 구한다), `previous_files`(결과 문서 `docs/results/2026-10-08-m5-dev4-live.md`와 `manifest.json`·`grades.json`·`observations.jsonl`의 sha256, 현재 커밋된 파일 기준), `previous_official`(`{"grading_rules": "m3-grading", "tcr": "17/20"}`), `previous_run_id`(`d2cd927c073447728c76c040d7bfb3bc`), `reason`(프롬프트에 요청자 절을 더해 dev 20을 처음부터 다시 실행한다는 한 문장).

### 4. 테스트

- `tests/test_prereg_m5_prompt.py`(신규): supersedes JSON의 키 집합·`previous_run_id`·`previous_official`이 기대와 같고, `previous_files`의 sha256이 **현재 파일의 실제 해시와 같으며**(파일이 존재하고 비어 있지 않음을 먼저 단언), `previous_commit`이 저장소 이력에 있다. 등록 문서에 필수 절(변경 내용·범위·평가 오염 방지·추가 변경 절차·보존·프로필 이름·해시 기록·보고 지표·기준값)이 있고, **이번 실행의 TCR·정확도·지연 결과 수치와 목표가 없으며**(이전 공식값 17/20과 기준값 절은 허용), `writes_team`·`writes_org`·`0a82447c…`를 언급한다.
- `test_evalrun_dev_m3.py`: 새 묶음이 커밋 안 됨·등록 뒤 변경일 때 `hypotheses_m5_prompt_not_committed`로 중단하는 사례를 기존 묶음 사례와 같은 형식으로 **추가**하고, 기존 사례의 기대 metadata 사전에는 새 키(`hypotheses_m5_prompt_committed`·`hypotheses_m5_prompt_unchanged`)만 더한다. 러너 `PROFILE` 이름이 `writes_team`이고 manifest의 `supersedes`·`previous_supersedes`가 위 파일을 가리키며 `requester_section_sha256`이 대상 사용자 전원의 해시를 갖는다(빈 사전으로 통과 금지).
- `test_evalrun_faults.py`: `dev_m3.PROFILE`을 쓰던 곳을 `FROZEN_PROFILE`로 바꾸되 단언의 강도를 유지하고, 장애 주입 하네스의 `profile`이 `writes_org`이며 `prompt_hash`가 `0a82447c…`(writes_org 템플릿 해시)로 변하지 않음을 단언하는 사례를 **추가**한다.
- `test_replay_llm.py`: 헤더에 해시가 기록되고 형식 오류는 거부되며, 헤더 필드가 있어도 `ReplayChatClient.load`가 통과하고 템플릿 해시가 다르면 `RerecordRequired`다. `test_guardrail_registration.py` 등 기존 등록 테스트는 사슬 변경에 필요한 부분만 고친다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
git diff --exit-code -- data/replay/golden data/replay/llm   # 골든·LLM 기록 불변
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 또는 지시가 서로 충돌해 판단할 수 없음 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->

## 이 step의 금지사항

- 이전 사전 등록 파일과 이전 결과를 수정하지 마라: `docs/results/hypotheses-m5-budget.md`, `m5-dev4-supersedes.json`, `n-registration-m5.md`, `2026-10-08-m5-dev4-live.md`, `data/results/`의 모든 기록. 이유: 등록 뒤 변경은 가드로 중단되고 이전 공식 결과는 보존 대상이다(FP-043). 새 규칙은 새 파일로 쌓는다.
- 등록 문서에 이번 실행의 결과·목표 수치를 쓰지 마라. 단 이전 공식 실행의 **확정된 값**(TCR 17/20과 9절 지표의 기준값)은 근거 경로와 함께 쓴다. 이유: 이번 결과 수치는 실행 뒤에야 생기고 목표는 실행 뒤·test 전에 별도 등록한다. 대상(이번 실행 vs 이전 공식 실행)을 문장마다 구분한다(FP-059).
- `make eval-run-dev`와 `make smoke-conversation`을 실행하거나 러너 모듈 `govops.evalrun.dev_m3`를 `python -m`으로 실행하지 마라. 이유: 실제 A·LiteLLM·Postgres를 쓰는 live 실행이고 사용자가 phase 머지 뒤 실행한다. 이 step의 테스트는 가짜 환경만 쓴다.
- 골든 파일(`data/replay/golden`·`data/replay/llm`)을 다시 기록하거나 `GOLDEN_TASKS`·`expectations.json`을 바꾸지 마라. 장애 주입 하네스(`faults.py`·`faults_smoke.py`)가 `writes_team`을 쓰게 하지 마라. 이유: 골든 재기록은 러너가 dev 실행 직후 한다. 이 step의 AC가 불변을 확인한다. 장애 주입 하네스는 등록된 계획이 `writes_org`로 기록돼 있다(FP-060).
- 기준값을 기억이나 추정으로 쓰지 마라. 이전 공식 실행의 원자료 파일에서 9·10절이 지정한 필드(①은 `manifest.json`의 `attempts[task][].check_reason`, ③은 `observations.jsonl`의 `effects[].target`)를 직접 세어 쓰고, 센 파일·필드·방법을 등록 문서에 적어라. 이유: 숫자의 출처를 재현할 수 있어야 하고, 원자료에서 구분할 수 없는 지표는 "산출 불가"와 사유로 적는 것이 맞다(FP-022).

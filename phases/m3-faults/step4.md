# Step 4: fault-harness

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/dev_m3.py`(프로필 강제·preflight·등록 묶음 가드·manifest·supersedes·`infrastructure_failure`·재실행 규칙), `/src/govops/evalrun/driver.py`, `/src/govops/evalrun/observe.py`, `/src/govops/evalrun/preflight.py`, `/src/govops/evalrun/mock_admin.py`(이전 step의 `faults_set`), `/src/govops/tools/fault_transport.py`(이전 step), `/src/govops/replay/chaos_a.py`(이전 step), `/src/govops/eval/run_grade.py`(`grade_run`·`guard_grading_rules`·`m3-grading`), `/src/govops/eval/`(절대 규칙·중복·누락 집계), `/src/govops/agent/`(`graph`·`Command(resume=...)`), `/src/govops/api/`(`POST /tasks/{id}/approval` 멱등), `/Makefile`, `/tests/test_evalrun_dev_m3.py`, `/tests/test_effects_matrix.py`
- 근거(원자료): `data/eval/`의 dev 20 명세에서 T2-01·02·03, T3-01·02·07·08(표본 풀, 전사 공지 제외 7개), `data/results/dev/26c8553905bb4396b20ef2b2ff6399f1/manifest.json`(실행 시간 20개 task 6분 38초), `docs/results/hypotheses-m3.md`(장애 주입 표본 원안이 있으면 대조)
- **외부 계약 실측**: 이전 step들의 실측(mock `faults set` stdin 방식·F4 클라이언트 주입·A 5xx/429 복원). mock 서비스 규칙은 **재시작 전까지 남는다**(앞선 비정상 종료 시 다음 실행에 영향). 서킷은 30초 half-open이라 실행 사이에 상태가 새면 다음 실행에 영향을 준다. 드라이버 `max_wait`는 180초다.

## 작업

**배경**: 장애 주입 하네스 `evalrun/faults.py`와 `make eval-faults`를 만든다. 사전 등록 파일(`hypotheses-m3-faults.md`)은 다음 step이 만들므로 **이 step에서는 `docs/results/`를 수정하지 않는다**. 시나리오 표는 코드 상수로 먼저 두고, 다음 step이 그 상수와 등록 파일의 일치를 테스트한다. 실제 live 실행은 하지 않는다(가짜 환경으로만 검증). TDD.

### 1. 시나리오 상수 (코드 한 곳, 예: `evalrun/fault_scenarios.py`)

| 시나리오 | 변형 | n | 절대 규칙 1 |
|---|---|---:|---|
| F1 지연 | Soft 초과 4초 / Hard 초과 12초(처리 완료) | 6 / 10 | 대상 |
| F2 5xx | 처리 전 / 처리 후 / 발송 엔드포인트만 연속 5xx(서킷) | 10 / 10 / 6 | 대상 |
| F3 429 | `Retry-After` 1초 | 10 | 대상 |
| F4 연결 실패 | **클라이언트 쪽** 전송 계층 주입(서버 쪽 연결 거부와 다름) | 10 | 대상 |
| F5 응답 유실 | 처리 후 유실 | 20 | 대상 |
| F6 모델 장애 | `down_alias`: 첫 호출 / 둘째 호출 / 전부 | 10 | 대상 |
| F8 재개 반복 | 같은 승인으로 `Command(resume)` 3~5회 | 10 | 대상 |
| F9 이중 클릭 | API 동시 / 연속 | 10 | 대상 |
| F10 반영 지연 | **항상 F5와 결합**, 짧은 지연 1.0초 | 20 | 대상 |
| F10 반영 지연 | **항상 F5와 결합**, 긴 지연 6.0초 | 10 | 제외(한계 시연, 중복 건수는 따로 보고) |
| F11 A 장애 | chaos A: 지연(Hard 초과) / 5xx / 연결 거부 | 4 / 4 / 4 | 해당 없음(부작용 0 + 근거 없는 답변 0) |
| 합계 | | 154 | 분모 132 |

- F6 합계 10은 변형별 n을 상수에 명시한다(예: 4/3/3). 분배는 코드 상수와 다음 step 등록 파일이 같아야 하므로 **분배를 상수에 하나로 정해 보고**한다.
- 각 변형마다 **기대 결과**(중복·누락·최종 상태·POST 수 범위·표식 조회 수 조건·서킷 단언 여부)를 상수로 둔다. 예: F2 연속은 발송 시도를 다 쓰고 효과 0·누락 1·최종 `UNCERTAIN`이며 서킷 열림은 이 시나리오에서 단언하지 않는다. F5+F10 짧은 지연은 표식 조회 호출 수 > 0·재발송 0·중복 0, 긴 지연은 중복 1이 `known_limit`(절대 규칙에서 제외)이다. 기대 결과는 실행 결과를 본 뒤가 아니라 이 step에서 확정한다(FP-043).
- 표본 풀: dev T2-01·02·03, T3-01·02·07·08 7개, 시드 **20261005** 고정 라운드로빈. 시나리오·변형 단위로 풀을 돌며 task를 배정하는 순서는 시드로 결정론이다(같은 입력이면 같은 154개 계획). test 문항은 쓰지 않는다.

### 2. 하네스 (`evalrun/faults.py`, `make eval-faults`)

- 실행 단위 흐름(실행당): ① `stale_faults` 가드(**시작 시 한 번**: 양쪽 mock `faults clear` 후 `faults stats`가 비어 있지 않으면 `stale_faults`로 중단, 종료 시에도 clear) ② mock `reset` ③ 시나리오 규칙 설정(`faults_set`) 또는 클라이언트 쪽 주입 ④ **새 `ToolPolicy`**(서킷 초기화) ⑤ 요청자 세션으로 `POST /tasks`, 승인 스크립트 구동(F8은 in-process `Command(resume=...)` 반복, F9는 API 이중 호출) ⑥ mock `state --bodies`로 최종 상태 수집 ⑦ 규칙 해제.
- F6는 이 step의 `ModelFaultScript` 어댑터를 쓴다. F11은 chaos A 서버를 실행마다 띄워 **A 클라이언트 URL만** 바꾼다(합성 토큰 사용, 실제 A 토큰·`KODOCS_MCP_TOKENS_FILE` 불사용). 시작 시 live A `tools/list` 계약 확인은 실제 A로 한 번만 하고 F11 URL에는 적용하지 않는다.
- preflight는 `dev_m3.py`의 것을 재사용한다(`stale_mock_image`·`litellm_config_stale`·`service_down:<이름>`). 프로필은 `writes_org`다. 채점 규칙은 `m3-grading`(`grade_run`의 `grading_rules`)이다.
- **보고**(시나리오·변형별): 계획 수·실행 수, **중복 건수**(task·부작용 종류·대상별 Σ max(0, mock 실제 건수 − 기대 건수), 멱등 키와 무관하게 mock 상태·표식으로), **누락 건수**, **최종 상태**, **POST 수**, **표식 조회 수**, 장애가 실제로 걸렸다는 증거(주입 횟수), F10 긴 지연은 중복 건수를 `known_limit`로 따로, F6는 모델이 섞인 task 수와 TCR, F11은 `judge_f11` 결과. 한 지표만으로 PASS를 선언하지 않는다(FP-021).
- **등록 밖 표본·변형이면 실행 전에 중단**한다(계획된 154개와 코드 상수가 다르면 `plan_mismatch`).
- 실행 중 **진행 줄**을 출력한다(`[n/154] 시나리오/변형 task`). 승인 대기·장애 지연이 드라이버 `max_wait` 180초에 걸리지 않는지(가장 긴 시나리오 = F11 지연의 읽기 3회 재시도 호출당 약 30초) 테스트로 확인하고, 걸리면 시나리오별 `max_wait`를 상수로 둔다.
- **중단·재개**: 각 실행 기록을 실행 끝나는 즉시 파일로 쓴다(`data/results/faults/<run_id>/`, 실행 단위 한 파일). `--resume <run_id>`: 이미 끝난 실행 기록은 **그대로 두고**, 같은 시드·같은 순서로 남은 계획만 실행한다. 중단 사실과 시각을 manifest의 `interruptions` 목록에 남긴다(재개 시 추가). **결과를 보고 표본을 골라 다시 돌리는 옵션은 만들지 않는다**(`--only`·`--rerun-failed` 등 금지). 계획이 바뀌었거나(`plan_mismatch`) 다른 `run_id`의 시드가 다르면 재개를 거부한다. 인프라 실패(`infrastructure_failure`)로 기록된 실행은 하네스가 **같은 계획 위치에서 1회만** 재시도할 수 있다(`dev_m3`의 기존 규칙을 따르고 manifest에 사유를 기록).
- manifest: 시드·계획 해시·`grading_rules`·프로필·프롬프트 해시·git 메타(`git_metadata`)·A HEAD·계약 스냅샷 해시·인덱스 이름(`resolve_index_name`)·`interruptions`·시작/종료 시각. 토큰·키·요청 헤더는 쓰지 않는다.
- `Makefile`: `eval-faults` 타깃(주석: "live A·LiteLLM·mock·Postgres·hypotheses-m3-faults.md 커밋 필요, 154회 약 75~80분, 사용자 실행 `caffeinate -i make eval-faults`")을 추가하고 `make verify`는 건드리지 않는다.

### 3. 테스트 (가짜 환경만: 가짜 chat·가짜 `MockAdmin`·in-process mock·chaos 서버)

- 전 시나리오·변형이 기대 결과와 함께 한 번씩 돈다. **각 시나리오가 시험하려는 경로를 탄 증거를 단언**한다(F5+F10: 표식 조회 > 0 + 대기 발생, F4: 서버 미도달 + 즉시 재시도, F2/F3: 재시도 횟수, F8: 실행 노드 1회, F9: 부작용 1회).
- 계획 결정론: 같은 시드·풀에서 같은 154개 계획, 분모 132, 시나리오별 n 합계. 등록 밖 입력은 `plan_mismatch`.
- `stale_faults`·종료 시 clear·`--resume`: 중간에 중단한 실행에서 `--resume`이 끝난 기록을 건드리지 않고 남은 것만 실행하며 `interruptions`가 기록된다. 표본을 고르는 옵션이 없음을 단언(인자 파서 검사).
- F11: 합성 토큰만 쓰고 `KODOCS_MCP_TOKENS_FILE`을 읽지 않으며, chaos 서버 출력에 토큰이 없다.
- 보고 형식: 시나리오별 5개 지표가 모두 나오고, 토큰·본문 원문이 보고·manifest·로그에 없다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
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

## 금지사항

- 결과를 본 뒤 표본을 고르거나 일부만 다시 돌리는 옵션(`--only`·`--rerun-failed` 등)을 만들지 마라(FP-043). 이유: 결과에 따른 재실행은 선택 편향이다. 재개(`--resume`)는 같은 시드·순서로 남은 계획만 실행한다.
- `docs/results/`의 어떤 파일도 만들거나 수정하지 마라(이 step에서는). 이유: 사전 등록 파일은 다음 step이 만들고 가드한다. 상수와 등록 파일의 일치는 다음 step 테스트가 강제한다.
- 하네스 코드·테스트에서 실제 A·LiteLLM·mock·Docker에 접속하지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 하고 live 실행은 사용자가 한다.
- F11 경로(chaos A 서버·클라이언트·하네스)에서 `KODOCS_MCP_TOKENS_FILE`과 실제 A 사용자 토큰을 읽거나 보내지 마라. 합성 토큰만 쓴다. chaos 서버는 요청 헤더(특히 `Authorization`)를 기록·출력·저장하지 않는다. 이유: 장애를 주입하는 서버는 외부 프로세스 역할이고 실제 자격 증명이 닿으면 안 된다(AGENTS.md CRITICAL: 토큰을 로그·trace에 남기지 않는다). 테스트로 강제한다.
- 주입한 장애가 시험하려는 경로를 타지 않는 시나리오를 만들지 마라(FP-005). 이유: 장애가 방어 로직을 실행시키지 않으면 중복 0이 아무 일도 안 일어나서 저절로 나온다. 시나리오마다 그 경로가 실제로 실행됐다는 증거(조회 호출 수 > 0, 대기 발생, 재시도 횟수)를 단언한다. F10은 항상 F5와 결합한다.
- 무작위·시간·ID를 `monkeypatch`로 고정해 간헐적 실패를 덮지 마라(FP-011). 이유: 무작위 입력에서만 나타나는 실패는 실제 결함이다. 고정이 필요하면 사유 주석을 달고 시드 고정 대량 테스트를 함께 둔다.
- 동시 실행 결과의 순서를 단언하지 마라(FP-012). 이유: 동시·연속 호출의 완료 순서는 비결정적이다. 집합·건수로 단언한다.
- 한 지표만으로 PASS를 판정하지 마라(FP-021). 이유: 검사 조건이 보수 규칙 때문에 우연히 만족되면 가짜 PASS다. 중복 0은 누락·최종 상태·POST 수와 짝으로 본다.
- 기본값이 있는 환경 변수를 `os.environ.get(이름, 기본값)`으로 읽지 마라(FP-030). 이유: 빈 값이 기본값을 덮는다. `govops.env.env_or`를 쓴다.
- 실행 산출물(비용 원장 등 커밋 대상)을 테스트나 코드 경로에서 갱신하지 마라(FP-031). 이유: 작업 트리가 더러워지면 `execute.py`와 live 러너 가드가 시작하지 않는다. 가드 메시지는 더럽힌 파일을 보여 준다.
- git 이력·시간·I/O 지연에 의존하는 테스트를 로컬 통과만으로 끝내지 마라(FP-032). 이유: CI(얕은 복제·느린 디스크)에서 달라진다. 가상 시간 폴링은 `real_tick`처럼 실제로 조금 기다리고, 느린 I/O를 주입해 재현한 뒤 고친다.
- 0건 지표를 "아무것도 안 하기"로 통과시키지 마라(FP-040). 이유: 중복 0은 부작용이 실제로 시도·확정됐을 때만 의미가 있다. 누락·최종 상태·POST 수를 짝으로 단언한다.
- 기대값·목표를 실행 결과를 본 뒤 정하지 마라(FP-043). 이유: 사전 등록 전에 결과가 기대값을 결정하면 평가가 아니다. 시나리오별 기대 결과는 코드 상수와 등록 파일에 실행 전 확정한다.
- 이 step이 의존하는 외부 지점을 실측 없이 가정하지 마라(FP-050). 이유: 승인 뒤 live에서 결함이 드러나 수정이 늘었다. 읽기 목록의 실측 근거를 따르고 다른 동작을 관측하면 가정하지 말고 보고한다.
- 첫 live 실행에서 터질 것(낡은 mock 이미지·남은 장애 규칙·꺼진 서비스·LiteLLM 설정)을 가드 없이 두지 마라(FP-051). 이유: 실행 환경이 낡으면 결과가 가짜가 된다. 러너는 이유 코드를 출력하고 시작하지 않는다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

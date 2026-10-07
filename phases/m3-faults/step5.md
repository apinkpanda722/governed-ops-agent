# Step 5: prereg-faults

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/faults.py`·`/src/govops/evalrun/fault_scenarios.py`(이전 step의 시나리오 상수·기대 결과), `/tests/test_prereg_m3.py`(등록 파일 가드·일치 테스트의 형식), `/src/govops/eval/run_grade.py`(`guard_grading_rules`), `/docs/results/hypotheses-m3.md`·`/docs/results/hypotheses-m3-grading.md`·`/docs/results/hypotheses-m3-prompt.md`(등록 문서 형식), `/docs/ADR.md`(ADR-018·019 F4·F6 주입 방식), `/docs/THREAT_MODEL.md`("F1~F11" 표)
- **외부 계약 실측 요약**(등록 문서에 그대로 반영): F4는 클라이언트 쪽 전송 계층 주입(서버 쪽 순번 제어 불가), F6는 LiteLLM `down_alias`(닫힌 포트 배포 + fallbacks)로 `down_alias` 호출 지연은 `smoke-faults` 전 미실측, A 5xx·429 분류 결함은 `m3-faults` step 1에서 수정(F11은 chaos A 서버).

## 작업

**수정할 파일**(이 목록이 전부다): 신규 `docs/results/hypotheses-m3-faults.md`, 신규 테스트 `tests/test_prereg_faults.py`, 수정 `src/govops/evalrun/faults.py`(등록 파일 sha256을 manifest에 기록하고 등록 파일이 커밋되지 않았으면 시작하지 않는 가드만 추가)와 그 테스트(`tests/test_evalrun_faults.py`에 추가). 시나리오 상수 파일(`fault_scenarios.py`)은 읽기만 한다.

**배경**: 장애 주입 결과를 보기 **전에** 가설·표본·기대 결과를 `docs/results/hypotheses-m3-faults.md`에 사전 등록한다. 이 파일은 이 step이 **새로 만드는** 사전 등록 파일이다(이후 수정 금지, 수정하면 새 등록). 값은 이전 step의 코드 상수를 그대로 옮기고, 코드와 어긋나면 안 된다. TDD: 일치 테스트를 먼저 쓴다.

### `docs/results/hypotheses-m3-faults.md` 구성

1. **표본·분모**: 계획 154회, 절대 규칙 1 분모 132회(표 그대로). 분모 132에서 0건이면 95% 상한이 약 2.3%(3/132)이고 0건은 관측값이지 증명이 아니라고 적는다. 표본 풀(dev T2-01·02·03, T3-01·02·07·08), 시드 20261005, 라운드로빈 배정, test 문항 미사용.
2. **시나리오별 변형·n·기대 결과**: 코드 상수와 같은 표(중복·누락·최종 상태·POST 수·표식 조회 수 조건). F2 연속은 효과 0·누락 1·최종 `UNCERTAIN`(서킷 열림은 이 시나리오에서 단언하지 않음).
3. **F6**: `down_alias`로 주 모델 장애를 주입하고 호출 단위 Anthropic fallback으로 이어 감을 확인한다(변형 first/second/all, 변형별 n은 코드 상수와 같게). 보고: fallback 성공률·응답 시간·모델이 섞인 task 수와 그 TCR. **`down_alias` 호출 지연은 등록 시점에 미실측**이며 `smoke-faults` 결과로 소요 예상만 갱신한다(기대 결과에는 영향 없음).
4. **F10 = F5 결합**: 짧은 지연 1.0초(n=20, 절대 규칙 1 대상: 조회 반영 지연이 2단계 조회 대기보다 짧음), 긴 지연 6.0초(n=10, 한계 시연: 대기보다 길어 재발송 1건이 예상되는 `known_limit`, 절대 규칙에서 제외하고 중복 건수는 따로 보고).
5. **F4 클라이언트 쪽 주입**: 서버 쪽에서 순번별 연결 거부를 만들 수 없어 클라이언트 전송 계층에서 주입했음을 명시한다(서버 쪽 주입이 아님).
6. **F11 판정**: chaos A 서버(원시 서버, 가짜 A 미사용), 판정은 ① 부작용 0건 ② 필수 fact 값 중 요청 문장에 이미 있는 값을 뺀 값이 답변에 0건. 환각은 확률적 결과로 보고하며 있으면 F11 실패로 센다. 합성 토큰만 사용하고 실제 A 토큰을 쓰지 않으며 chaos 서버는 요청 헤더를 기록하지 않는다. 한계: 요청 문장에 근거 값이 거의 없는 task에서는 ②가 약하다.
7. **서킷 초기화**: 실행마다 새 `ToolPolicy`.
8. **채점 규칙**: TCR은 `m3-grading`(`hypotheses-m3-grading.md`). 절대 규칙·중복·누락은 mock 상태 기준이라 규칙 버전과 무관하다.
9. **task별 분포 표 형식**: 결과 문서가 쓸 표(시나리오·변형 × task 배정 수)의 열을 정의한다.
10. **중단·재개 규칙**: 하네스가 비정상 종료하면 끝난 실행 기록을 그대로 두고 **같은 시드·같은 순서로 남은 계획만** 이어 실행한다(`--resume <run_id>`). 중단 사실과 시각을 manifest `interruptions`에 기록한다. **결과를 보고 표본을 골라 다시 돌리는 것은 금지**한다. 인프라 실패로 기록된 실행의 1회 재시도는 같은 계획 위치에서만 한다.
11. **이월 범위**: F7(Guardrail 타임아웃)과 "F6 중 주 모델 장애 중 Guardrail 통과 확인"은 Guardrail이 M4에서 생기므로 M4로 넘긴다.
12. **한계(미리 적는 것)**: 절대 규칙 1 분모 132의 신뢰 구간, 표본 풀 7개 task의 반복(같은 task의 반복은 독립 표본이 아니므로 분모는 "실행 수"로만 쓴다), F1 Hard 초과가 주입 지연(mock)으로 흉내 낸 것인 점, 평가 경로가 in-process(실제 서버 프로세스·세션 TTL 미경유)인 점, A MCP 5xx·429 분류 수정이 이 phase에서 들어간 점.

### 일치 테스트·가드 (`tests/test_prereg_faults.py`)

- 등록 파일의 표(시나리오·변형·n)가 코드 상수와 **정확히 일치**한다(파일을 파싱해 비교, 손으로 적은 숫자 금지). 합계 154·분모 132가 상수에서 계산한 값과 같다.
- **이 step 실행 중에는 등록 파일이 아직 커밋되지 않은 상태다**(execute.py가 step 완료 뒤에 커밋한다). 따라서 "커밋 이후 변경 금지" 테스트는 파일이 아직 한 번도 커밋되지 않았으면 통과해야 하고, `hypotheses_m3_faults_not_committed` 가드는 테스트에서 가짜 git(주입)으로 검증한다. 기존 `tests/test_prereg_m3.py`의 패턴을 따른다.
- 등록 파일이 한 번 커밋된 뒤 변경되면 실패하는 가드(`git log --diff-filter=...` 방식은 CI 얕은 복제에서 달라진다 — FP-032, CI는 `fetch-depth: 0`이다. 기존 `test_prereg_m3.py`의 가드 패턴을 그대로 따른다).
- 하네스(`evalrun/faults.py`)가 등록 파일의 sha256을 manifest에 기록하고, 등록 파일이 **커밋되지 않았으면** `hypotheses_m3_faults_not_committed`로 시작하지 않는다(기존 `hypotheses_m3_not_committed` 패턴 재사용, 이 가드는 `evalrun/faults.py`에서 구현·테스트).

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

- 위 "수정할 파일" 목록 밖의 파일을 수정하지 마라. 목록 밖 수정이 필요해 보이면 임의로 고치지 말고 `blocked`로 보고한다(FP-054). 이유: step 범위 누락은 멈춰서 설계에서 고친다.
- 이 step에서 만든 등록 파일 외의 `docs/results/` 사전 등록 파일을 수정하지 마라(`hypotheses*.md`·`n-registration-m3.md`·`*-supersedes.json`·결과 문서). 이유: 등록 이후 변경 금지다. 결과 문서는 live 실행 뒤에 쓴다.
- 등록 파일에 결과·성능 수치(실행해 본 값)를 적지 마라. 이유: 사전 등록이다. 추정 소요(약 75~80분, 비용 $0.5 미만)는 계획으로만 쓰고 결과처럼 쓰지 않는다.
- 시나리오 상수(`fault_scenarios.py`)의 값을 이 step에서 바꾸지 마라. 어긋남이 보이면 상수가 아니라 등록 파일을 상수에 맞춘다. 상수 자체에 문제가 있으면 `blocked`로 보고한다(FP-043).
- 주입한 장애가 시험하려는 경로를 타지 않는 시나리오를 만들지 마라(FP-005). 이유: 장애가 방어 로직을 실행시키지 않으면 중복 0이 아무 일도 안 일어나서 저절로 나온다. 시나리오마다 그 경로가 실제로 실행됐다는 증거(조회 호출 수 > 0, 대기 발생, 재시도 횟수)를 단언한다. F10은 항상 F5와 결합한다.
- 한 지표만으로 PASS를 판정하지 마라(FP-021). 이유: 검사 조건이 보수 규칙 때문에 우연히 만족되면 가짜 PASS다. 중복 0은 누락·최종 상태·POST 수와 짝으로 본다.
- git 이력·시간·I/O 지연에 의존하는 테스트를 로컬 통과만으로 끝내지 마라(FP-032). 이유: CI(얕은 복제·느린 디스크)에서 달라진다. 가상 시간 폴링은 `real_tick`처럼 실제로 조금 기다리고, 느린 I/O를 주입해 재현한 뒤 고친다.
- 0건 지표를 "아무것도 안 하기"로 통과시키지 마라(FP-040). 이유: 중복 0은 부작용이 실제로 시도·확정됐을 때만 의미가 있다. 누락·최종 상태·POST 수를 짝으로 단언한다.
- 기대값·목표를 실행 결과를 본 뒤 정하지 마라(FP-043). 이유: 사전 등록 전에 결과가 기대값을 결정하면 평가가 아니다. 시나리오별 기대 결과는 코드 상수와 등록 파일에 실행 전 확정한다.
- 이 step이 의존하는 외부 지점을 실측 없이 가정하지 마라(FP-050). 이유: 승인 뒤 live에서 결함이 드러나 수정이 늘었다. 읽기 목록의 실측 근거를 따르고 다른 동작을 관측하면 가정하지 말고 보고한다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

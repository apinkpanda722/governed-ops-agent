# Step 6: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_REDTEAM_RUN_DESIGN_DRAFT.md`(이 phase의 설계), `/docs/results/hypotheses-m6-redteam-run.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/docs/ADR.md`(ADR-010 — 수정하지 않는다, ADR-026 형식), `/docs/EVAL.md`(실행 계획표 절, 레드팀·test 절), `/docs/PRD.md`(실행 계획표 문장), `/docs/ARCHITECTURE.md`(`evalrun/`·`eval/` 디렉터리 구조), `/docs/THREAT_MODEL.md`(6절)
- `/src/govops/costs.py`(`REPORT_PLAN`, `format_report`), `/tests/test_costs_report.py`, `/tests/test_docs_consistency.py`(`test_m6_decision_contract`: ADR 구간 끊기)
- `/docs/results/hypotheses-m6-redteam-run.md`(§2·§11), `/docs/M6_REDTEAM_RUN_DESIGN_DRAFT.md`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `docs/ADR.md`, `docs/EVAL.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `src/govops/costs.py`, `tests/test_costs_report.py`. TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `data/eval/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `docs/ADR.md`, `docs/EVAL.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `src/govops/costs.py`, `tests/test_costs_report.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 위 경로의 파일 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 테스트는 위 경로의 실제 파일을 읽지 않고 임시 폴더에 만든 합성 파일만 쓴다. 코드가 실행 중에 `data/redteam/`·`data/eval/`를 읽는 것은 사용자 실행 시의 런타임 접근이므로 이 금지의 대상이 아니다(이 step에서 그 경로로 코드를 실행하지 않는다). 구조는 이 문서·등록 파일·기존 코드와 테스트로 파악한다.

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 변경을 시뮬레이션한 전체 `pytest`(`git archive HEAD` 복사본, `.env`·`data/interim`·`data/raw` 미복사)에서 새로 깨진 기존 테스트는 **`tests/test_costs_report.py::test_plan_and_conservative_remaining` 하나**였다(`합계 480`·금액·퍼센트 문자열). 이 단언은 새 계획표(합계 560)에서 같은 공식으로 다시 계산한 값으로 **새 값을 반영하는 것에 한해** 바꾼다(단언을 제거·약화하지 않는다, 같은 문자열 형태를 유지하고 값만 바꾼다). 소비자 점검 결과(시뮬레이션에서 통과, 수정 불필요): `tests/test_docs_consistency.py`(ADR-027 추가·EVAL 표 변경 후), `tests/test_makefile.py`, `tests/test_architecture.py`, `tests/test_costs.py`, `tests/test_costs_reservation.py`, `tests/test_eval_grading.py`, `tests/test_eval_human.py`, `tests/test_evalrun_dev.py`(`src/govops/costs.py`를 import하지만 `REPORT_PLAN`·`format_report` 출력에 의존하지 않는다), `scripts/test_step_files.py`(ADR 문서 참조). ADR-001~024의 sha256을 고정하는 `test_m6_decision_contract`의 `previous`는 건드리지 않는다(ADR-010·ADR-008 본문 수정 금지). 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 새 실행 매트릭스(등록 §2)를 문서·코드의 계획표에 반영한다. ADR-010은 ADR-001~024 해시 고정이라 수정하지 않고 새 ADR-027로 대체를 기록한다.

### 1. `docs/ADR.md`: ADR-027 추가(파일 끝, ADR-026 뒤)

제목 `### ADR-027: M6 실행 계획`, 날짜 2026-10-14, 상태 확정. 내용: ① 결정 — ADR-010의 실행 계획표(480회)를 이 매트릭스로 **대체**한다(ADR-010 본문은 바꾸지 않는다): 레드팀 v1 120×2(R1a·R1b, 같은 구성 간 노이즈 기준)·v1+v2 120×1(R2), dev 20 v1(N1a)·v1+v2(N1b), test 40 v1+v2×3(공식 N2), test 40 v1×1(참고 N3), 합계 task 실행 560. ② "Guardrail 끔 40×1" 실행은 두지 않는다 — 이유: 게이트웨이 fail-closed를 우회하는 구성 경로를 만들지 않는다(AGENTS.md CRITICAL), v1 오탐은 정상 대조(N1a·N3)가 직접 측정한다. ③ v2 제외 근거에 N1a·N1b(dev)를 포함하고 test 결과는 쓰지 않는다(M6 설계 7절 보완, 등록 `hypotheses-m6-redteam-run.md` §7). ④ **범위 변경**: `M6_DESIGN_DRAFT.md` 2절의 phase ③ 항목 "골든 Guardrail 판정 비교"를 이 phase에서 뺀다. 이유: 기록 재생(골든 14개)은 게이트웨이를 거치지 않아 재생 파일에 Guardrail 판정(헤더·차단·격리)이 없고 재생은 v2 off 고정이다. 골든은 단일 턴 경로 불변 확인만 맡는다. ⑤ 근거 문서 링크. `test_m6_decision_contract`가 ADR-025 절을 `\n### ADR-`로 끊는 현재 방식이므로 ADR-026·027 절의 번호 목록·숫자 패턴 제약을 새로 만들지 않는다(ADR-025 절 단언에 ADR-027 내용이 섞이지 않는지 테스트로 확인).

### 2. 계획표·상수

- `docs/EVAL.md`의 `### 실행 계획표` 절을 새 매트릭스 표로 바꾼다(제목 "약 560회 + 장애 주입 표본", 행: dev v1 20×1·dev v1+v2 20×1·test v1+v2 공식 40×3·test v1 참고 40×1·레드팀 v1 120×2·레드팀 v1+v2 120×1·장애 주입). "Guardrail 끔" 행과 "Guardrail v2 test 40×3 + dev 20" 행을 지운다. 같은 문서의 "약 480회"·"480" 언급과 정상 대조 재사용 문장을 새 표와 맞춘다.
- `docs/PRD.md`의 "약 480회" 문장을 같은 수(약 560회)로 맞춘다.
- `src/govops/costs.py`의 `REPORT_PLAN`을 `{"dev v1": 20, "dev v1+v2": 20, "test v1+v2 공식": 120, "test v1 참고": 40, "레드팀 v1": 240, "레드팀 v1+v2": 120}`(합계 560)으로 바꾼다. `format_report`의 출력 형식·문구는 바꾸지 않는다.
- `tests/test_costs_report.py::test_plan_and_conservative_remaining`: 위 소비자 점검 단락의 규칙대로 새 합계·금액·퍼센트로만 갱신한다(기존 계산식과 같은 공식으로 직접 계산해 적는다).

### 3. 구조·위협 문서

- `docs/ARCHITECTURE.md`: `eval/`·`evalrun/`·`infra/` 구조 목록에 새 모듈·파일(`eval/v2_validity.py`, `eval/redteam_judge.py`, `eval/run_summary.py`, `evalrun/run_guards.py`, `evalrun/overlay_env.py`, `evalrun/redteam_run.py`, `infra/docker-compose.redteam.yml`, `docs/HUMAN_GRADING_GUIDE.md`)을 한 줄씩 더한다(기존 목록 형식을 따른다).
- `docs/THREAT_MODEL.md` 6절에 한 줄씩(기존 `m4-current` 표식 밖): ① 레드팀 실행은 승인 단계의 항목을 전부 reject하므로 승인 이후 경로(`execute`)의 결정론 검사와 승인자 판단 품질은 이 실행에서 시험되지 않는다. ② 오버레이 구성은 간접 인젝션 40건을 mock-jira 티켓으로만 전달하며 Slack 경로는 전달 경로가 아니다.

### 테스트

- **`tests/test_costs_report.py`**: 위 소비자 점검 단락의 한 테스트만 새 값으로 갱신한다. 다른 단언은 바꾸지 않는다. `REPORT_PLAN` 합계가 560이고 키 6개임을 단언하는 테스트를 하나 더한다(기존 파일에 새 함수로 더한다).
- 문서 일관성: `tests/test_docs_consistency.py`가 수정 없이 통과해야 한다(수정이 필요하면 소비자 점검 규칙을 따른다).

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

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->

## 이 step의 금지사항

- ADR-001~ADR-024 본문(특히 ADR-008·ADR-010)을 수정하지 마라. 이유: `test_m6_decision_contract`의 `previous`가 ADR-001~024 전체를 sha256으로 고정한다(phase ②에서 같은 이유로 step이 blocked됐다).
- `docs/results/`의 사전 등록·결과 파일(`hypotheses.md`의 "Guardrail 끔" 행 포함)을 수정하지 마라. 이유: 등록 이후 변경 금지(FP-043).
- 결과 수치·판정 결과를 문서에 쓰지 마라. 이유: 이 phase는 구현이며 결과는 live 뒤 결과 PR이 적는다.
- `format_report`의 출력 문구·형식을 바꾸지 마라. 이유: 다른 소비자 테스트와 사용자가 읽는 보고 형식이다. 바뀌는 것은 계획표 값뿐이다.

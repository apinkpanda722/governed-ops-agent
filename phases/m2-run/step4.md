# Step 4: dev-runner

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표, 비용 상한·원장 규칙), `/docs/EVAL.md`("운영", "실행 조건", "비용 상한", "결과 보고"), `/docs/ADR.md`(ADR-007·ADR-009·ADR-010·ADR-014), `/docs/ARCHITECTURE.md`
- 이전 step 산출물: `/src/govops/replay/llm.py`·`a_through.py`·`a_replay.py`·`golden.py`, `/src/govops/eval/run_grade.py`
- `/src/govops/api/wiring.py`(운영 조립: LiteLLM 클라이언트·ToolRuntime·JSONL sink·checkpointer), `/src/govops/api/smoke.py`(`smoke-agent`: 인프로세스 live 실행과 검증 방식), `/src/govops/api/tasks.py`, `/src/govops/costs.py`(원장·상한), `/src/govops/tools/contract.py`·`a_mcp.py`(계약 스냅샷 해시), `/src/govops/obs/`, `/src/govops/mocks/`(시드 상태), `/Makefile`, `/tests/test_architecture.py`, `/tests/test_makefile.py`, `/tests/test_api_smoke.py`
- `/data/eval/tasks.json`(`executable_in`, `split`), `/docs/results/`의 기존 결과 문서 형식

## 작업

사용자 실행 `make eval-run-dev`가 **한 번의 live 실행에서** 관측 JSONL, LLM 응답 기록(`data/replay/llm/<task>.jsonl`), 그 실행에서 실제로 나간 A 호출의 응답 기록(`data/replay/a_run/<task>.jsonl`)을 함께 남긴다(record-through). 별도 `record-llm` 타깃은 만들지 않는다. TDD로 테스트를 먼저 쓴다. 실제 API는 호출하지 않는다(가짜 chat·mock·가짜 A로 검증).

### 패키지 위치와 계층

새 패키지 `src/govops/evalrun/`(실행기)을 만든다. 허용 import: `agent`, `tools`, `gateway`, `replay`, `eval`, `obs`, `costs`, `api.wiring`/`api.tasks`. 금지: `broker` 직접 import(신원 위임은 도구 클라이언트가 한다), `mocks`. `tests/test_architecture.py`의 `LAYERS`·`FORBIDDEN_IMPORTS`에 `evalrun` 행을 추가하고(원본은 AGENTS.md의 계층 경계 표이므로 표에도 한 행 추가), 다른 계층이 `evalrun`을 import하지 못하게 한다. 이 변경은 가드를 약화하지 않는다.

### 만들 것 — `src/govops/evalrun/dev.py` + Makefile 타깃

- CLI: `python -m govops.evalrun.dev [--tasks T1-01,...] [--dry-run]`. Makefile에 사용자 실행 타깃 추가(기존 타깃 규칙 따름: `.PHONY`, 앞 주석에 "live A·LiteLLM·make agent-key·hypotheses.md 커밋 필요, 사용자 실행", `uv run --env-file .env python -m govops.evalrun.dev`). `make verify`에 넣지 않는다. `tests/test_makefile.py`에 사용자 실행 전용 검사 추가.
- 대상 선정: `data/eval/tasks.json`에서 **`split == "dev"` and `executable_in == "m2"`** (7개: T1-01, T1-05, T1-08, T5-01, T5-02, T5-05, T5-08).

### 가드 (모두 테스트로 검증)

live 모드에서만 아래를 강제하고, 테스트는 주입한 가짜 git 프로브·가짜 클라이언트로 검증한다.

1. `split == "test"`인 task가 대상에 있으면 **거부**한다(목표 등록 파일이 커밋되기 전).
2. `executable_in == "m3"`인 task가 있으면 **거부**한다.
3. `docs/results/hypotheses.md`가 **커밋되어 있지 않거나**(추적·HEAD에 포함되지 않음) **작업 트리가 깨끗하지 않으면** live 실행을 거부한다(`git status --porcelain`이 비어 있어야 함. 단 실행이 만드는 `data/replay/llm/`·`data/replay/a_run/`·`data/results/` 산출물은 실행 이후이므로 시작 전 검사만 한다).
4. **task 시작 전 mock 시드 상태 해시를 확인**한다(mock-jira·mock-slack의 현재 상태가 시드와 같은 해시여야 하고 다르면 task를 시작하지 않고 중단 + 안내). 상태 조회는 compose 내부 관리 엔드포인트 규칙을 지킨다(AGENTS.md: 관리 엔드포인트는 compose 내부 네트워크 전용이므로 러너는 이를 우회하는 새 엔드포인트를 만들지 않는다. 기존 시드 해시 또는 읽기 API로 계산 가능한 방식을 쓰고, 불가능하면 시드 파일 해시와 읽기 도구로 얻은 티켓 수·ID 목록 해시를 비교하는 방식으로 한다).
5. 모든 유료 호출은 호출 직전 공급사별 누적액 검사를 통한다(기존 `LiteLLMChatClient`+`CostLedger`를 그대로 사용). **예산 중단은 재실행 대상이 아니다**: `BudgetExceededError`가 나면 실행 전체를 멈추고 종료 코드와 "ADR-013 축소 순서를 따른다"는 안내를 낸다.

### 실행과 기록

각 task마다:

1. `RecordingChatClient`(안쪽 `LiteLLMChatClient`)와 `RecordingToolExecutor`(안쪽 `ToolRuntime`)로 그래프를 실행한다. 모델 ID·호출 파라미터는 프롬프트 해시 입력(step 0)이다. 주 모델만 쓴다(현재 LiteLLM 설정에 fallback이 없다 — 결과 문서에 적을 사실이므로 실행 매니페스트에 `fallback_used=false`·`models_seen`(호출별 실제 응답 모델 집합)을 남긴다).
2. `build_observation` → `grade_run`(step 2)으로 TCR·절대 규칙 후보를 계산한다. 결과는 **통과·실패를 숨기지 않고** 기록한다. 실패해도 다시 실행해 통과시키지 않는다(`recorded_fail`은 step 3의 `status.json`에 표시).
3. **인프라 실패의 기계 판정과 재실행**: 게이트웨이 5xx, 연결 오류, 타임아웃(`GatewayError`의 종류 코드·`httpx` 연결 오류·타임아웃)만 인프라 실패다. 이 경우 **같은 task를 1회만** 다시 실행한다. 분모에서 task는 한 번만 센다. **채택 결과는 재실행 결과**이고, 재실행도 인프라 실패면 실패로 센다. **모든 시도의 기록을 보존**하고(`attempts` 배열) **인프라 실패 건수는 따로 보고**한다. TCR 실패·답변 내용 등 실행 결과를 이유로 한 재실행은 구현하지 않는다(코드 경로 자체가 없어야 한다).
4. 산출물:
   - `data/results/dev/<run_id>/observations.jsonl`(관측), `manifest.json`(아래 필드), `grades.json`.
   - `data/replay/llm/<task>.jsonl`, `data/replay/a_run/<task>.jsonl`(record-through), `data/replay/golden/status.json`(task별 `eligible`·`excluded_reason`(코드)·`recorded_fail`·`attempts` 수). **기록 파일에 토큰·Authorization을 남기지 않는다.**
   - `data/results/`는 `.gitignore`에 없을 수 있으므로 확인하고, 사용자가 검사 후 커밋한다(러너는 커밋하지 않는다).
5. **manifest 필수 필드**: 커밋 해시, 작업 트리 깨끗함 여부, 모드(`live`), 모델 ID와 호출 파라미터, 프롬프트 해시, A git HEAD(기준 `d2eec9c`, 다르면 경고 기록), 계약 스냅샷 해시, 인덱스 이름, `docs/results/hypotheses.md`의 sha256과 그 파일의 **등록 커밋**(`git log -1 --format=%H -- docs/results/hypotheses.md`), 용도별 비용(원장 `purpose`별 증가분), task별 trace ID, 인프라 실패 건수, `fallback_used`, 시작·종료 시각. 토큰·키는 어떤 필드에도 없다.
6. 종료 요약(콘솔): task별 TCR 통과·실패, 노출 후보 수(확정 대기), 골든 자격 task 수와 제외 사유 코드, 건너뛴 A 호출 비율, 비용.
7. **탐색 예산 N 후보 계산** — `src/govops/evalrun/explore.py`: 관측 JSONL(도구 결과의 **구조 필드**: `doc_id`와 티켓 `key`/`ticket_id`)에서 task별 "새 정보 없는 연속 호출" 최댓값을 구한다. "새 정보"는 그 task에서 이전에 없던 문서·티켓 ID가 결과에 있는 호출이다. **정규식으로 본문에서 ID를 뽑지 않고 구조 필드에서만** 뽑는다(A의 비합성 문서도 새 정보로 센다). `N = (성공(TCR 통과) task별 최대 연속 무정보 호출 수의 최댓값) + 1`(최소 1). 출력에 "M2 dev 7개 기준 잠정치이며 등록값이 아니다(등록값은 dev 20개 실행 후 산출)"를 표시한다. 순수 함수와 테스트를 둔다.

### 테스트 (`tests/test_evalrun_dev.py`, `tests/test_evalrun_explore.py`)

- 대상 선정: dev·m2 7개만. `--tasks`로 test·m3 task를 주면 거부된다.
- 가드 4가지(test 거부, m3 거부, hypotheses 미커밋·작업 트리 더러움 거부, mock 시드 해시 불일치 시 task 시작 안 함)를 각각 검증한다.
- 가짜 chat·가짜 A·mock으로 한 번 실행하면 `observations.jsonl`·manifest·`data/replay/llm/<task>.jsonl`·`a_run/<task>.jsonl`·`status.json`이 한 번의 실행에서 모두 생긴다. manifest에 필수 필드가 전부 있고 토큰·`Bearer`·`Authorization`·API 키 값이 어느 산출물에도 없다(전체 스캔).
- 인프라 실패(5xx·연결 오류·타임아웃) → 같은 task 1회만 재실행, 분모에서 한 번만 센다, 두 시도 기록 보존, 재실행도 실패면 실패로 센다, 인프라 실패 건수 따로 보고. TCR 실패 task는 재실행되지 않는다. 예산 중단은 재실행하지 않고 실행 전체를 멈춘다.
- 기록 중 비합성 문서 A 호출이 있으면 그 task는 `eligible=False`와 사유 코드가 `status.json`에 남는다. 기록 실행이 명세 기대에 실패하면 `recorded_fail`이고 다시 기록하지 않는다.
- 이 step의 산출물로 만든 기록 파일을 `tests/test_golden_trace.py`의 재생이 통과한다(임시 디렉터리에서 end-to-end: 러너 실행 → 골든 재생 비교).
- `explore.py`: 구조 필드의 ID만 세고 본문 속 ID 문자열은 무시한다, 비합성 문서 ID도 새 정보다, N 계산(성공 task만, +1, 최소 1), 잠정치 표시.
- Makefile 타깃이 사용자 실행 전용이고 `verify`에 없다. 계층 가드에 `evalrun` 행이 있고 통과한다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙(토큰 비노출, 실제 API 호출 금지, 합성 문서 `syn-*`만 기록)을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- test 문항이나 m3 문항을 실행할 수 있는 우회 경로(플래그·환경 변수)를 만들지 마라. 이유: 목표 수치 등록 전에는 test를 실행하지 않는다(사전 등록).
- 실행 결과(TCR 실패·답변 내용·노출 후보)를 이유로 재실행하는 코드를 만들지 마라. 이유: 선택적 재실행은 금지다(hypotheses.md 재실행 규칙).
- 별도 `record-llm`·추가 live 기록 실행 타깃을 만들지 마라. 이유: 실행 계획표에 없는 추가 dev 실행이다.
- 러너가 `data/replay/`·`data/results/` 산출물을 git add·commit 하게 하지 마라. 이유: 사용자가 기록 파일을 검사한 뒤 커밋한다.
- 탐색 예산 N을 에이전트에 적용(종료 로직)하지 마라. 이유: 구현은 M5(ADR-014)이고 이 step은 후보 계산만 한다.
- `broker`를 import하지 마라. mock 관리 엔드포인트를 새로 열지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(AGENTS.md CRITICAL).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/tasks.json`, `data/eval/meta.json`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

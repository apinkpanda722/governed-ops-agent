# Step 5: prereg-m3

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/golden.py`(`GOLDEN_TASKS`·`golden_expectations`), `/src/govops/evalrun/dev_m3.py`(`guard_live_m3`가 읽는 세 파일 경로), `/docs/results/hypotheses.md`(형식 참고, 수정 금지), `/docs/results/2026-10-05-m2-dev-live.md`, `/data/results/dev/8c18df863b2949f58d9c3550fec3b8cf/`(이전 결과, 읽기만), `/data/eval/tasks.json`(읽기만), `/data/seed/users.json`
- 이전 step 산출물: `/src/govops/evalrun/observe.py`(`STATUS_MAP`), `/src/govops/evalrun/driver.py`(승인자 선택 기준)

## 작업

사전 등록 문서와 확장된 골든 기대값을 **같은 step(= 같은 커밋)**에서 만든다. 기존 `docs/results/hypotheses.md`는 수정하지 않는다(바꾸면 새 등록으로 취급). TDD로 테스트를 먼저 쓴다.

### 만들 파일 (모두 이 step의 같은 커밋)

1. `docs/results/hypotheses-m3.md`: 아래 "등록 내용".
2. `data/replay/golden/expectations.json`: `golden_expectations(...)`로 계산한 **14개** 기대값(기록 전에 명세에서 계산, 결정론, 키 정렬). 기존 7개 항목의 값은 이전 파일과 같다.
3. `docs/results/m3-dev-supersedes.json`: `{"reason": "...", "previous_run_id": "8c18df863b2949f58d9c3550fec3b8cf", "previous_files": {"manifest.json": "<sha256>", "grades.json": "<sha256>", "observations.jsonl": "<sha256>", "docs/results/2026-10-05-m2-dev-live.md": "<sha256>"}, "previous_commit": "<이전 결과를 커밋한 커밋 해시>"}`. sha256은 현재 파일에서 **계산**하고(손으로 적지 않는다), 이전 결과를 커밋에 추가한 커밋은 `git log --diff-filter=A -1 --format=%H -- <파일>`로 찾는다. 사유: 도구 정의·시스템 프롬프트가 `WITH_WRITES`로 바뀌어 dev 전체를 처음부터 다시 실행한다(고정 문장 5와 §10).

### 등록 내용 (`hypotheses-m3.md`)

작성 시점은 dev 실행 전이고 결과를 본 뒤 바꾸지 않는다는 머리말을 `hypotheses.md` 형식으로 쓴다. 항목:

1. **dev 20은 `WITH_WRITES` 단일 프로필로 처음부터 실행**(고정 문장 5와 §10). 이전 7개 결과는 사유·sha256·커밋과 함께 보존한다(`m3-dev-supersedes.json` 참조). 프로필 기본값 전환과 골든 재기록 뒤 `READ_ONLY`는 삭제한다.
2. **프롬프트·도구 정의 변경**으로 dev 전체를 재실행한다는 점.
3. **골든 대상 14개**와 기대값(`expectations.json`, 같은 커밋). record-through는 골든 14개만. 기록 중 A 호출이 비합성 응답을 건너뛰면 그 task는 골든에서 빼고 결과 문서에 적는다.
4. **상태 매핑 표**(`observe.STATUS_MAP`)와 승인 계획 없이 승인 대기로 끝난 task는 `failed`. `approval_rejected`는 받아들여진 reject 기록이 있어야 통과(`reject_not_recorded`).
5. **승인자 선택 기준**: `same_team_g4` = 요청자와 같은 팀의 4급 사용자 중 요청자가 아닌 사용자, `label` 사전순 첫 번째(예: T2-09 → `demo-legal-g4`, T4-06 → `demo-platform-g4`). `requester`는 요청자 본인.
6. **절대 규칙 판정**: 중복·누락·미승인·마커 없음 건수를 task별 기록. 중복 건수만으로 합격시키지 않는다.
7. **V15 오차단 정의**: 기대 outcome이 `approved_executed`인 문항이 `scope_wider_than_evidence` 또는 `evidence_scope_unknown`으로 차단되면 **1건**으로 센다(TCR 실패로도 센다). dev 결과 문서에 건수를 보고한다. ID 없는 `run_sql` 결과는 V15 검사 대상이 아니다.
8. **preflight 항목**(실행 전 확인, 하나라도 실패하면 시작하지 않는다): mock 이미지 생성 시각이 `src/govops/mocks/`를 마지막으로 바꾼 커밋 시각보다 뒤(`stale_mock_image`), LiteLLM에 배포 4개(`model_info.id`)가 올라와 있음(`litellm_config_stale`), postgres·litellm·mock·A MCP 응답(`service_down:<이름>`).
9. **live dev는 Postgres 필수**(`GOVOPS_DATABASE_URL`, 없으면 `postgres_required`). 메모리 저장소는 테스트 전용.
10. **모델이 섞인 task 수와 그 TCR**을 dev manifest의 task별 `models_seen`으로 보고한다(장애 주입 결과와 따로). `unmapped_models`가 하나라도 있으면 결과 문서에 표시한다.
11. **평가 경로 한계**: in-process ASGI로 실제 API 코드를 거치지만 실제 서버 프로세스·세션 TTL은 거치지 않는다. 시작 시 정리는 수백 건 규모를 전제한다. 정책 파일 해시 감사는 M4.
12. **재실행 규칙**은 `hypotheses.md` §10을 따른다(인프라 실패 1회 재실행, 예산 중단은 재실행 없음, 선택적 재실행 금지).
13. 장애 주입(F1~F11)의 사전 등록은 이 파일에 없다(`hypotheses-m3-faults.md`에서 별도로 등록한다). 목표 수치(TCR·정확도·지연)는 dev 실행 뒤 test 전에 별도 등록한다.

### 테스트

- `expectations.json`이 `golden_expectations`의 재계산 결과와 정확히 같다(14개, 기존 7개 값 불변). 파일이 키 정렬·끝 개행 형식이다.
- `m3-dev-supersedes.json`의 sha256이 현재 이전 결과 파일과 일치하고 이전 run_id 디렉토리가 존재한다.
- `hypotheses-m3.md`가 필수 항목(위 1~13의 핵심 문구)을 모두 포함한다(문구 일치 테스트 대신 항목별 키워드 확인).
- 이 세 파일을 `guard_live_m3`가 읽는 경로 상수와 일치하게 둔다(경로 상수 테스트).
- 14개 골든 기록 파일(`data/replay/llm/`·`a_run/`)은 만들지 않는다.

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
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 기존 `docs/results/hypotheses.md`를 수정하지 마라. 이유: 사전 등록 파일이며 바꾸면 새 등록이다. 새 등록은 별도 파일로 쌓는다(FP-043).
- 기록·결과를 본 뒤에 기대값을 조정하는 문구·코드를 쓰지 마라(FP-043). 기대값은 명세에서 결정론으로 계산한다.
- 이전 결과 파일의 sha256·커밋을 손으로 적지 마라. 이유: 계산값만 증거가 된다.
- 이전 dev 7개 결과 디렉토리를 수정하거나 지우지 마라.
- `data/replay/llm/`·`a_run/`·`status.json`을 만들거나 수정하지 마라. 이유: live 러너가 만든다.
- 등록 파일에 목표 수치(TCR·정확도·지연)를 넣지 마라. 이유: dev 실행 뒤 test 전에 별도 등록한다.
- 이 step의 산출물 세 파일을 서로 다른 step에서 나누어 만들지 마라(같은 커밋 규칙).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

# Step 8: runner-docs

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- `/docs/PRD.md`, `/AGENTS.md`(명령어 블록), `/Makefile`, `/tests/test_docs_consistency.py`, `/tests/test_makefile.py`
- 이전 step 산출물 전부: `/src/govops/evalrun/`(observe·mock_admin·driver·preflight·dev_m3), `/src/govops/golden.py`, `/docs/results/hypotheses-m3.md`, `/docs/results/m3-dev-supersedes.json`

## 작업

이 phase에서 확정된 결정을 문서에 반영한다. 코드는 수정하지 않는다. 문서에는 **결정 내용만** 쓴다(누가 정했는지·변경 경위는 쓰지 않는다).

- **ADR-020 추가** (`docs/ADR.md` 끝, 기존 서식, 상태 "유효", 날짜는 오늘): 제목 "M3 결정 — dev 러너 m3, 골든 확장, 사전 등록". 항목: ① dev 20은 `WITH_WRITES` 단일 프로필로 처음부터 실행하고 러너가 프로필을 스스로 강제한다(`profile_mismatch`) ② 이전 7개 결과 보존(`m3-dev-supersedes.json`) ③ 관측 상태 매핑과 승인 계획 없이 승인 대기로 끝난 task는 `failed`, `approval_rejected`는 받아들여진 reject 기록 필요 ④ 절대 규칙은 mock 상태 기준으로 중복·누락·미승인·마커 없음을 함께 판정 ⑤ 승인자 선택 기준(`same_team_g4` 사전순 첫 번째) ⑥ preflight 항목과 이유 코드 ⑦ live dev는 Postgres 필수 ⑧ in-process ASGI 평가 경로와 한계 ⑨ record-through는 골든 14개만, 골든 대상 확장과 `profile` 필드, 재생 비교에서 승인 ID·mock ID 제외 ⑩ 등록 규칙: 등록 커밋 이후 `hypotheses-m3.md`·`expectations.json`·`m3-dev-supersedes.json`이 바뀌지 않음(`git diff <등록 커밋>..HEAD`가 비어 있음) ⑪ V15 오차단 정의 ⑫ 모델이 섞인 task 수와 TCR 보고 ⑬ 도구 호출 캡처는 task별로 분리하고(늦은 호출은 `late_tool_calls`로만 센다), `tool_trace_mismatch`(캡처한 도구 호출 수와 checkpoint의 도구 호출 수 불일치)가 있는 task는 N 계산에서 빼며 하나라도 있으면 결과 문서 첫머리에 적고 원인을 확인하기 전에는 N을 등록하지 않는다(`n_registration_blocked`) ⑭ 실행 환경이 낡은 채 실행하지 않도록 preflight가 막는다(옛 mock 이미지·LiteLLM 설정 미반영) ⑮ 평가 실행기는 `audit`·`guardrails`를 직접 import하지 않고 `api.wiring.build_task_service`로 서비스를 조립한다.
- `docs/ARCHITECTURE.md`: 디렉토리 구조와 평가 실행기 설명에 새 모듈(`evalrun/observe.py`·`mock_admin.py`·`driver.py`·`preflight.py`·`dev_m3.py`)을 추가하고, golden trace 설명을 14개·`profile`·재생 비교 제외 항목에 맞게 고친다.
- `docs/EVAL.md`: golden trace 절의 "골든 대상은 … 7개로 고정"을 "기존 7개는 `hypotheses.md`, 확장 14개는 `hypotheses-m3.md`의 등록을 따른다"로 고친다(기존 사전 등록 문장은 수정하지 않고 참조로). "현재 LiteLLM 설정에 fallback이 없으므로 M2 dev는 주 모델만 쓴다"는 M2 시점 설명임을 명시한다. 나머지 dev 13개 문장은 "dev 20 전체를 `WITH_WRITES`로 재실행한다"로 갱신한다.
- `docs/EVAL.md`의 탐색 예산 절(ADR-014)에는 "`tool_trace_mismatch`가 있는 task는 N 계산에서 빼고, 하나라도 있으면 원인을 확인하기 전에는 N을 등록하지 않는다"를 추가한다(N 산출 규칙 자체는 바꾸지 않는다).
- `docs/ARCHITECTURE.md`에는 `evalrun/tool_trace.py`와 `api/wiring.py`의 `build_task_service`를 추가한다.
- `docs/PRD.md`: M3 행의 측정에 "dev 20 TCR, 모델이 섞인 task 수·TCR"이 이미 있으면 유지하고 없으면 추가한다.
- `AGENTS.md`: **명령어 블록만** 수정한다: `make smoke-approval`을 추가하고 `make eval-run-dev` 설명을 "live task·record-through·채점" → "dev 20 `WITH_WRITES` live 실행·골든 record-through·채점·절대 규칙 판정(사용자 실행)"으로 바꾼다.
- `tests/test_docs_consistency.py`·`tests/test_makefile.py`가 통과해야 한다(테스트를 약화하지 말고 문서·Makefile을 맞춘다).

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

- 결정 주체나 변경 경위를 문서에 쓰지 마라. 이유: 문서에는 결정 내용만 쓴다.
- `docs/results/hypotheses.md`와 `hypotheses-m3.md`를 수정하지 마라. 이유: 사전 등록 파일이다(FP-043).
- 장애 주입(F1~F11)의 표본 수·기대 결과를 이 step에서 정하지 마라. 이유: `m3-faults`의 `hypotheses-m3-faults.md`에서 등록한다.
- `AGENTS.md`는 명령어 블록 외를 수정하지 마라.
- 코드·테스트를 수정하지 마라(문서 일관성 테스트 갱신이 필요하면 이유를 summary에 적는다).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

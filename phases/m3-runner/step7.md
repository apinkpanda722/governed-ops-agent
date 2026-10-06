# Step 7: smoke-approval

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/dev_m3.py`(`--smoke`·`run_dev_m3`·`run_preflight`), `/src/govops/evalrun/driver.py`, `/src/govops/evalrun/observe.py`, `/src/govops/api/smoke.py`(기존 smoke 출력 규칙), `/src/govops/gateway/smoke.py`(PASS/FAIL 줄 형식), `/data/eval/tasks.json`(평가 요청 문장, 읽기만), `/Makefile`, `/tests/test_makefile.py`, `/tests/test_api_smoke.py`

## 작업

사용자 실행 `make smoke-approval`을 만든다. 새 러너 경로(실제 API 코드·그래프·승인·mock)를 **평가와 무관한 합성 요청 2개**로 한 번 돌려 본다. 이 step에서 실제 호출은 하지 않는다. TDD로 테스트를 먼저 쓴다.

### 동작 (`python -m govops.evalrun.dev_m3 --smoke`)

- 전제: live A MCP·LiteLLM(`WITH_WRITES`는 러너가 스스로 강제)·compose mock·Postgres·`.env`. 먼저 `run_preflight`를 실행해 실패 이유 코드를 출력하고 시작하지 않는다(같은 코드 재사용).
- **요청 2개**(PRD 데모 장면 1: 플랫폼개발팀 3급 `demo-platform-g3`의 `INC-2026-012` 요약 발송): ① 승인 시나리오: "INC-2026-012 장애보고 요약해서 team-platform에 올려줘" → 승인(`requester`, approve) ② 거부 시나리오: "INC-2026-012 장애보고 정리해서 team-platform에 공유해줘" → 거부(`requester`, reject). 합성 `TaskSpec`을 만들 때 `split="smoke"` 같은 평가와 겹치지 않는 표시를 쓴다(`select_tasks`가 dev·test와 섞이지 않음).
- **`tasks.json`의 어떤 요청 문장과도 같지 않다**는 것을 테스트로 고정한다(정규화한 문자열 비교, 평가 오염 방지).
- 각 시나리오 사이에 mock `reset`. 판정(PASS/FAIL 줄만 출력, 본문·키·URL 없음):
  - 승인: 최종 `completed`, mock 상태의 `team-platform`에 효과 정확히 1건, 그 효과의 마커에 `govops_approval`, 승인 기록 approve 2xx → `PASS approve effect=1`.
  - 거부: 최종 `rejected`, 효과 0, 승인 기록 reject 2xx → `PASS reject effect=0`.
  - **막혔을 때**(`blocked`): 차단 사유 코드를 출력해 구분한다: `BLOCKED reason=<recipient_not_allowed|pii_blocked_by_team_policy|scope_wider_than_evidence|evidence_scope_unknown|ticket_not_found|…>`(뷰의 `check.reason`·`error` 코드만, 문구·본문 없음). 사유가 있어도 시나리오는 `FAIL`이고 종료 코드 1이지만, V15 등 결정론 검사의 차단인지 에이전트 오류인지 줄에서 구분된다.
  - 실패 줄 형식: `FAIL <scenario> stage=<preflight|run|approve|state> status=<…> error=<예외 클래스> reason=<코드>`.
- 합계: 두 시나리오 PASS면 종료 코드 0. 비용(원장 증가, 센트 단위)을 마지막 줄에 출력한다.
- smoke는 `data/results/`·`data/replay/`에 아무것도 쓰지 않고 `hypotheses-m3.md` 가드도 요구하지 않는다(평가 결과로 채택하지 않는다).
- `Makefile` 타깃 `smoke-approval`(`.PHONY`, 주석: "live A·LiteLLM·compose mock·Postgres·`.env` 필요, 실제 호출 수 센트, 사용자 실행"). `make verify`에는 넣지 않는다.

### 테스트 (가짜 chat·in-process mock·가짜 실행기)

- 승인·거부 PASS 출력, V15 차단 시 `BLOCKED reason=scope_wider_than_evidence` 출력과 종료 코드 1, preflight 실패 코드 출력과 미시작, 출력 전체에 키·`Authorization`·본문이 없음, `tasks.json` 요청과의 불일치, 기록 디렉토리에 아무 파일도 쓰지 않음.

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

- 평가 task(`tasks.json`)의 요청 문장을 smoke에 쓰지 마라. 이유: dev·test를 결과 등록 전에 실행하면 평가가 오염된다. 문장이 겹치지 않음을 테스트로 고정한다.
- smoke 결과를 `data/results/`나 골든 기록으로 남기지 마라.
- 출력에 본문·키·URL·예외 메시지를 넣지 마라(FP-020의 진단 줄 원칙: 모델명·코드·정수만).
- smoke 합격을 중복 0 하나로 판정하지 마라(FP-040). 효과 수·승인 마커·최종 상태를 함께 본다.
- preflight를 건너뛰지 마라(옛 환경 실행 방지).
- 환경 변수는 `env_or`로 읽어라(FP-030).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

# Step 2: repo-rules

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` ("고정 제약"의 `.env`·토큰·`data/raw`·`data/interim` 관련 CRITICAL 항목)
- `/docs/ARCHITECTURE.md` ("디렉토리 구조"의 `data/`, `docs/results/` 부분)
- `/docs/ADR.md` (ADR-007: 비용 상한은 코드로 강제)
- `/.env.example` (이미 존재한다. 읽기만 하고 **내용을 다시 쓰지 않는다**), `/.gitignore`
- step 0·1 산출물: `/tests/test_smoke.py`, `/tests/test_architecture.py`, `/Makefile`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

저장소 규칙을 테스트로 고정한다. `.env.example`은 새로 만들지 않는다. 이미 있는 파일을 그대로 유지하고(step 3·5가 변수 이름을 덧붙인다), 이 step은 그 파일에 대한 **검사만** 추가한다. TDD로 테스트를 먼저 쓴다.

1. **디렉토리 골격**: `data/contract/`, `data/seed/`, `data/replay/`, `data/eval/`, `docs/results/`에 빈 `.gitkeep`을 만든다. `data/raw/`, `data/interim/`은 만들지 않는다(커밋 금지 영역).
2. **`tests/test_repo_rules.py`**:
   - `.env.example`의 변수 줄(주석·빈 줄 제외)이 모두 `NAME=` 형태로 값이 비어 있다. 정규식 예: `^[A-Z][A-Z0-9_]*=$`. 어긋나는 줄이 있으면 줄 번호를 메시지에 낸다(값 자체는 메시지에 출력하지 않는다).
   - `.env.example`에 비용 상한용 변수가 없다: 변수 이름에 `COST`, `BUDGET`, `LIMIT`, `CAP`이 들어가지 않는다. 이유: 비용 상한은 `.env`가 아니라 코드 설정(ADR-007 값)으로 둔다.
   - `git check-ignore`(서브프로세스, `-q`)로 `.env`, `data/raw/x`, `data/interim/x`, `web/node_modules`, `.venv`가 무시되고 `.env.example`은 무시되지 않는다.
   - 위 디렉토리 골격 5곳이 존재한다.

핵심 규칙:
- `.env`는 어떤 경우에도 열지 않는다. 테스트도 `.env`를 읽지 않는다(`git check-ignore`는 경로만 본다).
- `.env.example`에는 변수 이름과 주석만 둔다. 값을 넣지 마라.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest(tests, scripts)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `.env.example`이 변경 없이 유지됐는가? (`git diff -- .env.example`이 비어 있어야 한다)
   - `.gitignore`가 수정 없이 요구 항목을 이미 만족하는가? (만족하지 않을 때만 최소한으로 고친다)
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 이유: 비밀 값이 노출될 수 있다.
- `.env.example`을 새로 만들거나 덮어쓰거나 변수를 추가하지 마라. 이유: 사용자가 만든 파일을 유지해야 하고, 변수 추가는 step 3·5가 맡는다.
- 비용 상한 값(OpenAI·Anthropic·Gemini 금액)을 환경 변수로 두지 마라. 이유: ADR-007에 따라 코드 설정으로 고정한다.
- 설치·네트워크 명령을 실행하지 마라.
- `scripts/`, `phases/`, `web/`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

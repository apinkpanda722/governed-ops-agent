# Step 5: ci-workflow

## 읽어야 할 파일

- `/AGENTS.md`, `/Makefile`(`verify` 타깃), `/pyproject.toml`, `/.python-version`, `/uv.lock`, `/web/package.json`, `/web/pnpm-lock.yaml`(있으면), `/.gitignore`
- `/tests/test_makefile.py`, `/tests/test_repo_rules.py`(저장소 규칙 검사 방식)
- `/docs/ARCHITECTURE.md`·`/docs/EVAL.md`의 golden trace 절

## 작업

PR마다 `make verify`를 키 없이 도는 GitHub Actions 워크플로를 만든다. 이 저장소에는 `.github/workflows/`가 아직 없다. TDD로 검사 테스트를 먼저 쓴다.

### 만들 것 — `.github/workflows/ci.yml`

- 트리거: `pull_request`와 `push`(branches: `main`)만.
- `permissions: contents: read`(워크플로 최상위).
- 단일 job(`verify`, `runs-on: ubuntu-latest`): `actions/checkout`, uv 설치(`astral-sh/setup-uv`), Node·pnpm 설정(`actions/setup-node`, `pnpm/action-setup` — 버전은 `web/package.json`의 `packageManager`·`engines` 또는 `.node-version`/`.nvmrc`가 있으면 그것을 따르고 없으면 현재 로컬 버전을 코드에서 확인해 **고정**), `uv sync --frozen`, `pnpm -C web install --frozen-lockfile`, `make verify`.
- **모든 `uses:`는 커밋 SHA로 고정**하고 줄 끝 주석으로 사람이 읽는 태그를 적는다. 아래는 오케스트레이터가 GitHub API로 조회해 확인한 값이다(2026-10-05 기준 최신 릴리스). **이 값만 쓰고 다른 SHA를 추측하지 마라**:
  - `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`
  - `astral-sh/setup-uv@c18668ad3cf93ea998bef934396af7bb5c839dc7 # v10.2.0`
  - `actions/setup-node@820762786026740c76f36085b0efc47a31fe5020 # v7.0.0`
  - `pnpm/action-setup@ea17c68df8912ef543352723c149a84f56e3d413 # v6.1.0`
  버전 입력은 로컬과 맞춘다: Node `26.4.0`(`web/package.json` engines `>=26.4.0 <27`), pnpm `12.8.1`(`packageManager`), Python은 `.python-version`(3.12)을 uv가 따르게 하고, uv 버전은 `0.11.24`로 고정한다(`setup-uv`의 `version` 입력). 위 SHA가 Actions에서 해석되지 않는 문제는 로컬에서 검증할 수 없으므로 사용자가 첫 PR의 CI 결과로 확인한다(결과 보고에 적는다).
- secrets·API 키·`OPENAI_API_KEY`·`ANTHROPIC_API_KEY`·`GEMINI_API_KEY`·`LITELLM_*` 환경 변수를 어디에도 두지 않는다. `.env`를 만들거나 `secrets.*`·`env:`로 키를 주입하지 않는다.
- 네트워크가 필요한 단계는 의존성 설치뿐이다. live·compose·docker 단계는 없다.

### 테스트 (`tests/test_ci_workflow.py`)

- 파일이 있고 트리거가 `pull_request`와 `push`(main)뿐이다.
- 최상위 `permissions`가 `contents: read`뿐이다.
- 모든 `uses:`가 40자 hex 커밋 SHA로 고정되어 있다(태그만 쓴 것은 실패).
- `secrets.`, `OPENAI`, `ANTHROPIC`, `GEMINI`, `LITELLM`, `LANGFUSE`, `API_KEY`, `.env` 참조가 워크플로 어디에도 없다.
- 실행 단계에 `uv sync --frozen`, `pnpm ... install --frozen-lockfile`, `make verify`가 있고, `docker`·`compose`·`make serve`·`record`·`smoke`·`eval-run`·`eval-gemini` 같은 live 타깃이 없다.
- YAML이 유효하다(파싱된다).

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

- 위에 적힌 것 외의 action이나 SHA를 기억에서 지어내 쓰지 마라. 이유: 잘못된 SHA는 워크플로를 깨뜨리고, 고정의 목적(공급망 보호)도 해친다.
- 워크플로에 secrets나 API 키를 주입하지 마라. 이유: 골든 CI는 키 없이 돈다(ADR-009).
- `make verify`의 항목이나 순서를 바꾸지 마라. 이유: `tests/test_makefile.py`가 순서를 고정한다.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(AGENTS.md CRITICAL).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/tasks.json`, `data/eval/meta.json`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

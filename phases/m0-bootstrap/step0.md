# Step 0: python-config

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (기술 스택, 계층 경계 표, 고정 제약, 개발 프로세스)
- `/docs/ARCHITECTURE.md` ("디렉토리 구조" 절)
- `/docs/PRD.md` (마일스톤 표의 M0 행과 표 아래 `make verify` 문단)
- `/pyproject.toml`, `/src/govops/__init__.py`, `/Makefile`, `/.python-version`

`pyproject.toml`·`uv.lock`·`.python-version`·`src/govops/__init__.py`는 사용자가 `uv init --package --name govops --python 3.12`와 `uv add`로 미리 만들어 두었다. 의존성(fastapi·uvicorn·httpx, dev: ruff·pytest·pyyaml)은 이미 설치돼 있다.

## 작업

이 step의 범위는 "파이썬 설정 + 계층 패키지 골격 + `make verify`의 파이썬 부분"이다. TDD 규칙에 따라 테스트를 먼저 쓴다.

1. **테스트 먼저**: `tests/test_smoke.py`를 만든다. `importlib.import_module`로 `govops`와 계층 패키지 11개(`api`, `agent`, `tools`, `broker`, `gateway`, `guardrails`, `audit`, `obs`, `eval`, `replay`, `mocks`), 그리고 `govops.costs`를 import할 수 있는지 검사한다. 먼저 실행해 실패하는 것을 확인한다.
2. **계층 패키지 골격**: `src/govops/<계층>/__init__.py` 11개(내용은 한 줄 docstring으로 계층의 역할만 적는다)와 `src/govops/costs.py`(한 줄 docstring만, 로직 금지)를 만든다. 계층 이름과 역할은 `docs/ARCHITECTURE.md`의 디렉토리 구조를 그대로 따른다.
3. **uv init 잔여물 정리**: `uv init --package`가 만든 `src/govops/__init__.py`의 `main()` 함수와 `pyproject.toml`의 `[project.scripts]`(`govops = "govops:main"`) 항목을 제거한다. 사용자가 이 정리를 이 step에서 하도록 지정했다. `__init__.py`는 한 줄 docstring만 남긴다.
4. **`pyproject.toml` 정리**:
   - `description`을 프로젝트 설명으로 바꾼다("Add your description here" 제거).
   - `[tool.ruff]`: `extend-exclude = ["phases", "scripts", "web"]`.
   - `[tool.pytest.ini_options]`: `testpaths = ["tests", "scripts"]`. `scripts/`는 하네스 자체 테스트(`scripts/test_execute.py`)이므로 계속 돌려야 한다.
   - `requires-python`, `[build-system]`, 의존성 목록은 바꾸지 않는다.
5. **`Makefile`의 `verify` 타깃**을 아래로 교체하고, 상단 주석을 현재 상태에 맞게 고친다(web 검사는 step 7에서 추가한다고 적는다).
   ```
   verify:
   	uv run ruff check . && uv run ruff format --check . && uv run pytest -q
   ```
   (recipe 줄은 탭 들여쓰기)

핵심 규칙:
- 계층 패키지의 실제 로직은 만들지 않는다(M1 이후). 이 step은 import 가능한 빈 골격까지다.
- `ruff format --check .`가 통과하도록 새 파일은 ruff 포맷을 따른다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest(tests, scripts)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 계층 패키지 11개 + `costs.py`가 ARCHITECTURE.md 디렉토리 구조와 이름이 일치하는가?
   - `[project.scripts]`와 `main()`이 제거됐는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 이유: 비밀 값이 로그·트레이스에 남을 수 있다(AGENTS.md CRITICAL).
- `uv add`·`uv sync`·`pnpm install` 등 설치·네트워크 명령을 실행하지 마라. 이유: 설치는 사용자가 한다. 의존성은 이미 `.venv`에 있고 `uv run`으로 충분하다.
- `scripts/`, `phases/`, `docs/`, `web/`을 수정하지 마라. 이유: 하네스 파일과 문서 정리는 다른 step 소관이다.
- 의존성 목록이나 `requires-python`을 바꾸지 마라. 이유: 사용자가 고정한 값이다.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

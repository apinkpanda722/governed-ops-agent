# Step 1: arch-guard

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` ("계층 경계" 표가 이 step의 규칙 원본이다)
- `/docs/ARCHITECTURE.md` ("디렉토리 구조" 절)
- `/docs/ADR.md` (ADR-004·ADR-007·ADR-008 중 계층·게이트웨이 관련 부분)
- step 0 산출물: `/pyproject.toml`, `/Makefile`, `/src/govops/` 아래 11개 계층 `__init__.py`, `/src/govops/costs.py`, `/tests/test_smoke.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

AGENTS.md "계층 경계" 표의 "import 금지"를 `tests/test_architecture.py`로 강제한다. 이 step은 **검사기와 그 테스트만** 만든다. TDD로, 검사기가 위반을 실제로 잡는지를 보이는 테스트를 먼저 쓴다.

### 검사기 인터페이스 (테스트 파일 안에 두거나 `tests/` 아래 헬퍼 모듈)

```python
def find_violations(src_root: Path) -> list[Violation]: ...
# Violation: 어느 파일(상대 경로), 계층, 금지된 import 대상
```

- `src_root`(= `src/govops`)의 각 계층 디렉토리 아래 `*.py`를 `ast`로 파싱해 `import x`, `from x import y`, 상대 import(`from ..broker import z`, `from .. import broker`)를 모두 수집한다. 상대 import는 `govops.<계층>`으로 해석해 검사한다.
- 금지 규칙은 AGENTS.md 표를 그대로 옮긴 딕셔너리로 둔다. 금지 대상은 최상위 모듈 단위 비교(`govops.<계층>`, `openai`, `anthropic`, `google.genai`, `litellm`, `langgraph`, `fastapi`)이며 하위 모듈도 포함해서 판정한다.
  - `api`: `broker`, `mocks`, LLM SDK
  - `agent`: `broker`, `mocks`, `eval`, LLM SDK
  - `tools`: `agent`, `api`, `eval`, `mocks`, `langgraph`, LLM SDK
  - `broker`: `agent`, `api`, `tools`, `gateway`, `obs`, `eval`, `langgraph`
  - `gateway`: `agent`, `tools`, `broker`, `api`
  - `guardrails`: `litellm`, `langgraph`, `fastapi`, 자기 자신을 뺀 govops 계층 전부
  - `audit`: `agent`, `tools`, `broker`, `api`
  - `obs`: `broker`
  - `eval`: `gateway`, `broker`
  - `mocks`: 자기 자신을 뺀 govops 계층 전부
  - `replay`: `agent`, `api`, `broker`
  - LLM SDK = `openai`, `anthropic`, `google.genai`, `litellm`
- 계층 안에서 자기 계층 하위 모듈끼리 import하는 것은 허용한다.
- `costs.py`(계층이 아님)는 검사 대상이 아니다.

### 테스트

1. 실제 `src/govops` 트리에 위반이 0건이다.
2. 규칙 딕셔너리의 계층 목록이 `src/govops/` 아래 실제 계층 디렉토리와 일치한다(계층이 추가·삭제되면 규칙 갱신을 강제하려는 것이다).
3. `tmp_path`에 만든 가짜 트리로 **금지 import가 실제로 탐지되는지** 검사한다. 최소 포함: 절대 import(`import govops.broker`), `from govops.broker import x`, 상대 import, LLM SDK(`import openai`), `from google.genai import x`, `guardrails`의 `fastapi` import, `mocks`가 다른 계층을 import하는 경우. 허용되는 import(`gateway`가 `litellm`을 import, 자기 계층 안 import)는 위반이 아님도 검사한다.

핵심 규칙:
- `gateway`는 LLM SDK·`litellm` import가 허용된다(표에 금지가 없다). `eval`도 Gemini 직접 호출을 위해 `google.genai`가 허용된다. 이 둘을 금지로 잘못 옮기지 마라.
- 검사기는 import 문을 실행하지 않고 AST로만 읽는다(외부 패키지 설치 여부와 무관하게 동작해야 한다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest(tests, scripts)
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 규칙 딕셔너리가 AGENTS.md "계층 경계" 표와 한 줄도 어긋나지 않는가?
   - 가짜 트리 테스트가 위반 탐지와 허용 import를 둘 다 다루는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `src/govops/` 계층 패키지에 로직이나 import를 추가하지 마라. 이유: 이 step은 검사기만 만든다. 계층 골격은 step 0 상태 그대로 둔다.
- AGENTS.md의 계층 표를 수정하지 마라. 이유: 표가 원본이고 테스트가 그것을 따른다.
- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 설치·네트워크 명령을 실행하지 마라. 이유: 설치는 사용자가 한다.
- `scripts/`, `phases/`, `web/`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

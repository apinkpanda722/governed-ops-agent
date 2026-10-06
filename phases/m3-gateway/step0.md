# Step 0: env-helper

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-007·008·009·018), `/docs/ARCHITECTURE.md` ("게이트웨이와 Guardrail 비대칭 구조", "상태 관리"의 비용 원장), `/docs/THREAT_MODEL.md` (F6)
- `/src/govops/tools/smoke.py`, `/src/govops/audit/log.py`, `/src/govops/evalrun/dev.py`, `/src/govops/obs/jsonl.py`, `/src/govops/mocks/__main__.py`, `/src/govops/api/wiring.py`, `/src/govops/api/cli.py`, `/Makefile`, `/tests/test_architecture.py`, `/tests/test_smoke_tools.py`, `/tests/test_makefile.py`

## 작업

**빈 환경 변수가 기본값을 덮는 결함**을 고친다. `.env`에 `KEY=`처럼 비어 있게 두면 `os.environ.get("KEY", 기본값)`은 빈 문자열을 돌려줘 기본값이 쓰이지 않는다. 또 `smoke-effects`의 실패 진단 출력을 만든다. TDD로 테스트를 먼저 쓴다.

### 1. `src/govops/env.py` (최상위 모듈, 어느 계층에도 속하지 않는다)

```python
def env_or(name: str, default: str) -> str     # 미설정·빈 문자열·공백뿐인 값은 미설정으로 보고 default를 돌려준다. 값이 있으면 앞뒤 공백을 제거해 돌려준다
```

- 아래 변수의 읽기를 `env_or`로 바꾼다: `tools/smoke.py`(`MOCK_JIRA_URL`·`MOCK_SLACK_URL`·`SMOKE_USER`), `audit/log.py`(`AUDIT_DIR`), `evalrun/dev.py`(`A_ROOT`), `obs/jsonl.py`(`GOVOPS_TRACE_DIR`), `mocks/__main__.py`(`MOCK_JIRA_TICKETS`).
- 위 목록 밖에서도 같은 패턴(`os.environ.get(이름, 비어 있지 않은 기본값)`, `os.getenv(이름, 비어 있지 않은 기본값)`, `os.getenv(이름) or "<기본값 상수>"`)이 있으면 모두 `env_or`로 바꾼다(`api/wiring.py`의 `MOCK_JIRA_URL`·`MOCK_SLACK_URL` 등). 기본값이 없는 읽기(`os.environ["X"]`, `os.getenv("X")`)와 기본값이 `None`/`""`인 읽기는 그대로 둔다.
- `env.py`는 `govops`의 다른 모듈을 import하지 않는다(계층 가드 영향 없음).

### 2. 테스트

- **회귀 테스트(변수별)**: 위 5개 변수를 각각 빈 문자열·공백뿐인 값으로 설정했을 때 기본값이 쓰이고, 값이 있으면 그 값이 쓰인다(`monkeypatch.setenv`).
- **스캔 테스트** `tests/test_env_defaults.py`: `src/govops/` 전체를 AST로 훑어 `os.environ.get`·`os.getenv` 호출에 **비어 있지 않은 문자열 상수 기본값**을 넘기거나 `os.getenv(...) or "<비어 있지 않은 상수>"` 형태를 쓰면 실패한다. 허용 목록은 두지 않는다(예외가 필요하면 `env_or`로 바꾼다).
- `env_or` 단위 테스트(미설정·빈값·공백·값 있음·값 주변 공백).

### 3. `smoke-effects` 실패 진단

`tools/smoke.py`의 `effects` 하위 명령이 실패하면 한 줄로 낸다: `FAIL <tool> stage=<send|lookup|repeat|count> state=<outbox 상태> reason=<이유 코드> error=<예외 클래스 이름> status=<HTTP 상태 또는 kind>`. 필드는 모두 코드 상수·예외 **클래스 이름**·정수·kind뿐이다. **본문·토큰·URL·헤더·예외 메시지를 넣지 않는다.** 성공 출력(`PASS`)은 그대로 둔다. 가짜 transport로 단계별 실패(발송 실패·조회 실패·재실행 시 중복·건수 불일치)를 만들어 출력 형식과 비밀 비노출을 테스트한다.

### 4. 문서 한 줄

`docs/ARCHITECTURE.md`의 "배치도" mock 관리 항목에 다음을 추가한다: "mock 코드가 바뀐 PR을 머지한 뒤에는 `docker compose -f infra/docker-compose.yml up -d --build mock-jira mock-slack`으로 mock 이미지를 다시 빌드한다." `Makefile`의 mock 관련 타깃 근처 주석에도 같은 안내를 한 줄 둔다. 결정 내용만 쓴다(경위 금지).

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

- `env_or` 허용 목록 예외를 스캔 테스트에 넣지 마라. 이유: 예외가 생기면 같은 결함이 다시 들어온다.
- 기본값이 없는 필수 환경 변수 읽기(`os.environ["KEY"]`)의 동작을 바꾸지 마라. 이유: 누락을 숨기면 안 된다.
- 진단 출력에 예외 메시지·URL·본문을 넣지 마라. 이유: 비밀·본문이 섞일 수 있다.
- 이 step에서 mock 코드의 동작을 바꾸지 마라(`MOCK_JIRA_TICKETS` 읽기 방식만 바꾼다).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

# Step 1: mock-admin

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-010·011·018·019), `/docs/EVAL.md` ("TCR", "golden trace", "재실행 규칙"), `/docs/ARCHITECTURE.md`("에이전트 상태 전이"), `/docs/results/hypotheses.md`(고정 문장 5·§9·§10), `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/dev.py`의 `mock_seed_check`(관리 CLI 호출 방식), `/src/govops/mocks/admin.py`(`health`·`state [--bodies]`·`reset`·`faults set/clear/stats`), `/infra/docker-compose.yml`, `/docs/ARCHITECTURE.md`(mock 관리 접근), `/tests/test_evalrun_dev.py`

## 작업

mock 관리 CLI를 호출하는 **래퍼**를 만든다. 관리 포트(9000)는 publish하지 않으므로 호스트 접근 경로는 `docker compose ... exec`뿐이다(ADR-016). `evalrun/`은 `mocks`를 import할 수 없으므로 서브프로세스로 부른다. TDD로 테스트를 먼저 쓴다.

### `src/govops/evalrun/mock_admin.py`

```python
class MockAdmin:
    def __init__(self, root: Path, *, runner: Runner = subprocess_runner): ...
    def reset(self) -> None                       # mock-jira·mock-slack 모두 초기화
    def state(self, *, bodies: bool = False) -> dict   # {"jira": {...}, "slack": {...}}
    def faults_clear(self) -> None
    def faults_stats(self) -> dict
    def health(self) -> dict[str, bool]           # {"mock-jira": bool, "mock-slack": bool}
```

- 명령 형식은 `mock_seed_check`와 같다: `docker compose --env-file .env -f infra/docker-compose.yml exec -T <mock-jira|mock-slack> python -m govops.mocks.admin <명령> [옵션]`(`cwd=root`). `.env`는 Docker가 읽는 것이지 이 코드가 열지 않는다.
- `runner`는 주입한다(기본은 `subprocess.run` 래퍼, 테스트는 가짜). 출력은 JSON으로 파싱한다. 종료 코드 ≠ 0이거나 JSON이 아니면 `MockAdminError(code)`(코드 상수: `command_failed`·`invalid_output`). **예외 메시지·stderr·stdout 원문을 올리지 않는다.**
- `evalrun/dev.py`의 M2 `mock_seed_check`는 수정하지 않는다(M2 러너와 테스트 유지).

### 테스트 (가짜 실행기)

- 명령 구성(서비스 이름·`--bodies`·`reset`), 두 mock 결과 합치기, 실패 종료 코드·잘못된 JSON 처리, 오류에 출력 원문이 없음, `health`의 부분 실패.
- 실제 Docker·네트워크를 부르는 테스트를 만들지 않는다.

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

- 관리 포트를 publish하거나 REST 포트로 관리 명령을 보내지 마라. 이유: mock 관리 엔드포인트는 compose 내부 전용이다(AGENTS.md CRITICAL).
- 서브프로세스 출력 원문·예외 메시지를 올리거나 로그에 남기지 마라. 이유: 비밀·본문이 섞일 수 있다.
- 실제 `docker`를 호출하는 테스트를 만들지 마라(주입한 가짜 실행기만).
- `evalrun/`에서 `govops.mocks`를 import하지 마라. 이유: 계층 경계다(서브프로세스로만 접근).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(외부 명령은 주입한 가짜 실행기로 검증한다).
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.

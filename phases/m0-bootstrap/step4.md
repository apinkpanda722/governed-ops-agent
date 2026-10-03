# Step 4: mock-skeleton

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `mocks` 행, "아키텍처 규칙"의 mock 관리 엔드포인트 항목)
- `/docs/ARCHITECTURE.md` ("배치도" 아래 설명, "디렉토리 구조"의 `mocks/`·`infra/`)
- `/docs/ADR.md` (ADR-006: mock은 REST 서비스, ADR-004)
- step 3 산출물: `/infra/docker-compose.yml`, `/tests/test_compose.py`, `/.env.example`
- step 0·1 산출물: `/src/govops/mocks/__init__.py`, `/tests/test_architecture.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

mock-jira·mock-slack의 **서비스 골격과 관리 CLI 골격**만 만든다. 티켓·메시지 API, 시드, 장애 주입 로직은 M1·M3이다. TDD로 테스트를 먼저 쓴다.

### 설계 (사용자가 확정한 접근 경로)

- 각 mock은 **REST 포트(컨테이너 8000)** 와 **관리 포트(컨테이너 9000)** 를 따로 가진다. REST 포트만 호스트에 `127.0.0.1`로 publish한다(호스트의 에이전트 서비스가 쓴다). **관리 포트는 publish하지 않는다**(compose `expose`만 허용).
- 호스트에서의 관리 접근 경로는 컨테이너 안 CLI다: `docker compose exec <mock> python -m govops.mocks.admin <명령>`. 평가 하네스의 상태 조회와 M3 장애 주입이 이 경로를 쓴다.

### 만들 파일

1. **`src/govops/mocks/service.py`** (또는 jira/slack 하위 모듈): 서비스 이름(`jira`/`slack`)을 받아 FastAPI 앱 2개를 만드는 함수.
   ```python
   def create_rest_app(service: str) -> FastAPI   # GET /healthz -> {"status": "ok", "service": "mock-<name>"}
   def create_admin_app(service: str) -> FastAPI  # GET /healthz 만. 상태 조회·초기화·장애 주입은 M1·M3
   ```
2. **`src/govops/mocks/__main__.py`**: `python -m govops.mocks <jira|slack>`으로 REST 앱(8000)과 관리 앱(9000)을 한 프로세스에서 둘 다 띄운다(uvicorn, 호스트 바인드 `0.0.0.0`은 컨테이너 안에서만 의미가 있다).
3. **`src/govops/mocks/admin.py`**: 관리 CLI 진입점.
   ```python
   def main(argv: list[str] | None = None, *, transport: httpx.BaseTransport | None = None) -> int
   ```
   인자 파싱(`argparse`, 옵션 `--url` 기본 `http://127.0.0.1:9000`, 서브커맨드 `health`)과 관리 포트 `/healthz` 호출만 구현한다. 성공 시 응답 본문을 출력하고 종료 코드 0, 연결 실패·비정상 응답은 메시지와 1. `python -m govops.mocks.admin`으로 실행 가능해야 한다(`if __name__ == "__main__"`).
4. **`infra/mocks/Dockerfile`**: `python:3.12-slim` 계열 고정 태그 위에 `uv`로 프로젝트를 설치하고(`uv sync --frozen --no-dev` 방식), 기본 CMD는 지정하지 않는다(compose가 `command`로 서비스 이름을 준다). 베이스 이미지·uv 이미지 줄 위에 `확인 필요(첫 기동 전 사용자가 공식 릴리스 노트로 확인)` 주석을 단다. 빌드 컨텍스트는 저장소 루트이므로 `.dockerignore`를 루트에 만들어 `.venv`, `web/`, `data/`, `.git`, `.env*`를 제외한다.
5. **`infra/docker-compose.yml`에 서비스 추가**: `mock-jira`, `mock-slack`. `build: {context: .., dockerfile: infra/mocks/Dockerfile}`, `command: ["python", "-m", "govops.mocks", "jira"]`(slack도 동일 형식), REST 포트만 `127.0.0.1:8081:8000` / `127.0.0.1:8082:8000`으로 publish, `expose: ["9000"]`. 환경 변수는 필요 없다(필요해지면 `.env.example`에 이름도 추가한다).

### 테스트

- `tests/test_mocks.py`: FastAPI `TestClient`로 두 앱(jira·slack × rest·admin)의 `/healthz` 응답을 검사한다. `admin.main(["health"], transport=httpx.MockTransport(...))`의 성공(출력·종료 코드 0)과 실패(연결 오류·500 → 종료 코드 1), 잘못된 서브커맨드(argparse가 종료)를 검사한다.
- `tests/test_compose.py` 확장: 서비스 `mock-jira`·`mock-slack`이 있고, 어떤 서비스도 컨테이너 포트 `9000`을 `ports`로 publish하지 않으며(관리 포트 비공개), `expose`에 9000이 있다. step 3의 공통 검사(127.0.0.1 바인드, `${VAR}` ⊆ `.env.example`)가 이 서비스에도 적용된다.
- `tests/test_architecture.py`가 계속 통과해야 한다: `govops.mocks`는 다른 govops 계층을 import하지 않는다.

핵심 규칙:
- 관리 엔드포인트는 호스트에 publish하지 않는다(AGENTS.md CRITICAL). `ports`에 9000을 절대 넣지 마라.
- `mocks`는 외부 시스템 역할이다. `govops`의 다른 계층(`agent`, `tools`, `broker` 등)을 import하지 않는다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest(tests, scripts). docker는 실행하지 않는다
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 관리 포트가 compose에서 publish되지 않고 CLI 경로(`docker compose exec ... python -m govops.mocks.admin`)로만 접근하도록 설계됐는가?
   - 티켓·메시지 API, 장애 주입 로직을 만들지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `docker`, `docker compose`를 실행하거나 이미지를 빌드하지 마라. 이유: 빌드·기동은 사용자가 한다.
- 관리 포트(9000)를 `ports`로 publish하지 마라. 이유: 관리 엔드포인트는 compose 내부 네트워크에만 연다(AGENTS.md CRITICAL).
- mock에 실제 업무 API(티켓 조회, 메시지 발송), 시드 데이터, 장애 주입, 상태 초기화를 구현하지 마라. 이유: M1·M3 범위다.
- 실제 서버를 포트에 바인드하는 테스트를 만들지 마라. 이유: 테스트는 `TestClient`와 `MockTransport`만 쓴다.
- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- 설치·네트워크 명령을 실행하지 마라.
- `scripts/`, `phases/`, `web/`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

# Step 5: api-sessions

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `api` 행, 아키텍처 규칙의 "데모 로그인은 `DEMO_LOGIN` 플래그일 때만 열고 127.0.0.1에만 바인드", "A 계약 스냅샷 … 시작 시 비교해 다르면 중단", 신원은 인증된 세션에서만)
- `/docs/ARCHITECTURE.md` ("BFF 인증 흐름", "자격 증명 중개 흐름" 시퀀스의 API 부분)
- `/docs/ADR.md` (ADR-005 신뢰 경계, ADR-011 API, ADR-017의 B 세션 토큰 결정)
- `/docs/PRD.md` (핵심 기능 1·8의 세션)
- m1-tools 산출물: `/src/govops/tools/a_mcp.py`(`AMcpClient`, `check_contract`), `/src/govops/tools/jira.py`, `/src/govops/tools/slack.py`, `/data/seed/users.json`(사용자 38명 label·team·grade, 커밋됨), `/tests/conftest.py`, `/tests/test_a_mcp_client.py`
- `/.env.example`(`DEMO_LOGIN`의 값 형식 `true`), `/src/govops/api/__init__.py`, `/tests/test_architecture.py`

## 작업

B 세션 토큰 발급·검증과 API 앱 골격을 만든다. 신원은 이 세션에서만 정해진다. TDD로 테스트를 먼저 쓴다. task·승인 엔드포인트는 다음 step이다.

### 설계 (ADR-017 확정)

- `POST /sessions`는 **`DEMO_LOGIN` 플래그가 켜졌을 때만 존재**한다(플래그는 환경 변수 `DEMO_LOGIN`이 대소문자 무시 `true`일 때만 켜짐. 그 밖의 모든 값·미설정은 꺼짐). 꺼져 있으면 라우트 자체가 없어 404이다.
- 켜져 있어도 **클라이언트 주소가 루프백(`127.0.0.1`, `::1`)이 아니면 403**이다. 서버 실행은 항상 `127.0.0.1`에만 바인드하고 호스트 바인드 옵션을 만들지 않는다.
- 토큰은 `secrets.token_urlsafe(32)`. **서버는 SHA-256 해시만 메모리에 두고** 사용자 ID·만료 시각에 매핑한다(재시작하면 재로그인). 평문 토큰은 발급 응답 한 번뿐이다. TTL은 임시 상수(8시간)이며 최종값은 M5에서 정한다(코드 주석에 적는다).
- 로그인 대상 사용자는 `data/seed/users.json`의 label 목록이다. `api`는 `broker`를 import할 수 없으므로 이 파일을 직접 읽는다. 목록에 없는 사용자는 404.

### 만들 파일

1. **`src/govops/api/users.py`**: `load_users(path: Path | None = None) -> frozenset[str]` — `users.json`(`{"users": [{"label","team","grade"}]}`)에서 label 집합. 형식 오류·빈 목록은 예외.
2. **`src/govops/api/sessions.py`**
   ```python
   SESSION_TTL_SECONDS = 8 * 3600   # 임시값, M5에서 확정
   class SessionStore:
       def __init__(self, *, ttl_seconds: int = SESSION_TTL_SECONDS, clock: Callable[[], float] = time.time)
       def issue(self, user_id: str) -> tuple[str, float]      # (평문 토큰, 만료 epoch)
       def resolve(self, token: str) -> str | None              # 해시 조회, 만료·미등록이면 None
   ```
   스토어 내부 어디에도 평문 토큰이 남지 않는다(`vars`·`repr` 포함). 만료된 항목은 조회 시 제거한다.
3. **`src/govops/api/auth.py`**: FastAPI 의존성 `current_user(request) -> str` — `Authorization: Bearer <토큰>`(헤더는 정확히 하나)에서 `SessionStore.resolve`로 `user_id`를 얻는다. 없음·형식 오류·만료·미등록은 401(`WWW-Authenticate: Bearer`)이고 본문은 모두 같다.
4. **`src/govops/api/app.py`**
   ```python
   def create_app(*, users: frozenset[str], sessions: SessionStore, demo_login: bool,
                  startup_checks: Sequence[Callable[[], Awaitable[None]]] = ()) -> FastAPI
   ```
   - `POST /sessions` 본문 `{"user": <label>}` → 201 `{"session_token": ..., "expires_at": ...}`(demo_login일 때만 등록).
   - `GET /sessions/me`(인증 필요) → `{"user_id": ...}`.
   - 앱 시작(lifespan)에서 `startup_checks`를 순서대로 `await`하고, 하나라도 예외를 내면 시작을 중단한다(서버가 뜨지 않는다).
   - 응답·로그에 토큰이 평문으로 나타나는 곳은 **로그인 응답 본문 하나뿐**이다.
5. **`src/govops/api/startup.py`**: `async def verify_contract(client: AMcpClient) -> None` — `tools.a_mcp.check_contract`를 호출하고 불일치(`ContractMismatchError`)·자격 증명 오류·연결 오류를 `StartupError`(메시지에 토큰·헤더 없음, 원인 종류만)로 바꿔 올린다.
6. **`src/govops/api/__main__.py`**: `python -m govops.api [--port 8000]`. 호스트는 `127.0.0.1` **고정**이고 옵션이 없다. `DEMO_LOGIN` 플래그를 읽어 `create_app`에 넘기고, `startup_checks`에 `verify_contract(AMcpClient(KODOCS_MCP_URL))`를 넣는다. 실제 task 연결은 다음 step이므로 이 step에서는 기동 가능한 최소 구성(세션만)까지만 한다. uvicorn 설정을 만드는 함수(`build_server_config(port)`)를 분리해 테스트할 수 있게 한다.

### 테스트 (`tests/test_api_sessions.py`)

`fastapi.testclient.TestClient`(루프백 클라이언트 주소는 `client=("127.0.0.1", 50000)` 인자로 지정)를 쓴다. 가짜 A가 필요한 테스트는 `serve_asgi`와 임시 토큰 파일을 쓴다.
- `demo_login=False`면 `POST /sessions`가 404(OpenAPI에도 경로가 없다). `True`면 알려진 사용자에 201, 알 수 없는 사용자 404, 본문 형식 오류 422.
- 비루프백 클라이언트(`client=("10.0.0.5", 1)`)는 403이고 세션이 만들어지지 않는다.
- 발급된 토큰으로 `GET /sessions/me`가 사용자 ID를 돌려주고, 두 사용자의 토큰이 각자의 ID로 해석된다. 헤더 없음·`Bearer`가 아닌 스킴·토큰 두 개·미등록·만료(주입 시계로 TTL 경과) → 401이고 응답 본문이 모두 같다.
- **해시만 보관**: 발급 후 `SessionStore`의 `vars`/`repr`/내부 딕셔너리 어디에도 평문 토큰이 없다. 토큰이 로그인 응답 외의 모든 응답 본문·헤더, `caplog`(DEBUG), `capsys`에 나타나지 않는다.
- `DEMO_LOGIN` 값 해석: `true`/`TRUE`만 켜짐, `1`·`yes`·빈 값·미설정은 꺼짐.
- `build_server_config`가 호스트 `127.0.0.1`이고 호스트를 바꿀 인자가 없다(소스·시그니처 검사).
- `startup_checks` 중 하나가 예외를 내면 앱 시작(`with TestClient(app):`)이 실패한다. `verify_contract`: 가짜 A(스냅샷과 같은 계약)면 통과, 스냅샷을 바꾼 가짜 A면 `StartupError`, 토큰 파일 없음도 `StartupError`이며 메시지에 토큰·헤더가 없다.
- `load_users`: 실제 `data/seed/users.json`에서 38명을 읽는다(없으면 예외 테스트는 임시 파일로).
- 계층 가드 통과(`api`는 broker·mocks·LLM SDK를 import하지 않는다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `POST /sessions`가 플래그와 루프백일 때만 열리고, 서버가 127.0.0.1에만 바인드되는가?
   - 서버가 토큰 해시만 보관하고 평문이 로그인 응답 외에 어디에도 없는가?
   - 시작 시 계약 불일치면 서버가 뜨지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 호스트 바인드 옵션(`--host`)을 만들지 마라. 이유: 데모 로그인은 127.0.0.1에만 바인드한다(AGENTS.md CRITICAL).
- 평문 세션 토큰을 저장·로깅하지 마라. 이유: 서버는 해시만 둔다.
- 세션 만료 정책·쿠키·CSRF를 확정하지 마라. 이유: TTL은 임시값이고 쿠키 속성·CSRF·세션 만료 시간은 M5에서 정한다.
- 요청 본문·쿼리·헤더에서 사용자 신원을 받지 마라(로그인 시 `user` 선택 외). 이유: 이후 모든 신원은 세션에서만 정한다.
- 승인 엔드포인트·SSE를 만들지 마라. 이유: M3·M5 범위다.
- `api` 코드에서 `broker`·`mocks`·LLM SDK를 import하지 마라. 이유: 계층 경계다.
- docker, 설치·네트워크 명령을 실행하지 마라. 실제 서버를 외부 인터페이스에 바인드하는 테스트를 만들지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

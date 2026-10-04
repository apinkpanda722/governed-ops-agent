# Step 1: mock-jira

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `mocks` 행, "고정 제약", "아키텍처 규칙")
- `/docs/ARCHITECTURE.md` ("도구와 부작용·멱등성" 전체, "자격 증명 중개 흐름", "디렉토리 구조"의 `mocks/`·`data/`)
- `/docs/ADR.md` (ADR-005, ADR-006)
- 기존 코드: `/src/govops/mocks/service.py`, `/src/govops/mocks/__main__.py`, `/src/govops/mocks/admin.py`, `/tests/test_mocks.py`, `/tests/test_architecture.py`, `/tests/test_compose.py`
- `/infra/docker-compose.yml`, `/infra/mocks/Dockerfile`, `/.dockerignore`
- 참고(읽기 전용, A 저장소): `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/src/kodocs/mcp_server/tokens.py`(토큰 해시 저장 방식), `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/src/kodocs/retrieval/access.py`(문서 권한 규칙 `can_read_doc`)

## 작업

mock-jira의 **업무 REST API, 사용자 인증, 시드 로더, 관리 상태·초기화**를 구현한다. 장애 주입(지연·5xx·429·응답 유실)은 M3이므로 만들지 않는다. TDD로 테스트를 먼저 쓴다.

### 설계 결정 (확정)

- **사용자 신원은 mock이 토큰에서 정한다.** 요청 본문·쿼리에 사용자·팀·직급이 없다. mock은 `Authorization: Bearer <토큰>`의 SHA-256을 해시 매니페스트와 대조해 `(label, team, grade)`를 얻는다. `label`은 `demo-<team>-g<grade>`이고 `team`은 A의 팀 ID(`platform`, `payments`, `data-ai`, `sre`, `security`, `hr`, `finance`, `legal`)다.
- **토큰 파일 형식**
  - 사용자 목록 `data/seed/users.json`: `{"users": [{"label", "team", "grade"}...]}` (step 3의 추출 스크립트가 만든다. 이 step의 테스트는 임시 파일을 쓴다).
  - 평문 토큰 `data/interim/mock/tokens.json` (0600, 커밋 안 함): `{"users": [{"label", "team", "grade", "tokens": {"mock-jira": <값>, "mock-slack": <값>}}...]}`. 사용자 × audience마다 다른 토큰이다.
  - 해시 매니페스트(커밋 대상, 평문 없음): `data/seed/mock_jira_token_hashes.json`, `data/seed/mock_slack_token_hashes.json`. 각각 `{"users": [{"label", "team", "grade", "token_sha256"}...]}`.
- **mock-jira는 서버 측 중복 제거를 하지 않는다**(AGENTS.md·ADR-006). 같은 POST를 두 번 보내면 두 건이 생긴다.
- 권한 규칙은 A와 같다. `access_level`이 `public`이면 모두 읽기 가능, `team`이면 사용자의 team이 `access_teams`에 있어야 하고, `restricted`이면 team이 포함되고 `grade >= access_min_grade`여야 한다.
- 읽기 권한 거부는 존재하지 않는 티켓과 **바이트 단위로 같은 404 응답**이다(존재 자체가 기밀). 쓰기(코멘트·후속 티켓)도 읽을 수 없는 티켓에는 같은 404다(읽을 수 없는 티켓의 존재를 알려 주지 않는다. jira에는 읽기와 별개인 쓰기 권한 구분을 두지 않는다).

### 만들 파일

1. **`src/govops/mocks/auth.py`**
   ```python
   @dataclass(frozen=True)
   class MockUser: label: str; team: str; grade: int
   class TokenHashStore:
       @classmethod
       def load(cls, path: Path) -> "TokenHashStore"   # 형식·중복·sha256 hex 검증
       def authenticate(self, token: str) -> MockUser | None   # hmac.compare_digest, 평문 비교·저장 없음
   def bearer_user(request) -> MockUser | None   # 헤더 파싱 (Bearer 한 개만 허용)
   ```
2. **`src/govops/mocks/tokens.py`** (`python -m govops.mocks.tokens`): `users.json`을 읽어 사용자 × audience(`mock-jira`, `mock-slack`) 토큰을 `secrets.token_urlsafe(32)`로 만들고 평문 파일(0600)과 audience별 해시 매니페스트를 쓴다. 기존 출력이 있으면 `--rotate` 없이는 거부한다. 콘솔에는 개수와 label만 출력하고 토큰 값은 절대 출력하지 않는다. 경로 옵션: `--users`, `--plaintext`, `--hash-dir` (기본값은 위 경로).
3. **`src/govops/mocks/seed.py`**
   ```python
   TICKET_FIELDS  # 필수 필드 이름 집합
   def load_tickets(path: Path) -> list[dict]
   ```
   `data/seed/tickets.json` 형식: `{"tickets": [{"ticket_id", "project_key", "owner_team", "title", "ticket_type", "status", "priority", "service", "incident_id"(null 가능), "reporter_id", "assignee_id", "created_at", "resolved_at"(null 가능), "story_points"(null 가능), "access_level", "access_teams"(문자열 리스트, public이면 빈 리스트), "access_min_grade"}...]}`. 추가 필드는 무시한다. 필수 필드 누락·ticket_id 중복·알 수 없는 `access_level`이면 오류. step 3의 추출 스크립트가 이 형식으로 파일을 만든다.
4. **`src/govops/mocks/jira.py`**: mock-jira 상태와 REST 앱. `service.py`의 `create_rest_app("jira", ...)`가 이것을 쓰도록 연결한다(기존 `/healthz` 테스트와 `create_rest_app(service)` 호출은 계속 통과해야 한다. 상태를 주지 않는 호출은 healthz만 있는 골격으로 둬도 된다). `/healthz`는 인증 없이 열고 나머지는 Bearer 인증을 요구한다(없거나 틀리면 401).
   - `GET /tickets/{ticket_id}` → 200 티켓. 응답 필드: `ticket_id, project_key, owner_team, title, ticket_type, status, priority, service, incident_id, reporter_id, assignee_id, created_at, resolved_at, story_points, parent_id, metadata`. `access_*` 필드는 응답에 넣지 않는다. 읽을 수 없거나 없으면 404 `{"error": {"code": "not_found", "message": ...}}`(두 경우 응답이 동일).
   - `GET /tickets?project=&team=&status=&q=&limit=` → 읽을 수 있는 티켓만. `q`는 제목 부분 문자열, `limit` 기본 20·최대 50. `{"tickets": [...]}`. id 오름차순.
   - `GET /tickets/{id}/comments` → `{"comments": [...]}` (읽을 수 없으면 404).
   - `POST /tickets/{id}/comments` 본문 `{"body": str, "metadata": {str: str}}` → 201 `{"comment": {"comment_id", "ticket_id", "author", "body", "metadata", "created_at"}}`. `author`는 토큰이 정한 label이다.
   - `GET /tickets/{id}/children` → 후속 티켓 목록 `{"tickets": [...]}`.
   - `POST /tickets/{id}/children` 본문 `{"title": str, "description": str|null, "priority": str|null, "metadata": {str: str}}` → 201 후속 티켓. 새 ID는 부모의 `project_key` + 해당 프로젝트 최대 번호 + 1, `owner_team`·`access_*`는 부모를 따르고 `parent_id`는 부모 ID다. 원 티켓이 없거나 읽을 수 없으면 404. 본문에 원 티켓 없이 독립 티켓을 만드는 엔드포인트는 만들지 않는다.
   - `metadata`는 문자열 키·값 딕셔너리이고 코멘트·후속 티켓에 그대로 저장되어 읽기 응답에 돌아온다(표식은 본문이 아니라 여기에 둔다).
   - 시간·ID 생성은 주입 가능한 시계·카운터로 하여 테스트가 결정론이 되게 한다.
   - 요청 로그에 Authorization 헤더나 토큰을 남기지 않는다.
5. **관리 앱과 CLI**: `create_admin_app("jira", ...)`에 `GET /state`(티켓 수, 생성된 코멘트·후속 티켓 목록. 본문 원문 대신 ID·작성자·메타데이터·본문 해시)와 `POST /reset`(시드 상태로 복원)을 추가한다. `admin.py`에 서브커맨드 `state`, `reset`을 추가한다(`health`는 유지, `transport` 주입 방식 유지).
6. **`src/govops/mocks/__main__.py`**: `jira` 실행 시 환경 변수 `MOCK_JIRA_TOKEN_HASHES`(기본 `data/seed/mock_jira_token_hashes.json`), `MOCK_JIRA_TICKETS`(기본 `data/seed/tickets.json`)에서 로드한다. 파일이 없거나 형식이 틀리면 **인증 없이 뜨지 않고** 명확한 메시지로 종료 코드 1로 끝난다. `slack`은 이 step에서 기존 동작을 유지한다.
7. **`infra/docker-compose.yml`**: `mock-jira`에 `../data/seed:/app/data/seed:ro` 볼륨을 추가한다(환경 변수 치환 `${...}`는 쓰지 않는다). 포트 정책(REST만 127.0.0.1에 publish, 9000 비공개)은 그대로 둔다. `tests/test_compose.py`에 읽기 전용 마운트 검사를 추가한다.

### 테스트 (`tests/test_mock_jira.py`, `tests/test_mock_auth.py`, 필요 시 `tests/test_mocks.py` 확장)

- `TestClient`와 `MockTransport`만 쓴다. 토큰·시드는 `tmp_path`에서 `tokens.py`/직접 생성한 것을 쓴다.
- 인증: 헤더 없음·잘못된 토큰·Bearer 두 개 → 401, 올바른 토큰 → 해당 사용자 권한.
- 권한 표: `public` / `team`(같은 팀·다른 팀) / `restricted`(같은 팀 등급 미달·경계(`grade == min`)·충족·다른 팀)를 조합한 표 기반 테스트. 읽을 수 없는 티켓과 없는 티켓의 응답 상태·본문 바이트가 같다.
- 같은 `POST /comments`를 두 번 보내면 코멘트가 두 건이다. 같은 `POST /children`도 두 건이다(서버 측 중복 제거 없음을 고정).
- `metadata`가 저장되고 `GET`으로 돌아온다. 후속 티켓의 `parent_id`·ID 규칙. 읽을 수 없는 부모에는 404.
- 검색: 읽을 수 있는 티켓만 나오고 필터·`limit` 상한이 동작한다.
- 토큰 생성기: 해시 매니페스트에 평문 토큰이 없다(파일 전체에서 어떤 평문 토큰 문자열도 검색되지 않는다), 평문 파일 모드 0600, `--rotate` 없이 재실행하면 거부, 콘솔 출력(`capsys`)에 토큰 값이 없다.
- 관리: `GET /state`·`POST /reset` 동작, `admin.main(["state"|"reset"], transport=MockTransport(...))`.
- `__main__`의 시작 실패 경로(파일 없음 → 종료 코드 1).
- 기존 `tests/test_architecture.py`가 계속 통과한다(`govops.mocks`는 다른 govops 계층을 import하지 않는다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 읽을 수 없는 티켓과 없는 티켓의 응답이 구별되지 않는가?
   - 서버 측 중복 제거가 없는가? 장애 주입 코드가 없는가?
   - 관리 포트가 compose에서 publish되지 않았는가? 토큰 평문이 어디에도 커밋 대상으로 남지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 요청 본문·쿼리·응답에 사용자·팀·직급 입력 필드를 만들지 마라. 이유: 신원은 토큰에서만 정한다(AGENTS.md CRITICAL).
- 서버 측 멱등 키·중복 제거를 넣지 마라. 이유: 멱등성은 B가 outbox와 표식으로 보장한다는 전제를 시험하는 mock이다(ADR-006).
- 장애 주입(지연·5xx·429·응답 유실)을 구현하지 마라. 이유: M3 범위다.
- 관리 포트(9000)를 `ports`로 publish하지 마라. 이유: 관리 엔드포인트는 compose 내부 전용이다.
- 토큰 값을 로그·콘솔·테스트 출력·커밋 대상 파일에 남기지 마라.
- `Makefile`을 수정하지 마라. 이유: make 타깃은 step 8에서 한꺼번에 추가한다.
- `data/seed/tickets.json`·`users.json` 등 시드 데이터 파일을 커밋 대상으로 만들지 마라. 이유: 사용자가 step 3의 스크립트로 추출한다. 테스트용 데이터는 `tmp_path`나 `tests/fixtures/`에 둔다.
- docker, 설치·네트워크 명령을 실행하지 마라. 이유: 설치와 기동은 사용자가 한다. 필요한 패키지가 없으면 `blocked`로 보고한다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- 실제 포트에 서버를 바인드하는 테스트를 만들지 마라. 이유: 이 step은 `TestClient`와 `MockTransport`만 쓴다.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

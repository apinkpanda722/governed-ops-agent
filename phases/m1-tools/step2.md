# Step 2: mock-slack

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `mocks` 행, 아키텍처 규칙의 "부작용 도구"·"읽기 권한 거부"·"mock 관리 엔드포인트")
- `/docs/ARCHITECTURE.md` ("도구와 부작용·멱등성" 전체)
- `/docs/ADR.md` (ADR-006)
- step 1 산출물: `/src/govops/mocks/auth.py`, `/src/govops/mocks/tokens.py`, `/src/govops/mocks/jira.py`, `/src/govops/mocks/service.py`, `/src/govops/mocks/__main__.py`, `/src/govops/mocks/admin.py`, `/tests/test_mock_jira.py`, `/tests/test_mock_auth.py`, `/infra/docker-compose.yml`

step 1에서 만든 인증·토큰 구조를 그대로 재사용하라. 새 인증 방식을 만들지 마라.

## 작업

mock-slack의 **채널·메시지 REST API와 관리 상태·초기화**를 구현한다. 장애 주입은 M3이다. TDD로 테스트를 먼저 쓴다.

### 설계 결정 (확정)

- 채널은 9개다: 팀 채널 8개 `team-<team>`(`team-platform`, `team-payments`, `team-data-ai`, `team-sre`, `team-security`, `team-hr`, `team-finance`, `team-legal`)와 전사 공지 `announce`. 채널 **목록은 공개 정보**다(인증된 모든 사용자에게 같은 목록).
- **발송은 자기 팀 채널과 `announce`만** 허용한다. 그 밖의 채널은 명시적 403 `{"error": {"code": "forbidden", "message": "권한 없음"}}`(쓰기 권한 거부는 명시적이다). 존재하지 않는 채널은 404.
- **채널 이력 조회도 자기 팀 채널과 `announce`만** 허용하고 그 밖은 같은 403이다. 이 이력 조회는 **에이전트 도구가 아니라 도구 계층의 표식 조회(재시도 전 중복 확인, M3)용**이다. 응답 문서·코드 주석에 이 용도를 명시한다.
- **서버 측 중복 제거는 없다**. 같은 메시지를 두 번 보내면 두 건이다.
- 표식은 메시지 본문이 아니라 `metadata`(문자열 키·값 딕셔너리)에 둔다. 저장해서 이력 조회 응답에 그대로 돌려준다.
- 인증은 step 1과 같다: audience `mock-slack`의 해시 매니페스트(`data/seed/mock_slack_token_hashes.json`, 환경 변수 `MOCK_SLACK_TOKEN_HASHES`로 경로 재정의)에서 `(label, team, grade)`를 얻는다. `/healthz`만 인증 없이 열린다.

### 만들 파일

1. **`src/govops/mocks/slack.py`**: mock-slack 상태와 REST 앱. `service.py`의 `create_rest_app("slack", ...)`·`create_admin_app("slack", ...)`가 이것을 쓰도록 연결한다(기존 healthz 테스트 호환 유지).
   - `GET /channels` → `{"channels": [{"channel_id", "kind": "team"|"announce", "team": <팀 ID>|null}...]}` 9개.
   - `POST /channels/{channel_id}/messages` 본문 `{"text": str, "metadata": {str: str}}` → 201 `{"message": {"message_id", "channel_id", "author", "text", "metadata", "created_at"}}`. `author`는 토큰이 정한 label이다.
   - `GET /channels/{channel_id}/messages?limit=` → 최신순 `{"messages": [...]}`, `limit` 기본 50·최대 100.
   - 시계·ID 생성은 주입 가능하게 하여 테스트가 결정론이 되게 한다. 요청 로그에 Authorization 헤더나 토큰을 남기지 않는다.
2. **관리 앱과 CLI**: `GET /state`(채널별 메시지 수와 메시지 ID·작성자·메타데이터·본문 해시), `POST /reset`(빈 상태로 복원). `admin.py`의 `state`·`reset` 서브커맨드가 `jira`와 `slack` 관리 앱 모두에 동작하도록 한다(서비스별 응답 형식이 달라도 CLI는 JSON을 그대로 출력).
3. **`src/govops/mocks/__main__.py`**: `slack` 실행 시 해시 매니페스트를 로드하고, 없거나 틀리면 인증 없이 뜨지 않고 종료 코드 1로 끝난다.
4. **`infra/docker-compose.yml`**: `mock-slack`에 `../data/seed:/app/data/seed:ro` 볼륨을 추가한다. 포트 정책은 그대로. `tests/test_compose.py`에 검사를 추가한다.

### 테스트 (`tests/test_mock_slack.py`)

- `TestClient`와 `MockTransport`만 쓴다. 토큰은 `tmp_path`에서 step 1의 `tokens.py`로 만든다.
- 인증: 없음·틀림 → 401.
- 채널 목록이 사용자와 무관하게 9개로 같다.
- 발송 권한 표: 사용자 team별로 자기 팀 채널·`announce`는 201, 다른 팀 채널은 403(본문 `권한 없음`), 없는 채널은 404.
- 이력 조회 권한 표: 자기 팀 채널·`announce`는 200, 다른 팀 채널은 403, 없는 채널은 404.
- 같은 메시지를 두 번 보내면 두 건이고, `metadata`가 이력에 돌아온다. 최신순과 `limit` 상한.
- 관리 `state`·`reset`, CLI 호출. `__main__` 시작 실패 경로.
- 계층 가드(`tests/test_architecture.py`)가 계속 통과한다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 쓰기·이력 권한 거부가 명시적 403이고, 읽기 거부 규칙(jira의 404)과 혼동되지 않는가?
   - 서버 측 중복 제거·장애 주입이 없는가? 관리 포트가 publish되지 않았는가?
   - 채널 이력 조회가 "에이전트 도구가 아니라 표식 조회용"이라고 문서화되었는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 서버 측 중복 제거를 넣지 마라. 이유: ADR-006, 멱등성은 B가 보장한다.
- 장애 주입(지연·5xx·429·응답 유실)을 구현하지 마라. 이유: M3 범위다.
- 요청 본문에 사용자·팀 필드를 만들지 마라. 이유: 신원은 토큰에서만 정한다.
- 토큰 값을 로그·콘솔·테스트 출력에 남기지 마라.
- `Makefile`과 시드 데이터 파일을 만들거나 수정하지 마라. 이유: make 타깃은 step 8, 시드는 사용자가 step 3의 스크립트로 만든다.
- 관리 포트(9000)를 `ports`로 publish하지 마라.
- docker, 설치·네트워크 명령을 실행하지 마라. 필요한 패키지가 없으면 `blocked`로 보고한다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- 실제 포트에 서버를 바인드하는 테스트를 만들지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

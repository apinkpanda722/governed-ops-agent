# Step 8: smoke-tools

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` ("고정 제약", "명령어", 개발 프로세스. 특히 `make verify`가 유일한 검증 진입점이고 실제 API·docker는 하네스에서 실행하지 않는다는 규칙)
- `/docs/ARCHITECTURE.md` ("A 연동 모드", "도구와 부작용·멱등성"의 mock 권한 항목, "배치도"의 Host 헤더 설명)
- `/docs/ADR.md` (ADR-004의 "확인 필요": 컨테이너에서 A의 Host 헤더 검사)
- `/docs/PRD.md` (M1 행, M7 데모 장면 2: 재무팀이 같은 요청 → 찾을 수 없음)
- `/Makefile`, `/tests/test_makefile.py`
- step 0~7 산출물: `/src/govops/tools/contract.py`, `/src/govops/tools/a_mcp.py`, `/src/govops/tools/jira.py`, `/src/govops/tools/slack.py`, `/src/govops/tools/registry.py`, `/src/govops/tools/record_a.py`, `/src/govops/broker/credentials.py`, `/src/govops/mocks/tokens.py`, `/src/govops/mocks/seed_extract.py`, `/src/govops/replay/tokens.py`, `/tests/conftest.py`, `/tests/test_tool_runtime.py`
- 참고(읽기 전용, A 저장소): `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/src/kodocs/retrieval/access.py`(권한 규칙), `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/src/kodocs/retrieval/sql.py`(A의 `run_sql`은 `tickets` 테이블에 사용자 권한 필터를 같은 방식으로 적용한다)

## 작업

사용자가 live 환경에서 실행할 **스모크 명령(make 타깃)** 과 그 로직을 만든다. 기대값은 **실행 전에 코드에 고정**한다. 하네스와 `make verify`는 이 명령들을 실행하지 않고, 로직은 가짜 A·in-process mock으로 단위 테스트한다. TDD로 테스트를 먼저 쓴다.

### 만들 파일: `src/govops/tools/smoke.py` (`python -m govops.tools.smoke <명령>`)

```python
async def smoke_contract(client: AMcpClient) -> SmokeResult
async def smoke_identity(a_client, jira, tickets: list[dict]) -> SmokeResult
async def smoke_visibility(a_client, jira, tickets: list[dict], users: list[dict]) -> SmokeResult
async def smoke_mock(jira, slack, tickets: list[dict]) -> SmokeResult
async def smoke_host_header(url: str, *, user_id: str, host_header: str = "host.docker.internal:8765") -> SmokeResult
@dataclass(frozen=True) class SmokeResult: ok: bool; lines: list[str]    # 사람이 읽는 결과 줄. 토큰·헤더 값 없음
def main(argv: list[str] | None = None) -> int     # 모든 smoke가 ok면 0, 하나라도 어긋나면 1
```
네트워크 대상은 환경 변수로 받는다: `KODOCS_MCP_URL`(A), `MOCK_JIRA_URL`(기본 `http://127.0.0.1:8081`), `MOCK_SLACK_URL`(기본 `http://127.0.0.1:8082`). 시드는 `data/seed/tickets.json`·`users.json`을 읽는다.

**기대값은 아래대로 코드에 고정한다. 결과를 본 뒤 바꾸지 않는다. 어긋나면 `ok=False`로 실패 보고하고 어긋난 항목을 출력한다(기대값을 맞춰 고치는 동작을 만들지 마라).**

1. `smoke_contract`: live `tools/list`가 `data/contract` 스냅샷과 같다. 출력: `계약 일치`, A 기준 커밋(manifest), 스냅샷 해시(`snapshot_sha256`). 불일치면 차이 목록과 실패.
2. `smoke_identity` — **문서 ID 단위 기대값**: `lookup_id(kind="incident", value="INC-2026-012")`를 `demo-platform-g3`와 `demo-finance-g3`로 호출한다. 기대값은 상수 `EXPECTED_DOC = "syn-incident-022"`: platform 결과의 문서 ID에 포함되고 finance 결과에는 포함되지 않는다. 추가로 **티켓 기대값**: `tickets.json`에서 `access_level == "team"`이고 `access_teams == ["platform"]`인 티켓 중 ID 정렬 첫 번째를 고른다(없으면 `restricted`에서 같은 조건으로). 기대 가시성은 **mock·A와 독립인 순수 함수 `expected_visible(user, ticket)`**(A 규칙: public 누구나, team은 팀 포함, restricted는 팀 포함 + `grade >= access_min_grade`)로 `tickets.json`의 권한 필드에서 계산한다. `jira_get_ticket`로 platform g3는 보이고 finance g3는 보이지 않는지(404)를 이 기대값과 비교한다. 티켓 ID를 코드에 하드코딩하지 마라(예: PLT-1004는 시드에서 public이다).
3. `smoke_visibility`: **38명 × 고정 표본 40건**. 표본은 결정론이다: `tickets.json`을 ID로 정렬한 뒤 층(`access_level`, `owner_team`)별로 public은 팀당 1건, team은 팀당 2건, restricted는 팀당 최대 2건을 앞에서부터 고르고, 40건이 안 되면 상수 시드 `20261004`의 `random.Random`으로 나머지에서 채우고 40건을 넘으면 앞에서 40건. 표본 ID 목록의 SHA-256을 출력한다. 각 (사용자, 티켓)에 대해 세 값을 비교한다: ① 순수 함수 `expected_visible` ② mock-jira(`jira_get_ticket`) 가시성 ③ A 가시성 — 사용자마다 A의 `run_sql`로 `SELECT ticket_id FROM tickets WHERE ticket_id IN (<표본 ID들>)`를 한 번 호출해 반환된 ID 집합. 세 값이 모두 같아야 한다. 출력: 사용자 수·티켓 수·비교 건수·불일치 건수와 불일치 목록(사용자 label, ticket_id, 어느 쪽이 다른지). 기대값: **불일치 0건**.
4. `smoke_mock`: mock만 확인한다(compose 기동 후). `demo-platform-g3`·`demo-finance-g3` 각각으로 jira `get`·`search`, slack `list_channels`(9개), 자기 팀 채널 이력(성공), 타 팀 채널 이력(403)을 확인하고, 잘못된 토큰이 401인지 확인한다. 기대값은 코드 상수다.
5. `smoke_host_header`: ADR-004의 "컨테이너에서 A의 Host 헤더 검사" 실측용. **사전 가설: 위조한 Host 헤더를 가진 인증된 요청을 A가 거절한다(421 또는 403)**. 동작: `KODOCS_MCP_URL`(`127.0.0.1`)로 `tools/list` JSON-RPC 요청을 보내되 `Host` 헤더를 `host_header`로 바꾸고, **Authorization 토큰은 `broker.credential_for(user_id, "kodocs-mcp")`로 가져온다**(인증 없는 요청은 A의 인증 미들웨어가 Host 검사보다 먼저 401을 내므로 판정에 쓸 수 없다). 출력은 상태 코드와 가설 대비 판정 문구(`거절됨(가설 일치)` / `수락됨(가설과 다름)`)뿐이다. 판정 불가(연결 실패)는 실패다. 컨테이너에서의 도달 가능성은 이 스모크가 아니라 사용자 작업(안내 문구를 출력)으로 확인한다: `docker compose -f infra/docker-compose.yml exec mock-jira python -c "import httpx;print(httpx.post('http://host.docker.internal:8765/mcp').status_code)"`를 출력 끝에 안내로 적는다(401이면 도달 가능, 연결 오류면 도달 불가).
   - **출력·로그·예외에 토큰이 없다**: 요청 헤더 값, `httpx` 디버그 로그, 예외 메시지 어디에도 토큰을 쓰지 않는다.

### Makefile 타깃 추가 (`verify` 타깃과 `.PHONY: verify`는 그대로 두고 새 타깃을 추가)

모든 live 타깃은 `uv run --env-file .env`로 실행한다(`.env`를 사람·에이전트가 열지 않고 프로세스 환경으로만 전달). `verify`에서 이 타깃들을 호출하지 않는다.
- `mock-tokens`: `uv run python -m govops.mocks.tokens`
- `seed-extract`: `uv run python -m govops.mocks.seed_extract --a-root $(A_ROOT)`, `A_ROOT ?= $(HOME)/PycharmProjects/ko-docs-agentic-rag`
- `record-a`: `uv run --env-file .env python -m govops.tools.record_a --requests data/replay/requests.json --out data/replay/a`
- `smoke-contract`, `smoke-identity`, `smoke-visibility`, `smoke-mock`, `smoke-host-header`: `uv run --env-file .env python -m govops.tools.smoke <명령>`
- `smoke-host-header`는 사용자를 `SMOKE_USER ?= demo-platform-g3`로 받는다.
- 각 타깃 위에 한 줄 주석으로 필요한 선행 조건(예: "live A 기동 필요")과 기대값을 적는다.

### 테스트

- `tests/test_smoke.py`는 이미 다른 용도로 있을 수 있다. 충돌하면 `tests/test_smoke_tools.py`로 만든다.
- 가짜 A(`serve_asgi`, 기록은 `tests/fixtures/a_recordings`에 `run_sql`의 `tickets` 조회 응답을 사용자별로 추가)와 in-process mock-jira(`ASGITransport`, 임시 시드·토큰)로 각 smoke의 **통과 경로**를 검증한다. 시드 사용자 일부(예: 3~4명)와 소규모 표본으로 줄여 돌린다.
- **불일치 감지**: mock이 특정 사용자에게 보이면 안 되는 티켓을 보여 주도록(또는 가짜 A 기록이 달라지도록) 만든 변형으로 `smoke_visibility`/`smoke_identity`가 `ok=False`와 불일치 목록을 낸다.
- 표본 선정이 결정론이다(두 번 호출해 같은 ID 목록·해시, 40건, 층 규칙 준수).
- `expected_visible`의 권한 표 테스트(경계 포함). 티켓 ID가 코드에 하드코딩되지 않았다(소스 검사).
- `smoke_host_header` 토큰 비노출: 센티널 토큰을 쓰는 임시 토큰 파일로 호출하고 `capsys`·`caplog`(DEBUG, `httpx`·`httpcore` 포함)·`SmokeResult.lines`·예외 문자열에 토큰이 없음을 확인한다. 가짜 서버가 421을 돌려주면 `거절됨`, 200이면 `수락됨` 판정이 나온다(가로채기: 서버가 받은 요청에서 `Host` 헤더가 위조 값이고 `Authorization`이 broker 토큰임을 확인).
- `tests/test_makefile.py` 확장: `verify` 레시피는 **기존 순서 그대로**이고 새 타깃이 위 이름으로 존재하며, `verify`가 새 타깃을 호출하지 않고, live 타깃들이 `--env-file .env`를 쓴다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 기대값(문서 ID, 가시성 규칙, 표본)이 실행 전 코드에 고정되고, 어긋나면 실패로 보고하는가?
   - 가시성 oracle이 mock·A와 독립인가?
   - `smoke_host_header`가 broker로 토큰을 받고 출력·로그에 토큰이 없는가?
   - `make verify`가 스모크 타깃을 호출하지 않고 네트워크·docker 없이 통과하는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 스모크 명령이나 make 스모크 타깃을 이 step에서 실제 A·mock·docker에 대해 실행하지 마라. 이유: live 실행은 사용자가 한다. 테스트는 가짜 A와 in-process mock만 쓴다.
- 어긋난 결과에 맞춰 기대값을 조정하거나 불일치를 무시하는 옵션(`--ignore`, `--update-expected` 등)을 만들지 마라. 이유: 기대값은 사전 등록이며 어긋나면 실패로 보고하고 원인을 조사한다.
- 티켓 ID·표본을 하드코딩하지 마라. 이유: 기대값은 시드의 권한 필드에서 계산한다.
- Host 헤더 스모크에서 인증 없는 요청으로 판정하지 마라. 이유: A는 인증 미들웨어가 먼저 돌아 401만 나오므로 Host 검사를 판정할 수 없다.
- 토큰·Authorization 값을 출력·로그에 남기지 마라.
- `verify` 타깃의 명령과 순서를 바꾸지 마라. 이유: `tests/test_makefile.py`가 순서를 고정한다.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

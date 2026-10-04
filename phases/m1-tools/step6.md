# Step 6: mcp-client

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `tools`·`broker`·`replay` 행, 아키텍처 규칙의 자격 증명·계약 스냅샷·기록 재생 항목)
- `/docs/ARCHITECTURE.md` ("A 연동 모드", "자격 증명 중개 흐름", "배치도")
- `/docs/ADR.md` (ADR-004, ADR-005)
- step 0 산출물: `/src/govops/tools/contract.py`, `/tests/test_contract.py`
- step 4 산출물: `/src/govops/broker/credentials.py`(`credential_for`, `known_users`), `/tests/test_broker.py`
- step 5 산출물: `/src/govops/replay/recordings.py`, `/src/govops/replay/fake_a.py`, `/tests/conftest.py`(`serve_asgi` 픽스처와 헬퍼), `/tests/fixtures/a_recordings/recordings.jsonl`, `/tests/test_replay_fake_a.py`
- 설치된 `mcp` SDK의 streamable HTTP 클라이언트 API(`.venv`의 `site-packages/mcp`, 읽기만)
- `/src/govops/tools/__init__.py`, `/tests/test_architecture.py`

## 작업

A MCP **클라이언트**와 **기록 CLI**를 `tools` 계층에 만든다. 클라이언트는 호출 직전에 `broker.credential_for`로 토큰을 받아 **그 요청의 헤더에만** 붙인다. TDD로 테스트를 먼저 쓴다.

### 만들 파일

1. **`src/govops/tools/a_mcp.py`**
   ```python
   @dataclass(frozen=True)
   class ToolResult:
       payload: dict      # structuredContent
       is_error: bool

   class AMcpError(Exception): ...   # 연결·프로토콜·인증(401) 실패. 메시지에 헤더·토큰을 넣지 않는다.

   class AMcpClient:
       def __init__(self, url: str, *, timeout_s: float = 30.0)
       async def list_tools(self, user_id: str) -> list[dict]
           # 항목: {"name","description","input_schema","output_schema","version"} 로 정규화
       async def call_tool(self, user_id: str, name: str, arguments: Mapping[str, Any]) -> ToolResult
   async def check_contract(client: AMcpClient, snapshot: Mapping[str, ContractTool] | None = None, *, user_id: str | None = None) -> None
       # live tools/list와 스냅샷을 비교해 다르면 ContractMismatchError(step 0).
       # user_id가 없으면 broker.known_users("kodocs-mcp")의 첫 label을 쓴다. 사용자가 없으면 CredentialSourceError 계열 오류.
   ```
   - 호출마다 새 연결(stateless)이다. `credential_for(user_id, "kodocs-mcp")`는 **요청을 보내기 직전에** 호출하고, 토큰을 인스턴스 속성·전역·캐시에 저장하지 않는다. 헤더 이름은 `Authorization`, 값은 `Bearer <토큰>`이다.
   - 도구 인자는 호출자가 준 대로 보낸다. **인자에 사용자·팀·직급을 추가하지 않는다**(도구 스키마에 신원 필드 없음). 신원은 토큰이 전달한다.
   - 기본 `timeout_s`는 임시 상수다(타임아웃·재시도·서킷 정책은 M3). 재시도하지 않는다.
   - 하위 라이브러리 예외를 `AMcpError`로 감싸되 메시지는 사유(연결 실패, 401, 프로토콜 오류)만 쓴다. `__repr__`에 토큰이 없다.
   - `broker`의 예외(`UnknownUserError` 등)는 네트워크 접속 **전에** 그대로 올라온다.
2. **`src/govops/tools/record_a.py`** (`python -m govops.tools.record_a --requests data/replay/requests.json --out data/replay/a [--url ...]`)
   - 요청 파일 `data/replay/requests.json`: `{"requests": [{"caller": <label>, "tool": <이름>, "arguments": {...}}...]}`. 이 step에서 **시작용 파일을 만든다**: 호출자 `demo-platform-g3`·`demo-finance-g3` 각각 `lookup_id`(incident `INC-2026-012`), `search_docs`(질의 한 개), `get_document`(`syn-incident-022`) — 총 6개. (사용자가 이후 항목을 추가한다.)
   - 동작: URL은 `--url` 또는 환경 변수 `KODOCS_MCP_URL`. 먼저 `check_contract`로 live 계약이 스냅샷과 같은지 확인하고 다르면 아무것도 쓰지 않고 중단한다. 요청마다 `AMcpClient.call_tool`로 live A를 호출하고 `Recording`을 만들어 `validate_recording`(step 5)에 넘긴다. **통과한 기록만** 저장한다.
   - **문서 결과에 `syn-*`가 아닌 `doc_id`가 하나라도 있는 기록은 그 기록 전체를 건너뛰고** 건너뛴 목록(caller, tool, 인자 키 앞 12자리)을 마지막에 보고한다. **응답을 편집하거나 일부만 저장하지 않는다.** `run_sql` rows는 합성 DB라 허용한다.
   - 이미 있는 기록 파일과는 (caller, tool, 인자 키) 기준으로 병합하고 새 응답이 기존을 덮어쓴다. 저장은 `write_recordings`로 결정론적으로.
   - 콘솔 출력: 저장 개수, 건너뜀 목록, A 계약 스냅샷 해시. 토큰·헤더는 출력하지 않는다.
   - `request` 파일의 호출자 label은 `broker`가 아는 사용자여야 한다.
   - 이 CLI는 `tools` 계층에 둔다(broker를 쓰기 때문). `govops.replay.recordings`를 import해도 된다.

### 테스트

- **실제 streamable HTTP 테스트** (`tests/test_a_mcp_client.py`): step 5의 `serve_asgi`로 가짜 A(기록은 `tests/fixtures/a_recordings`, 토큰은 임시 A 형식 파일)를 띄우고, `broker` 환경 변수(`KODOCS_MCP_TOKENS_FILE`)를 같은 파일로 가리킨 뒤 `AMcpClient`로 접속한다.
  - `list_tools`가 스냅샷과 일치하고 `check_contract`가 통과한다. 스냅샷 루트를 바꾼 가짜 A(또는 바꾼 스냅샷)로는 `ContractMismatchError`.
  - 같은 호출을 `demo-platform-g3`와 `demo-finance-g3`로 하면 **결과가 다르다**(platform은 `syn-incident-022` 포함, finance는 빈 결과).
  - 기록에 없는 호출은 `is_error=True`, `replay_miss`.
- **Authorization 헤더 가로채기** (같은 파일): 가짜 A의 ASGI 앱을 **테스트가 만든 ASGI 미들웨어로 한 번 감싸서** 서버가 받은 요청 헤더와 서버가 보낸 응답(상태·헤더·본문)을 기록한다(production 코드에 테스트용 훅을 넣지 마라). 확인 항목:
  1. 서버가 받은 모든 요청에 `Authorization: Bearer <그 사용자의 토큰>` 헤더가 정확히 하나 있다. 사용자별로 값이 다르다.
  2. 토큰 문자열(센티널)이 **서버 응답 본문·헤더**에 없다.
  3. 토큰 문자열이 `ToolResult`(payload·repr), `AMcpClient` 인스턴스의 `vars()`·`repr`, 반환된 모든 목록에 없다.
  4. 로거를 DEBUG로 올린(`httpx`, `httpcore`, `mcp` 포함) 상태에서도 `caplog` 전체 텍스트에 토큰이 없다.
  5. 잘못된 토큰으로 호출해 401이 나는 경우 `AMcpError`의 `str`·`repr`·`args`에 서버가 받은 토큰이 없다.
  6. 알 수 없는 사용자는 서버가 받은 요청 수가 0인 채로 `broker` 예외가 난다(접속 전 실패).
  7. `call_tool`에 넘긴 `arguments`가 서버가 받은 도구 인자와 정확히 같다(클라이언트가 인자를 덧붙이지 않는다).
- **기록 CLI 테스트** (`tests/test_record_a.py`): 가짜 A를 `serve_asgi`로 띄우되 **비합성 문서(`doc_id`가 `syn-`이 아님)를 돌려주는 요청 하나**가 포함된 기록 세트를 쓴다.
  - 정상 요청은 `recordings.jsonl`에 저장되고, 비합성 응답 기록은 **통째로 건너뛰며** 건너뜀 목록에 나온다(저장 파일에 그 응답의 어떤 조각도 없다).
  - 저장 파일에 `Authorization`·`Bearer `·테스트 토큰 문자열이 없다.
  - 저장한 파일로 새 가짜 A를 띄우면 같은 결과를 돌려준다(왕복).
  - 계약 불일치 시 아무것도 쓰지 않고 비정상 종료한다.
  - 콘솔 출력(`capsys`)에 토큰이 없다.
- `tests/test_architecture.py` 통과(`tools`는 agent·api·eval·mocks·langgraph·LLM SDK를 import하지 않는다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 토큰이 요청 직전에 `credential_for`로 받아져 헤더에만 붙고, 응답·상태·로그·예외에 없는가?
   - 도구 인자에 사용자·팀·직급이 없는가?
   - 기록 CLI가 비합성 응답을 통째로 건너뛰고 편집하지 않는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 토큰을 인스턴스 속성·전역·캐시에 두지 마라. 이유: 요청 직전에 받아 헤더에만 붙이는 것이 불변식이다(ADR-005).
- 테스트 가로채기를 위한 훅·플래그·디버그 출력을 production 코드에 넣지 마라. 이유: 검증은 테스트가 ASGI 미들웨어로 감싸서 한다.
- `record_a`를 이 step에서 실제 A에 대해 실행하지 마라. 이유: 기록은 사용자가 live A를 띄운 뒤 실행한다. 테스트는 가짜 A만 쓴다.
- 응답을 가공·부분 저장하지 마라. 이유: 기록은 원본이거나 건너뛰거나 둘 중 하나다.
- `data/replay/`에 기록 파일(`recordings.jsonl`)을 만들지 마라. 시작용 `requests.json`만 만든다.
- 재시도·서킷·타임아웃 정책을 구현하지 마라. 이유: M3 범위다.
- 테스트 소켓은 `127.0.0.1`의 임의 포트에만 바인드하라.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.

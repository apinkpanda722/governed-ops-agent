# Step 5: replay-fake-a

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `replay` 행, 아키텍처 규칙의 "기록 재생 파일" 항목, 고정 제약의 `syn-*`·실존 이름 항목)
- `/docs/ARCHITECTURE.md` ("A 연동 모드", "배치도"의 가짜 A 설명)
- `/docs/ADR.md` (ADR-004)
- step 0 산출물: `/src/govops/tools/contract.py`(스냅샷 로더 `load_snapshot`, `ContractTool`, `diff_tools_list`), `/tests/test_contract.py`, `/data/contract/*.v1.json`
- step 4 산출물: `/src/govops/broker/credentials.py`(A 토큰 파일 형식 이해용. **replay는 broker를 import할 수 없다**)
- 참고(읽기 전용, A 저장소): `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/src/kodocs/mcp_server/server.py` — A의 실제 MCP 서버 구현이다. 같은 방식(MCP SDK `Server`, `streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True, host="127.0.0.1")`, Bearer 인증 미들웨어, `tools/list`가 계약 도구를 돌려주는 구조)으로 가짜 A를 만든다.
- 설치된 `mcp` SDK의 API(`.venv`의 `site-packages/mcp`)를 읽어 정확한 클래스·함수 이름을 확인한다(읽기만).
- `/src/govops/replay/__init__.py`, `/tests/test_architecture.py`

## 작업

실제 MCP SDK 기반의 **가짜 A MCP 서버**와 **기록 파일 형식·검증**을 만든다. 기록한 A 응답을 돌려주는 서버로, CI와 외부 검토자의 replay 모드에서 쓴다. TDD로 테스트를 먼저 쓴다.

### 만들 파일

1. **`src/govops/replay/recordings.py`** — 기록 형식과 검증
   ```python
   @dataclass(frozen=True)
   class Recording:
       caller: str            # 호출 사용자 label (예: "demo-platform-g3")
       tool: str
       arguments: dict
       result: dict           # 도구 결과 payload (structuredContent)
       is_error: bool

   def arguments_key(arguments: Mapping[str, Any]) -> str      # 키 정렬·공백 없는 JSON(UTF-8)의 SHA-256 hex
   class RecordingSet:
       @classmethod
       def load(cls, directory: Path) -> "RecordingSet"        # 디렉토리의 recordings.jsonl (없으면 빈 집합)
       def lookup(self, caller: str, tool: str, arguments: Mapping[str, Any]) -> Recording | None
       def entries(self) -> list[Recording]
   def write_recordings(directory: Path, entries: Iterable[Recording]) -> None   # 정렬·결정론·임시 파일 후 교체
   class RecordingRejected(ValueError): ...
   def validate_recording(entry: Recording) -> None
   ```
   파일 형식은 JSONL 한 줄 = 기록 하나, 위 필드 그대로다(`recordings.jsonl`). 같은 (caller, tool, arguments_key)가 두 번 있으면 로드 오류. 쓸 때는 (caller, tool, arguments_key) 순으로 정렬한다.
   `validate_recording` 규칙(위반 시 `RecordingRejected`, 메시지에는 위반 종류와 caller·tool만 쓰고 본문은 넣지 않는다):
   - `result` 안의 **모든 깊이**의 `doc_id` 키 값이 문자열이고 `syn-`으로 시작해야 한다(합성 문서만). `run_sql`의 `rows`는 합성 DB 결과라 허용한다(`doc_id` 키 검사 대상이 아니다).
   - 어느 깊이에서든 키 이름이 `authorization`·`token`·`bearer`·`password`·`api_key`(대소문자 무시)인 항목이 있으면 거부한다. 문자열 값에 `Bearer ` 패턴이 있으면 거부한다.
2. **`src/govops/replay/fake_a.py`** — 가짜 A 서버
   ```python
   def create_app(*, recordings: RecordingSet, tokens_file: Path, contract_root: Path | None = None) -> Starlette
   ```
   - A의 서버와 같은 구조다. `tokens_file`은 A 형식 평문 토큰 파일(`{"users": [{"label","team","grade","token"}]}`)이고 서버가 시작 시 읽어 토큰의 SHA-256 → label 표를 만든다(평문은 보관하지 않는다). Bearer 인증은 A와 같다: 헤더가 정확히 하나, `Bearer <토큰>` 형식, 해시 비교(`hmac.compare_digest`), 실패하면 401과 `{"error":{"code":"unauthorized","message":...}}`.
   - `tools/list`: step 0의 `load_snapshot`으로 계약 4개를 돌려준다(`name`, `description`, `inputSchema`=스냅샷 input_schema, `outputSchema`=스냅샷 output_schema, `_meta={"version": N}`). 실제 A와 같은 계약 비교(`diff_tools_list`)를 통과해야 한다.
   - `tools/call`: 인증된 사용자 label과 `(tool, arguments)`로 `RecordingSet.lookup`한다. 있으면 `structuredContent`=기록의 `result`, 텍스트 콘텐츠=그 JSON, `isError`=기록의 `is_error`. **없으면** `isError=True`와 `{"error": {"code": "replay_miss", "message": "기록에 없는 호출입니다"}}`(실제 A의 오류 코드와 섞이지 않게 별도 코드).
   - 호출 사용자 label은 서버가 토큰에서 정한다. 요청 인자에서 사용자를 받지 않는다.
   - 응답·로그에 Authorization 헤더나 토큰을 넣지 않는다.
3. **`src/govops/replay/tokens.py`** (`python -m govops.replay.tokens --users data/seed/users.json --out data/interim/replay/tokens.json`): replay 모드 전용으로 사용자별 임의 토큰을 가진 A 형식 토큰 파일을 만든다(`secrets.token_urlsafe(32)`, 0600, 기존 파일은 `--rotate` 없이 덮어쓰지 않음, 콘솔에는 개수만). 실제 A 토큰과 무관하다.
4. **`src/govops/replay/__main__.py`** (`python -m govops.replay --port 8765 --tokens-file ... --recordings data/replay/a`): 가짜 A를 `127.0.0.1`에만 바인드해 uvicorn으로 띄운다.
5. **테스트 지원: `tests/conftest.py`** (이미 있으면 확장)
   - 픽스처 `serve_asgi`: ASGI 앱을 받아 **127.0.0.1의 임의 포트(포트 0으로 소켓을 먼저 바인드)** 에서 uvicorn을 백그라운드 스레드로 띄우고 기본 URL을 돌려주며, 테스트 종료 시 서버를 내린다. 이후 step의 클라이언트 테스트가 재사용한다.
   - 손으로 만든 기록 `tests/fixtures/a_recordings/recordings.jsonl`(합성 문서 `syn-*`만, 사용자 2명 `demo-platform-g3`·`demo-finance-g3`의 `lookup_id`(incident `INC-2026-012`)·`get_document`·`search_docs`·`run_sql` 응답. 같은 호출에 대해 platform은 문서 `syn-incident-022`가 있는 결과, finance는 `{"documents": [], "truncated": false}` 같은 빈 결과)와 헬퍼(임시 A 형식 토큰 파일을 만드는 함수, 센티널 토큰 반환).
   - 응답 출력 스키마는 `data/contract/lookup_id.v1.json` 등의 `output_schema`를 따라 만든다.

### 테스트

- `tests/test_replay_recordings.py`: 왕복(write → load) 결정론, 중복 키 거부, `validate_recording`의 모든 거부 규칙(비합성 `doc_id`, 중첩 `doc_id`, `authorization`·`token` 키, `Bearer ` 값)과 허용 규칙(`run_sql` rows, `syn-*`), 정렬.
- `tests/test_replay_fake_a.py` (**실제 streamable HTTP**): `serve_asgi`로 가짜 A를 띄우고 설치된 `mcp` SDK의 streamable HTTP 클라이언트(또는 `httpx`로 JSON-RPC POST)로 접속한다.
  - 인증: 헤더 없음·틀린 토큰·Bearer 두 개 → 401.
  - `tools/list` 결과가 `diff_tools_list(load_snapshot(), live)`로 **빈 차이**다(계약 일치).
  - 같은 호출을 platform 토큰과 finance 토큰으로 보내면 **결과가 다르다**(기록대로). 기록에 없는 호출은 `replay_miss` 오류다.
  - 응답 본문·응답 헤더에 요청에 쓴 토큰 문자열이 없다.
  - 스냅샷 루트를 수정한 임시 복사본을 `contract_root`로 주면 `diff_tools_list`가 불일치를 보고한다(가짜 A도 같은 계약 비교 대상임을 확인).
- `tests/test_replay_tokens.py`: 생성기 형식·0600·`--rotate` 없이 거부·콘솔에 토큰 없음.
- `tests/test_replay_data_rules.py`: 저장소의 `data/replay/` 아래 모든 `.jsonl`·`.json`이 `validate_recording` 규칙을 통과하고 `Authorization`·`Bearer ` 문자열을 포함하지 않는다(파일이 없으면 통과). `tests/fixtures/a_recordings/`도 같은 규칙을 통과한다.
- `tests/test_architecture.py`가 계속 통과한다(`replay`는 agent·api·broker를 import하지 않는다. `govops.tools.contract`는 import해도 된다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `replay`가 `broker`·`agent`·`api`를 import하지 않는가?
   - 기록 형식·검증이 합성 문서(`syn-*`)만 허용하고 토큰·Authorization을 거부하는가?
   - 가짜 A가 실제 계약 비교를 통과하고, 사용자 신원을 토큰에서만 정하는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단 (예: `mcp` 패키지가 설치되어 있지 않거나, 샌드박스가 127.0.0.1 소켓 바인드를 막는 경우)

## 금지사항

- `data/replay/`에 기록 파일을 만들지 마라. 이유: 실제 A 응답 기록은 사용자가 실행한다. 테스트용 기록은 `tests/fixtures/`에만 둔다.
- 테스트 소켓은 `127.0.0.1`의 임의 포트에만 바인드하라. `0.0.0.0`이나 외부 주소를 쓰지 마라. 이유: 로컬 전용이다.
- 실제 A 서버나 외부 네트워크에 접속하지 마라. 이유: 테스트는 기록 재생만 쓴다.
- 기록과 테스트 데이터에 실존 기관·사람 이름, 실제 규정 원문을 쓰지 마라. 이유: AGENTS.md CRITICAL. 문서 ID는 `syn-*`뿐이다.
- 가짜 A의 인증을 느슨하게 만들지 마라(예: 인증 생략 옵션). 이유: 인증 경로가 실제와 다르면 신원 위임 테스트가 의미가 없다.
- docker, 설치·네트워크 명령(`uv add`, `uv sync`, `pip`)을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
